import asyncio, os, json, re, logging
from typing import TypedDict, List, Any, Dict, Optional
from collections import Counter

from datetime import datetime, timezone
from langgraph.graph import StateGraph, END
from openai import OpenAI
from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

# ログ抑制（SSHライブラリの冗長出力を抑える）
logging.getLogger("asyncssh").setLevel(logging.WARNING)

SERVER_CMD = "python"
SERVER_ARGS = [os.path.abspath("mcp_ops_server.py")]

# State型定義
class Decision(TypedDict, total=False):
    should_create: bool
    severity: str
    reason: str
    short_summary_ja: str
    findings: List[Dict[str, Any]]

class State(TypedDict):
    # Splunk
    base_url: str
    query: str
    earliest: str
    verify_ssl: bool
    logs: List[Dict[str, Any]]

    # SSH接続ホスト
    target_host: Optional[str]

    # LLM 初回判断
    decision: Decision

    # SSH 計画＆結果
    ssh_plan: List[str]
    ssh_results: List[Dict[str, Any]]

    # Redmine
    rm_base_url: Optional[str]
    rm_api_key: Optional[str]
    rm_project_id: Optional[str]
    rm_tracker_id: Optional[int]
    rm_priority_id: Optional[int]
    rm_assigned_to_id: Optional[int]
    rm_verify_ssl: bool

    # ServiceNow
    sn_instance_url: str
    sn_username: Optional[str]
    sn_password: Optional[str]
    sn_bearer: Optional[str]
    sn_verify_ssl: bool

    # SSH 接続
    ssh_user: Optional[str]
    ssh_key_path: Optional[str]
    ssh_password: Optional[str]
    ssh_port: int
    ssh_verify_host: bool

# MCP呼び出し
async def call_mcp(tool: str, args: Dict[str, Any]) -> Any:
    params = StdioServerParameters(command=SERVER_CMD, args=SERVER_ARGS, env={**os.environ})
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            res = await session.call_tool(tool, args)
            return json.loads(res.content[0].text) if res.content else {}


def _save_json(obj: dict, path: str) -> None:
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print("[warn] failed to save review json:", e)

def _print_ssh_review(ssh_review: list) -> None:
    if not ssh_review:
        print("[ssh_review] (empty)")
        return
    print("\n[LLM SSH Review]")
    for i, r in enumerate(ssh_review, 1):
        cmd   = r.get("command", "")
        es    = r.get("exit_status")
        ver   = r.get("verdict", "")
        kfs   = r.get("key_findings") or []
        notes = r.get("notes", "")
        print(f"#{i} verdict={ver} exit={es}")
        print(f"  $ {cmd}")
        if kfs:
            for k in kfs[:5]:
                print(f"    - {k}")
        if notes:
            print(f"    notes: {notes[:200]}")
    print("")


# 共通ユーティリティ
def _even_samples(rows: List[Any], k: int) -> List[Any]:
    """リストから均等間引きで最大k件サンプル（先頭/末尾も残す）"""
    n = len(rows)
    if n <= k: return rows
    idxs = [0] + [round(i*(n-1)/(k-1)) for i in range(1, k-1)] + [n-1]
    out, seen = [], set()
    for i in idxs:
        if i not in seen:
            out.append(rows[i]); seen.add(i)
    return out

def _top_host(items: List[Dict[str, Any]]) -> Optional[str]:
    c = Counter()
    for r in items:
        h = r.get("host")
        if isinstance(h, str): c[h] += 1
    return c.most_common(1)[0][0] if c else None

# ノード: 収集
async def fetch_logs(state: State) -> State:
    out = await call_mcp("splunk_search", {
        "base_url": state["base_url"],
        "query": state["query"],
        "earliest": state["earliest"],
        "verify_ssl": state["verify_ssl"],
        "max_results": 2000
    })
    items = [r for r in out.get("items", []) if isinstance(r, dict)]
    state["logs"] = items
    state["target_host"] = state.get("target_host") or _top_host(items)
    print("[target_host]", state.get("target_host"))
    return state

# ===================== ノード: 初回解析 =====================
DANGEROUS_HINTS = re.compile(
    r"(failed password|unauthorized|denied|forbidden|error\s*5\d\d|"
    r"sql\s*injection|union\s+select|or\s+1\s*=\s*1|information_schema|"
    r"sleep\(\s*\d+\s*\)|sqlmap|xss|rce|bruteforce)",
    re.I
)

