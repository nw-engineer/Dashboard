#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any, Dict, List

from openai import OpenAI


PROMPT_RE = re.compile(r"^[A-Za-z0-9_.-]+@[A-Za-z0-9_.-]+:.*[$#]\s?(.*)?$")
ANSI_ESCAPE_RE = re.compile(r"\x1B\[[0-?]*[ -/]*[@-~]")
EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
IPV4_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
PASSWORD_RE = re.compile(r"(?i)(password\s*=\s*)\S+")
BEARER_RE = re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._\-]+")
TOKENISH_RE = re.compile(r"(?i)\b(api[_-]?key|token|secret)\s*[:=]\s*\S+")


def build_client() -> OpenAI:
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY が未設定です。")
    return OpenAI(api_key=api_key)


def default_state() -> Dict[str, Any]:
    return {
        "last_line": 0,
        "chunk_start_time": None,
        "annotations": {},
        "summary_by_line": {},
        "chunk_summaries": [],
    }


def load_state(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return default_state()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        state = default_state()
        state.update(data)
        return state
    except Exception:
        return default_state()


def save_state(path: Path, state: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def strip_ansi(text: str) -> str:
    return ANSI_ESCAPE_RE.sub("", text)


def redact_sensitive(text: str) -> str:
    text = IPV4_RE.sub("<IP>", text)
    text = EMAIL_RE.sub("<EMAIL>", text)
    text = PASSWORD_RE.sub(r"\1<REDACTED>", text)
    text = BEARER_RE.sub(r"\1<REDACTED>", text)
    text = TOKENISH_RE.sub("<REDACTED>", text)
    return text


def normalize_line(text: str) -> str:
    text = strip_ansi(text)
    text = text.replace("\r", "")
    text = redact_sensitive(text)
    return text


def read_all_lines(path: Path) -> List[str]:
    if not path.exists():
        return []
    return path.read_text(encoding="utf-8", errors="replace").splitlines()


def detect_prompt_line(line: str) -> bool:
    return bool(PROMPT_RE.match(line))


def extract_command(line: str) -> str:
    m = PROMPT_RE.match(line)
    if not m:
        return ""
    return (m.group(1) or "").strip()


def should_flush_by_time(state: Dict[str, Any], max_seconds: int) -> bool:
    started = state.get("chunk_start_time")
    if not started:
        return False
    return (time.time() - started) >= max_seconds


def build_chunk(
    new_lines: List[str],
    start_line_no: int,
    max_lines: int,
    max_commands: int,
) -> Dict[str, Any]:
    chunk_lines = []
    command_count = 0

    for i, raw_line in enumerate(new_lines):
        line_no = start_line_no + i
        clean = normalize_line(raw_line)

        chunk_lines.append({
            "line_no": line_no,
            "text": clean,
        })

        if detect_prompt_line(clean):
            cmd = extract_command(clean)
            if cmd:
                command_count += 1

        if len(chunk_lines) >= max_lines:
            break
        if command_count >= max_commands:
            break

    return {
        "lines": chunk_lines,
        "command_count": command_count,
    }


def make_llm_input(chunk: Dict[str, Any], state: Dict[str, Any]) -> Dict[str, Any]:
    previous_summaries = state.get("chunk_summaries", [])[-3:]

    return {
        "previous_summaries": previous_summaries,
        "chunk_lines": [
            {"line_index": item["line_no"], "text": item["text"]}
            for item in chunk["lines"]
        ],
        "requirements": {
            "goal": "後から見て、このタイミングで何をしたかが分かる注釈を付ける",
            "annotation_style": "短い1行、事実ベース、控えめ",
            "focus": [
                "何をしたか",
                "何を確認したか",
                "何を見ようとしていたか"
            ],
            "avoid": [
                "過剰な原因断定",
                "長文説明",
                "証拠のない意図推定"
            ]
        }
    }


def schema() -> Dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "chunk_summary": {
                "type": "string",
                "description": "この塊で何をしていたかの短い要約。1文。"
            },
            "annotations": {
                "type": "array",
                "description": "注釈が必要なコマンド行だけ返す",
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


def call_llm(client: OpenAI, model: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    instructions = (
        "あなたは Linux の作業ログに短い注釈を付けるアシスタントです。"
        "目的は、後から読んだ人が『このタイミングで何をしたか』を理解できるようにすることです。"
        "事実ベースで短く書いてください。"
        "分からない場合は『状態確認』『ログ確認』『絞り込み確認』のような控えめな表現にしてください。"
        "注釈は必要なコマンド行だけに付けてください。"
        "コマンド出力行には注釈を付けないでください。"
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
                "name": "script_log_annotations",
                "strict": True,
                "schema": schema(),
            }
        },
    )

    result = json.loads(response.output_text)

    chunk_summary = str(result.get("chunk_summary", "")).strip() or "作業ログを確認"
    annotations = result.get("annotations", [])
    if not isinstance(annotations, list):
        annotations = []

    cleaned = []
    for item in annotations:
        try:
            line_index = int(item["line_index"])
            note = str(item["note"]).strip()
            if not note:
                continue
            cleaned.append({
                "line_index": line_index,
                "note": note,
            })
        except Exception:
            continue

    return {
        "chunk_summary": chunk_summary,
        "annotations": cleaned,
    }


def merge_annotations(state: Dict[str, Any], llm_result: Dict[str, Any], chunk: Dict[str, Any]) -> None:
    ann = state.get("annotations", {})
    for item in llm_result.get("annotations", []):
        ann[str(item["line_index"])] = item["note"]
    state["annotations"] = ann

    summary = llm_result.get("chunk_summary", "").strip()
    if summary and chunk["lines"]:
        first_line = chunk["lines"][0]["line_no"]
        state.setdefault("summary_by_line", {})[str(first_line)] = summary
        state.setdefault("chunk_summaries", []).append(summary)


def regenerate_annotated(raw_lines: List[str], state: Dict[str, Any], out_path: Path) -> None:
    annotations = state.get("annotations", {})
    summaries = state.get("summary_by_line", {})

    out_lines = []
    for idx, line in enumerate(raw_lines, start=1):
        summary = summaries.get(str(idx))
        if summary:
            out_lines.append("=== chunk summary ===")
            out_lines.append(f"概要: {summary}")

        out_lines.append(line)

        note = annotations.get(str(idx))
        if note:
            out_lines.append(f"注釈: {note}")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(out_lines) + ("\n" if out_lines else ""), encoding="utf-8")


def process_once(
    client: OpenAI,
    model: str,
    raw_log: Path,
    annotated_log: Path,
    state_file: Path,
    max_lines: int,
    max_commands: int,
    max_seconds: int,
    debug: bool = False,
) -> None:
    state = load_state(state_file)
    raw_lines = read_all_lines(raw_log)

    last_line = int(state.get("last_line", 0))

    if last_line > len(raw_lines):
        if debug:
            print("[INFO] raw log truncated; resetting state", file=sys.stderr)
        state = default_state()
        last_line = 0

    new_lines = raw_lines[last_line:]

    if not new_lines:
        regenerate_annotated(raw_lines, state, annotated_log)
        save_state(state_file, state)
        return

    if not state.get("chunk_start_time"):
        state["chunk_start_time"] = time.time()

    chunk = build_chunk(
        new_lines=new_lines,
        start_line_no=last_line + 1,
        max_lines=max_lines,
        max_commands=max_commands,
    )

    enough_by_size = (
        len(chunk["lines"]) >= max_lines
        or chunk["command_count"] >= max_commands
    )
    enough_by_time = should_flush_by_time(state, max_seconds=max_seconds)

    if enough_by_size or enough_by_time:
        payload = make_llm_input(chunk, state)

        if debug and chunk["lines"]:
            print(
                f"[INFO] flush chunk: start_line={chunk['lines'][0]['line_no']} "
                f"lines={len(chunk['lines'])} commands={chunk['command_count']}",
                file=sys.stderr,
            )

        llm_result = call_llm(client, model, payload)
        merge_annotations(state, llm_result, chunk)

        consumed = len(chunk["lines"])
        state["last_line"] = last_line + consumed
        state["chunk_start_time"] = None

    regenerate_annotated(raw_lines, state, annotated_log)
    save_state(state_file, state)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Watch script(1) session log and build annotated log.")
    p.add_argument("--raw-log", required=True, help="script の session.log")
    p.add_argument("--annotated-log", required=True, help="注釈付きログ出力先")
    p.add_argument("--state-file", required=True, help="進捗JSON")
    p.add_argument("--model", default=os.environ.get("OPENAI_MODEL", "gpt-5"), help="OpenAI model")
    p.add_argument("--max-lines", type=int, default=100, help="1チャンク最大行数")
    p.add_argument("--max-commands", type=int, default=5, help="1チャンク最大コマンド数")
    p.add_argument("--max-seconds", type=int, default=60, help="チャンク確定最大秒数")
    p.add_argument("--poll-interval", type=int, default=5, help="監視間隔秒")
    p.add_argument("--once", action="store_true", help="1回だけ処理")
    p.add_argument("--debug", action="store_true", help="デバッグ出力")
    return p.parse_args()


def main() -> None:
    args = parse_args()

    client = build_client()
    raw_log = Path(args.raw_log)
    annotated_log = Path(args.annotated_log)
    state_file = Path(args.state_file)

    raw_log.parent.mkdir(parents=True, exist_ok=True)
    annotated_log.parent.mkdir(parents=True, exist_ok=True)
    state_file.parent.mkdir(parents=True, exist_ok=True)

    if args.debug:
        print(f"[INFO] raw_log={raw_log}", file=sys.stderr)
        print(f"[INFO] annotated_log={annotated_log}", file=sys.stderr)
        print(f"[INFO] state_file={state_file}", file=sys.stderr)
        print(f"[INFO] model={args.model}", file=sys.stderr)

    if args.once:
        process_once(
            client=client,
            model=args.model,
            raw_log=raw_log,
            annotated_log=annotated_log,
            state_file=state_file,
            max_lines=args.max_lines,
            max_commands=args.max_commands,
            max_seconds=args.max_seconds,
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
                max_lines=args.max_lines,
                max_commands=args.max_commands,
                max_seconds=args.max_seconds,
                debug=args.debug,
            )
        except KeyboardInterrupt:
            break
        except Exception as e:
            print(f"[ERROR] {e}", file=sys.stderr)

        time.sleep(args.poll_interval)


if __name__ == "__main__":
    main()