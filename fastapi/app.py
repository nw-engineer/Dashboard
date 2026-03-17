#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Tera Term / shell raw log watcher
- raw ログを監視
- 一定単位で OpenAI Responses API に送る
- 行番号付き注釈を生成
- 注釈付きログを別ファイルに再生成

使い方:
  python3 app.py \
    --raw-log data/session_raw.log \
    --annotated-log data/session_annotated.log \
    --state-file data/session_state.json

環境変数:
  OPENAI_API_KEY   必須
  OPENAI_MODEL     任意 (既定: gpt-5)
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from openai import OpenAI


# ========= 設定既定値 =========

DEFAULT_MAX_LINES_PER_CHUNK = 100
DEFAULT_MAX_COMMANDS_PER_CHUNK = 5
DEFAULT_MAX_SECONDS_PER_CHUNK = 60
DEFAULT_POLL_INTERVAL = 5
DEFAULT_MODEL = os.environ.get("OPENAI_MODEL", "gpt-5")

# Tera Term / bash っぽいプロンプトをざっくり検出
# 例:
#   tadashi@docker001:~$
#   root@host:/var/log#
#   user-name@host-name:/path/to/dir$
PROMPT_RE = re.compile(
    r"^[A-Za-z0-9_.-]+@[A-Za-z0-9_.-]+:.*[$#]\s?(.*)?$"
)

ANSI_ESCAPE_RE = re.compile(r"\x1B\[[0-?]*[ -/]*[@-~]")
EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
IPV4_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
PASSWORD_RE = re.compile(r"(?i)(password\s*=\s*)\S+")
BEARER_RE = re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._\-]+")
AUTH_HEADER_RE = re.compile(r"(?i)(authorization:\s*bearer\s+)[A-Za-z0-9._\-]+")
TOKENISH_RE = re.compile(r"(?i)\b(api[_-]?key|token|secret)\s*[:=]\s*\S+")


# ========= OpenAI client =========

def build_openai_client() -> OpenAI:
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY が未設定です。")
    return OpenAI(api_key=api_key)


# ========= state =========

def default_state() -> Dict[str, Any]:
    return {
        "last_line": 0,
        "chunk_start_time": None,
        "annotations": {},      # { "line_no": "注釈" }
        "summary_by_line": {},  # { "first_line_no": "概要" }
        "chunk_summaries": [],  # 履歴
    }


def load_state(state_file: Path) -> Dict[str, Any]:
    if not state_file.exists():
        return default_state()
    try:
        data = json.loads(state_file.read_text(encoding="utf-8"))
        # 足りないキーを補完
        state = default_state()
        state.update(data)
        return state
    except Exception:
        return default_state()


def save_state(state_file: Path, state: Dict[str, Any]) -> None:
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text(
        json.dumps(state, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


# ========= utility =========

def strip_ansi(text: str) -> str:
    return ANSI_ESCAPE_RE.sub("", text)


def redact_sensitive(text: str) -> str:
    text = IPV4_RE.sub("<IP>", text)
    text = EMAIL_RE.sub("<EMAIL>", text)
    text = PASSWORD_RE.sub(r"\1<REDACTED>", text)
    text = BEARER_RE.sub(r"\1<REDACTED>", text)
    text = AUTH_HEADER_RE.sub(r"\1<REDACTED>", text)
    text = TOKENISH_RE.sub("<REDACTED>", text)
    return text


def normalize_line(text: str) -> str:
    text = strip_ansi(text)
    text = text.replace("\r", "")
    text = redact_sensitive(text)
    return text


def read_all_lines(raw_log: Path) -> List[str]:
    if not raw_log.exists():
        return []
    return raw_log.read_text(encoding="utf-8", errors="replace").splitlines()


def detect_prompt_line(line: str) -> bool:
    return bool(PROMPT_RE.match(line))


def is_probable_command_line(line: str) -> bool:
    # プロンプト行だけをコマンド候補とする
    return detect_prompt_line(line)


def extract_command_from_prompt_line(line: str) -> str:
    m = PROMPT_RE.match(line)
    if not m:
        return ""
    # 正規表現の最後の capture をコマンド扱い
    cmd = m.group(1) or ""
    return cmd.strip()


def last_n_summaries(state: Dict[str, Any], n: int = 3) -> List[str]:
    return state.get("chunk_summaries", [])[-n:]


# ========= chunking =========

def should_flush_by_time(state: Dict[str, Any], max_seconds: int) -> bool:
    started = state.get("chunk_start_time")
    if not started:
        return False
    return (time.time() - started) >= max_seconds


def build_chunk(
    new_lines: List[str],
    start_line_no: int,
    max_lines_per_chunk: int,
    max_commands_per_chunk: int,
) -> Dict[str, Any]:
    """
    new_lines[0] が raw 全体で start_line_no 行目に相当する。
    """
    chunk_lines: List[Dict[str, Any]] = []
    command_count = 0

    for i, raw_line in enumerate(new_lines):
        line_no = start_line_no + i
        clean = normalize_line(raw_line)

        chunk_lines.append({
            "line_no": line_no,
            "text": clean,
        })

        if is_probable_command_line(clean):
            command_count += 1

        if len(chunk_lines) >= max_lines_per_chunk:
            break
        if command_count >= max_commands_per_chunk:
            break

    return {
        "lines": chunk_lines,
        "command_count": command_count,
    }


# ========= prompt payload =========

def make_llm_input(chunk: Dict[str, Any], state: Dict[str, Any]) -> Dict[str, Any]:
    previous_summaries = last_n_summaries(state, n=3)

    # 長すぎると無駄なので必要最小限だけ
    compact_lines = []
    for item in chunk["lines"]:
        compact_lines.append({
            "line_index": item["line_no"],
            "text": item["text"],
        })

    return {
        "previous_summaries": previous_summaries,
        "chunk_lines": compact_lines,
        "requirements": {
            "goal": "作業ログを後から見て、何をしたか分かる注釈を付ける",
            "annotation_style": "短い1行、事実ベース、控えめ",
            "focus": [
                "何をしたか",
                "何を確認したか",
                "何を見ようとしていたか"
            ],
            "avoid": [
                "過剰な原因断定",
                "長すぎる解説",
                "証拠のない意図の決めつけ"
            ]
        }
    }


def openai_schema() -> Dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "chunk_summary": {
                "type": "string",
                "description": "このチャンク全体で何をしていたかの短い要約。1文。"
            },
            "annotations": {
                "type": "array",
                "description": "注釈が必要なコマンド行のみ返す",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "line_index": {
                            "type": "integer",
                            "description": "raw ログ全体の行番号"
                        },
                        "note": {
                            "type": "string",
                            "description": "1行の簡潔な注釈"
                        }
                    },
                    "required": ["line_index", "note"]
                }
            }
        },
        "required": ["chunk_summary", "annotations"]
    }