async def analyze(state: State) -> State:
    logs = state.get("logs") or []
    if not logs:
        state["decision"] = {"should_create": False, "severity":"low", "reason":"ログ0件（ヒットなし）", "short_summary_ja":"検出なし", "findings":[]}
        return state

    hinted = [row for row in logs if DANGEROUS_HINTS.search(json.dumps(row, ensure_ascii=False))]
    samples = _even_samples(hinted, 120) + _even_samples([r for r in logs if r not in hinted], max(0, 200-len(hinted)))

    meta = {
        "total_logs": len(logs),
        "hinted_logs": len(hinted),
        "sampled": len(samples),
        "query": state["query"],
        "earliest": state["earliest"],
        "target_host": state.get("target_host"),
    }

    client = OpenAI()
    system = (
        "あなたはSOCアナリストです。提供されたログサンプルと集計から、"
        "『今すぐ調査（SSHプローブ）が必要か』を判断します。"
        "誤検知を避け、証拠が弱い場合は should_create=false。"
        "返答は必ずJSONのみ。日本語。短文は120文字以内。"
    )
    user = {
        "meta": meta,
        "samples": samples,
        "decision_schema": {
            "should_create": "bool",
            "severity": "low|medium|high|critical（true時）",
            "short_summary_ja": "120文字以内",
            "reason_ja": "400文字以内",
            "findings": "任意配列（summary/reason/raw など）"
        }
    }
    resp = client.chat.completions.create(
        model="gpt-4o-mini", temperature=0.0,
        response_format={"type": "json_object"},
        messages=[
            {"role":"system","content":system},
            {"role":"user","content":json.dumps(user, ensure_ascii=False)}
        ],
    )
    d = json.loads(resp.choices[0].message.content)
    short = (d.get("short_summary_ja") or "")[:120]
    reason = (d.get("reason_ja") or "")[:400]
    state["decision"] = {
        "should_create": bool(d.get("should_create", False)),
        "severity": d.get("severity","low"),
        "short_summary_ja": short,
        "reason": reason,
        "findings": d.get("findings") or []
    }
    return state

# ノード: SSH 調査計画
SSH_TIPS = r"""
使えるコマンドは次の例に限定してください（例を参考に具体化）:
- who ; w ; last ; lastlog
- id ; uname -a ; uptime
- tail -n 200 /var/log/nginx/access.log | grep -Ei 'union[[:space:]]+select|or[[:space:]]+1[[:space:]]*=[[:space:]]*1|sqlmap|information_schema|sleep\([0-9]+\)'
- grep -Ei 'failed password|invalid user' /var/log/auth.log | tail -n 200
- ps aux | egrep -i 'nginx|mysql|sqlmap|netcat|nc' | head -n 50
- ss -tuna | head -n 200
- journalctl -S -1h | egrep -i 'fail|error|denied' | head -n 200
- crontab -l
- ls -la /etc/cron.* | head -n 200
- find /var/www -maxdepth 3 -type f -name '*.php' | head -n 200
"""

def _normalize_cmd(c: str) -> str:
    c2 = c.strip()
    if "grep -i" in c2 and r"\s" in c2:
        c2 = c2.replace("grep -i", "grep -Ei")
        c2 = (c2
              .replace(r"\s\+", "[[:space:]]+")
              .replace(r"\s*", "[[:space:]]*")
              .replace(r"\s", "[[:space:]]"))
    if ("grep -i" in c2) and any(tok in c2 for tok in [r"\+", "{", "}"]):
        c2 = c2.replace("grep -i", "grep -Ei")
    return c2

async def plan_probe(state: State) -> State:
    if not state.get("target_host"):
        state["ssh_plan"] = []
        return state

    client = OpenAI()
    prompt = {
        "goal": "ログの兆候（SQLi/認証攻撃など）を検証するため、読み取り専用の短時間コマンドで現地確認したい。",
        "constraints": [
            "破壊的操作は不可（rm/systemctl stop など禁止）",
            "1コマンドは数秒で終わるもの、ログ/プロセス/ネットワーク/認証を確認",
            "最大8個まで、各コマンドは100文字以内",
        ],
        "tips": SSH_TIPS,
        "format": {"commands": ["who", "tail -n 200 /var/log/nginx/access.log | grep -Ei 'union[[:space:]]+select'"]},
        "context": {"host": state["target_host"], "first_decision": state["decision"]},
    }
    resp = client.chat.completions.create(
        model="gpt-4o-mini", temperature=0.0,
        response_format={"type":"json_object"},
        messages=[
            {"role":"system","content":"許可された安全コマンドだけを提案してください。JSONのみ。"},
            {"role":"user","content":json.dumps(prompt, ensure_ascii=False)}
        ],
    )
    plan = json.loads(resp.choices[0].message.content)
    cmds = plan.get("commands") or []

    # 重複・長さ・個数・補正
    uniq = []
    for c in cmds:
        c = (c or "").strip()
        if c and c not in uniq and len(c) <= 120:
            uniq.append(_normalize_cmd(c))
        if len(uniq) >= 8: break

    state["ssh_plan"] = uniq
    print("[ssh_plan]", state["ssh_plan"])
    return state

# ノード: SSH実行
async def exec_probe(state: State) -> State:
    if not state.get("target_host") or not state.get("ssh_plan"):
        state["ssh_results"] = []
        return state
    res = await call_mcp("ssh_exec_multi", {
        "host": state["target_host"],
        "user": state.get("ssh_user") or "root",
        "port": state.get("ssh_port", 22),
        "key_path": state.get("ssh_key_path"),
        "password": state.get("ssh_password"),
        "verify_host": state.get("ssh_verify_host", False),
        "commands": state["ssh_plan"],
        "timeout_sec": 15,
        "max_bytes": 20000,
    })
    state["ssh_results"] = res.get("executed") or []
    return state

# ノード: 最終判定
async def decide_final(state: State) -> State:
    dec0 = state.get("decision") or {}
    client = OpenAI()

    # LLMに返させるスキーマを明示
    prompt = {
        "question": "以下のSSH結果を踏まえ、本当にインシデント起票が必要か？",
        "base_decision": dec0,
        "ssh_results": state.get("ssh_results") or [],
        "rules": [
            "証拠が明確なら should_create=true",
            "誤検知の可能性が高ければ false に下げる",
            "short_summary_ja は120文字以内、日本語で要点のみ",
            "reason_ja は400文字以内、何を根拠にしたかを書く",
            "各コマンドごとに解釈レビューを返す",
            "起票しない場合は、ssh_review の 'safe' 根拠を2点以上、reason_jaに要約して記述"
        ],
        "return_schema": {
            "should_create": "bool",
            "severity": "low|medium|high|critical",
            "short_summary_ja": "<=120 chars",
            "reason_ja": "<=400 chars",
            "findings": "optional array",
            "ssh_review": [
                {
                    "command": "string",
                    "exit_status": "int or null",
                    "key_findings": ["string", "..."],   # そのコマンド出力から読み取ったポイント（最大5）
                    "verdict": "safe|suspicious|malicious",
                    "notes": "short explanation (<=200 chars)"
                }
            ]
        }
    }

    resp = client.chat.completions.create(
        model="gpt-4o-mini",
        temperature=0.0,
        response_format={"type":"json_object"},
        messages=[
            {"role":"system","content":"JSONのみで返答。スキーマを満たすこと。"},
            {"role":"user","content":json.dumps(prompt, ensure_ascii=False)}
        ],
    )

    d = json.loads(resp.choices[0].message.content)
    short  = (d.get("short_summary_ja") or dec0.get("short_summary_ja") or "")[:120]
    reason = (d.get("reason_ja") or dec0.get("reason") or "")[:400]
    review = d.get("ssh_review") or []

    state["last_ssh_review"] = review

    safe = sum(1 for r in review if (r.get("verdict") == "safe"))
    susp = sum(1 for r in review if (r.get("verdict") == "suspicious"))
    mal  = sum(1 for r in review if (r.get("verdict") == "malicious"))

    if safe > 0 and susp == 0 and mal == 0:
        d["should_create"] = False
        d["severity"] = "low"
        d["reason_ja"] = (d.get("reason_ja") or "") + "（SSH調査では侵害兆候なしのため抑止）"


    # 画面にきれいに出す
    _print_ssh_review(review)

    # 監査用に保存（タイムスタンプ付き）
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    _save_json(
        {"timestamp": ts, "ssh_review": review, "base_decision": dec0},
        f"ssh_llm_review_{ts}.json"
    )

    state["decision"] = {
        "should_create": bool(d.get("should_create", False)),
        "severity": d.get("severity") or dec0.get("severity","low"),
        "short_summary_ja": short,
        "reason": reason,
        "findings": d.get("findings") or dec0.get("findings") or [],
    }
    return state