def call_llm(
    client: OpenAI,
    model: str,
    payload: Dict[str, Any],
) -> Dict[str, Any]:
    instructions = (
        "あなたは Linux / Tera Term の作業ログに短い注釈を付けるアシスタントです。"
        "目的は、後から読んだ人が『このタイミングで何をしたか』を理解できるようにすることです。"
        "必ず事実ベースで、短く、控えめに書いてください。"
        "過剰な推測は禁止です。"
        "分からない場合は『状態確認』『ログ確認』『絞り込み確認』のような控えめな表現にしてください。"
        "注釈は必要なコマンド行だけに付けてください。"
        "コマンド出力行には原則として注釈を付けないでください。"
        "出力は必ず JSON Schema に厳密準拠してください。"
    )

    response = client.responses.create(
        model=model,
        instructions=instructions,
        input=json.dumps(payload, ensure_ascii=False),
        temperature=0.1,
        max_output_tokens=1200,
        store=False,
        text={
            "format": {
                "type": "json_schema",
                "name": "scrapmemo_annotations",
                "strict": True,
                "schema": openai_schema(),
            }
        },
    )

    raw_text = response.output_text
    result = json.loads(raw_text)

    chunk_summary = str(result.get("chunk_summary", "")).strip() or "作業ログを確認"
    annotations = result.get("annotations", [])
    if not isinstance(annotations, list):
        annotations = []

    cleaned_annotations = []
    for item in annotations:
        try:
            line_index = int(item["line_index"])
            note = str(item["note"]).strip()
            if not note:
                continue
            cleaned_annotations.append({
                "line_index": line_index,
                "note": note,
            })
        except Exception:
            continue

    return {
        "chunk_summary": chunk_summary,
        "annotations": cleaned_annotations,
    }


# ========= merge / render =========

def merge_annotations(
    state: Dict[str, Any],
    llm_result: Dict[str, Any],
    chunk: Dict[str, Any],
) -> None:
    ann = state.get("annotations", {})
    for item in llm_result.get("annotations", []):
        ann[str(item["line_index"])] = item["note"]
    state["annotations"] = ann

    summary = llm_result.get("chunk_summary", "").strip()
    if summary and chunk["lines"]:
        first_line = chunk["lines"][0]["line_no"]
        summary_map = state.get("summary_by_line", {})
        summary_map[str(first_line)] = summary
        state["summary_by_line"] = summary_map
        state.setdefault("chunk_summaries", []).append(summary)


def regenerate_annotated_log(
    raw_lines: List[str],
    state: Dict[str, Any],
    annotated_log: Path,
) -> None:
    annotations = state.get("annotations", {})
    summary_by_line = state.get("summary_by_line", {})
    out_lines: List[str] = []

    for idx, line in enumerate(raw_lines, start=1):
        summary = summary_by_line.get(str(idx))
        if summary:
            out_lines.append("=== chunk summary ===")
            out_lines.append(f"概要: {summary}")

        out_lines.append(line)

        note = annotations.get(str(idx))
        if note:
            out_lines.append(f"注釈: {note}")

    annotated_log.parent.mkdir(parents=True, exist_ok=True)
    annotated_log.write_text("\n".join(out_lines) + ("\n" if out_lines else ""), encoding="utf-8")