# ノード: 起票（必要時のみ）
async def notify(state: State) -> State:
    dec = state.get("decision") or {}
    review = state.get("last_ssh_review") or []
    susp = sum(1 for r in review if r.get("verdict") in ("suspicious","malicious"))

    if not dec.get("should_create") or susp == 0:
        print(f"[skip] 起票せず: SSHに怪しい所見なし ? {dec.get('reason')}")
        return state

    sev = (dec.get("severity") or "medium").lower()
    short = dec.get("short_summary_ja") or f"危険ログ検出（{sev}）"
    if len(short) > 160: short = short[:157].rstrip() + "…"

    desc = dec.get("reason") or "自動解析で危険と判定。"
    # SSHの要点を数件だけ追記
    ex = state.get("ssh_results") or []
    if ex:
        bullets = []
        for r in ex[:3]:
            line = f"$ {r.get('command')}\nexit={r.get('exit_status')}  out={(r.get('stdout') or '')[:120].replace('\\n',' ')}"
            bullets.append(line)
        desc = (desc + "\n\n[SSH抜粋]\n" + "\n".join(bullets))[:4000]

    # 任意：Splunkの検索URLも追記（UIのホスト名に合わせて調整）
    try:
        spl_url = (
            f"{state['base_url'].replace(':8089','')}/en-US/app/search/search?"
            f"q={json.dumps(state['query'])}&earliest={state['earliest']}&latest=now"
        )
        if len(desc) < 3800:
            desc += f"\n\nSplunk検索: {spl_url}"
    except Exception:
        pass

    if state.get("rm_base_url") and state.get("rm_api_key") and state.get("rm_project_id"):
        args = {
            "base_url": state["rm_base_url"],
            "api_key": state["rm_api_key"],
            "project_id": state["rm_project_id"],
            "subject": short,
            "description": desc,
            "verify_ssl": state.get("rm_verify_ssl", True),
        }
        # 任意フィールド
        if state.get("rm_tracker_id") is not None:
            args["tracker_id"] = int(state["rm_tracker_id"])
        if state.get("rm_priority_id") is not None:
            args["priority_id"] = int(state["rm_priority_id"])
        if state.get("rm_assigned_to_id") is not None:
            args["assigned_to_id"] = int(state["rm_assigned_to_id"])

        res = await call_mcp("redmine_create_issue", args)
        print("Redmine issue:", res.get("id"), res.get("url"))
        return state

    args = {
        "instance_url": state["sn_instance_url"],
        "short_description": short,
        "description": desc,
        "severity": sev,
        "verify_ssl": state["sn_verify_ssl"],
    }
    if state["sn_bearer"]:
        args["bearer_token"] = state["sn_bearer"]
    else:
        args["username"] = state["sn_username"]
        args["password"] = state["sn_password"]

    res = await call_mcp("servicenow_create_incident", args)
    print("ServiceNow incident:", res.get("number"), res.get("url"))
    return state

# Build & main
def build_graph():
    g = StateGraph(State)
    g.add_node("fetch_logs", fetch_logs)
    g.add_node("analyze", analyze)
    g.add_node("plan_probe", plan_probe)
    g.add_node("exec_probe", exec_probe)
    g.add_node("decide_final", decide_final)
    g.add_node("notify", notify)
    g.set_entry_point("fetch_logs")
    g.add_edge("fetch_logs", "analyze")
    g.add_edge("analyze", "plan_probe")
    g.add_edge("plan_probe", "exec_probe")
    g.add_edge("exec_probe", "decide_final")
    g.add_edge("decide_final", "notify")
    g.add_edge("notify", END)
    return g.compile()

async def main():
    graph = build_graph()
    result = await graph.ainvoke({
        # Splunk
        "base_url": "https://10.2.0.40:8089",
        "query": 'index=main earliest=-30m',
        "earliest": "-30m",
        "verify_ssl": False,
        "logs": [],
        "target_host": None,

        # 初回判断の初期器
        "decision": {"should_create": False, "severity":"low", "reason":"", "short_summary_ja":"", "findings":[]},

        # SSH接続（鍵 or パスワードのどちらかを設定）
        "ssh_user": os.getenv("SSH_USER")
        "ssh_key_path": os.getenv("SSH_KEY_PATH"),
        "ssh_password": os.getenv("SSH_PASSWORD"),
        "ssh_port": int(os.getenv("SSH_PORT") or "22"),
        "ssh_verify_host": False,

        # Redmine
        "rm_base_url": os.getenv("RM_BASE_URL"),
        "rm_api_key": os.getenv("RM_API_KEY"),
        "rm_project_id": os.getenv("RM_PROJECT_ID"),
        "rm_tracker_id": int(os.getenv("RM_TRACKER_ID") or "0") or None,
        "rm_priority_id": int(os.getenv("RM_PRIORITY_ID") or "0") or None,
        "rm_assigned_to_id": int(os.getenv("RM_ASSIGNED_TO_ID") or "0") or None,
        "rm_verify_ssl": (os.getenv("RM_VERIFY_SSL") or "true").lower() != "false",

        # ServiceNow
        "sn_instance_url": os.getenv("SN_INSTANCE_URL") or "https://dev330752.service-now.com",
        "sn_username": os.getenv("SN_USERNAME"),
        "sn_password": os.getenv("SN_PASSWORD"),
        "sn_bearer": os.getenv("SN_BEARER_TOKEN"),
        "sn_verify_ssl": True,
    })
    print("done. final decision:", result["decision"])

if __name__ == "__main__":
    asyncio.run(main())