# ========= core loop =========

def process_once(
    client: OpenAI,
    model: str,
    raw_log: Path,
    annotated_log: Path,
    state_file: Path,
    max_lines_per_chunk: int,
    max_commands_per_chunk: int,
    max_seconds_per_chunk: int,
    debug: bool = False,
) -> None:
    state = load_state(state_file)
    raw_lines = read_all_lines(raw_log)

    last_line = int(state.get("last_line", 0))

    # ログローテーション or truncate をざっくり検出
    if last_line > len(raw_lines):
        if debug:
            print("[INFO] raw log truncated or rotated; resetting state", file=sys.stderr)
        state = default_state()
        last_line = 0

    new_lines = raw_lines[last_line:]

    if not new_lines:
        regenerate_annotated_log(raw_lines, state, annotated_log)
        save_state(state_file, state)
        return

    if not state.get("chunk_start_time"):
        state["chunk_start_time"] = time.time()

    chunk = build_chunk(
        new_lines=new_lines,
        start_line_no=last_line + 1,
        max_lines_per_chunk=max_lines_per_chunk,
        max_commands_per_chunk=max_commands_per_chunk,
    )

    enough_by_size = (
        len(chunk["lines"]) >= max_lines_per_chunk
        or chunk["command_count"] >= max_commands_per_chunk
    )
    enough_by_time = should_flush_by_time(state, max_seconds=max_seconds_per_chunk)

    if enough_by_size or enough_by_time:
        payload = make_llm_input(chunk, state)

        if debug:
            print(
                f"[INFO] flush chunk: lines={len(chunk['lines'])}, "
                f"commands={chunk['command_count']}, "
                f"start_line={chunk['lines'][0]['line_no'] if chunk['lines'] else '-'}",
                file=sys.stderr,
            )

        llm_result = call_llm(client=client, model=model, payload=payload)
        merge_annotations(state, llm_result, chunk)

        consumed = len(chunk["lines"])
        state["last_line"] = last_line + consumed
        state["chunk_start_time"] = None

    regenerate_annotated_log(raw_lines, state, annotated_log)
    save_state(state_file, state)


# ========= CLI =========

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Annotate shell/Tera Term logs with LLM summaries.")
    parser.add_argument("--raw-log", required=True, help="生ログファイル")
    parser.add_argument("--annotated-log", required=True, help="注釈付きログ出力先")
    parser.add_argument("--state-file", required=True, help="進捗管理JSON")
    parser.add_argument("--model", default=DEFAULT_MODEL, help=f"OpenAI model (default: {DEFAULT_MODEL})")
    parser.add_argument("--max-lines", type=int, default=DEFAULT_MAX_LINES_PER_CHUNK, help="1チャンク最大行数")
    parser.add_argument("--max-commands", type=int, default=DEFAULT_MAX_COMMANDS_PER_CHUNK, help="1チャンク最大コマンド数")
    parser.add_argument("--max-seconds", type=int, default=DEFAULT_MAX_SECONDS_PER_CHUNK, help="チャンク確定までの最大秒数")
    parser.add_argument("--poll-interval", type=int, default=DEFAULT_POLL_INTERVAL, help="監視間隔秒")
    parser.add_argument("--once", action="store_true", help="1回だけ処理して終了")
    parser.add_argument("--debug", action="store_true", help="デバッグログを標準エラーへ出力")
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    raw_log = Path(args.raw_log)
    annotated_log = Path(args.annotated_log)
    state_file = Path(args.state_file)

    client = build_openai_client()

    raw_log.parent.mkdir(parents=True, exist_ok=True)
    annotated_log.parent.mkdir(parents=True, exist_ok=True)
    state_file.parent.mkdir(parents=True, exist_ok=True)

    if args.debug:
        print(f"[INFO] watching raw log: {raw_log}", file=sys.stderr)
        print(f"[INFO] annotated log: {annotated_log}", file=sys.stderr)
        print(f"[INFO] state file: {state_file}", file=sys.stderr)
        print(f"[INFO] model: {args.model}", file=sys.stderr)

    if args.once:
        process_once(
            client=client,
            model=args.model,
            raw_log=raw_log,
            annotated_log=annotated_log,
            state_file=state_file,
            max_lines_per_chunk=args.max_lines,
            max_commands_per_chunk=args.max_commands,
            max_seconds_per_chunk=args.max_seconds,
            debug=args.debug,
        )
        return

    while True:
        try:
            process_once(
                client=client,
                model=args.model,
                raw_log=raw_log,
                annotated_log=annotated_log,
                state_file=state_file,
                max_lines_per_chunk=args.max_lines,
                max_commands_per_chunk=args.max_commands,
                max_seconds_per_chunk=args.max_seconds,
                debug=args.debug,
            )
        except KeyboardInterrupt:
            if args.debug:
                print("[INFO] stopped by keyboard interrupt", file=sys.stderr)
            break
        except Exception as e:
            print(f"[ERROR] {e}", file=sys.stderr)

        time.sleep(args.poll_interval)


if __name__ == "__main__":
    main()