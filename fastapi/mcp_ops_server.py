import asyncio, re
import asyncssh
import httpx
import os

from __future__ import annotations
from typing import Any, Dict, List, Optional
from mcp.server.fastmcp import FastMCP

app = FastMCP("ops-bridge")
TIMEOUT = 20.0

# 安全なSSH実行（許可リスト方式）
SAFE_CMD_PATTERNS = [
    r"^who(\s|$)", r"^w(\s|$)", r"^last(\s|$)", r"^lastlog(\s|$)", r"^id(\s|$)",
    r"^uname(\s|$)", r"^uptime(\s|$)",
    r"^cat\s+/etc/os-release(\s|$)", r"^cat\s+/etc/passwd(\s|$)",
    r"^grep\s+.+\s+/var/log/.*", r"^egrep\s+.+\s+/var/log/.*", r"^zgrep\s+.+\s+/var/log/.*",
    r"^tail\s+-n\s+\d+\s+/var/log/.*", r"^head\s+-n\s+\d+\s+/var/log/.*",
    r"^journalctl(\s|$)", r"^dmesg(\s|$)",
    r"^ps\s+aux(\s|$)", r"^ps\s+-ef(\s|$)",
    r"^ss\s+-tuna(\s|$)", r"^netstat\s+-tuna(\s|$)",
    r"^crontab\s+-l(\s|$)",
    r"^ls\s+-l[aA]?\s+.*", r"^find\s+/(var|etc|home|opt|usr)\b.*-maxdepth\s+\d+.*",
]

DENY_TOKENS = [
    "rm ", "shutdown", "reboot", "mkfs", "dd if=", ">:",
    "iptables -F", "ufw disable", "chmod 777", "chown -R",
    "curl | sh", "curl|sh", "wget | sh", "wget|sh",
    "systemctl stop", "systemctl disable", "kill -9", ":(){:|:&};:",
]

def _is_safe_cmd(cmd: str) -> bool:
    c = cmd.strip()
    if any(tok in c for tok in DENY_TOKENS):
        return False
    return any(re.search(p, c) for p in SAFE_CMD_PATTERNS)


def _client(verify: bool = True, headers: Optional[Dict[str,str]] = None):
    return httpx.AsyncClient(
        timeout=TIMEOUT,
        follow_redirects=True,
        headers={"User-Agent": "mcp-ops-bridge/1.0", **(headers or {})},
        verify=verify,
    )

@app.tool()
async def ping() -> str:
    return "pong"


@app.tool()
async def ssh_exec_multi(
    host: str,
    user: str,
    commands: List[str],
    port: int = 22,
    key_path: Optional[str] = None,
    password: Optional[str] = None,
    verify_host: bool = False,
    timeout_sec: int = 15,
    max_bytes: int = 20000,
) -> Dict[str, Any]:
    """
    許可リストに合致する読み取り系コマンドだけを、複数まとめて安全に実行。
    - host/user/port/key_path/password を指定（パスワードまたは鍵のどちらか）
    - verify_host=False で known_hosts 無視（検証環境向け）
    - 1コマンドあたり timeout_sec で打ち切り
    - 出力は先頭 max_bytes まで返す
    """
    if not commands:
        raise ValueError("commands が空です。")
    to_run = [c for c in commands if _is_safe_cmd(c)]
    rejected = [c for c in commands if c not in to_run]
    if not to_run:
        return {"ok": False, "reason": "all commands rejected by safety filter", "rejected": rejected}

    client_keys = [key_path] if key_path else None
    known_hosts = None if not verify_host else asyncssh.read_known_hosts()
    results: List[Dict[str, Any]] = []

    async with asyncssh.connect(
        host=host, port=port, username=user,
        client_keys=client_keys, password=password,
        known_hosts=known_hosts
    ) as conn:
        for cmd in to_run:
            try:
                res = await asyncio.wait_for(conn.run(cmd, check=False), timeout=timeout_sec)
                out = (res.stdout or "")[:max_bytes]
                err = (res.stderr or "")[:max_bytes]
                results.append({
                    "command": cmd, "exit_status": res.exit_status,
                    "stdout": out, "stderr": err,
                })
            except asyncio.TimeoutError:
                results.append({"command": cmd, "timeout": True})
            except Exception as e:
                results.append({"command": cmd, "error": str(e)})

    return {"ok": True, "executed": results, "rejected": rejected}


# Splunk: export API（Free想定）
@app.tool()
async def splunk_search(
    base_url: str,
    query: str,
    earliest: str = "-15m",
    output_mode: str = "json",
    verify_ssl: bool = False,
    username: Optional[str] = None,
    password: Optional[str] = None,
    max_results: int = 200,
) -> Dict[str, Any]:
    """
    curl -k https://HOST:8089/services/search/jobs/export
      -d search='search <query> earliest=-15m' -d output_mode=json
    """
    search_str = query.strip()
    if not search_str.startswith("search "):
        search_str = "search " + search_str
    if "earliest=" not in search_str and earliest:
        search_str = f"{search_str} earliest={earliest}"

    url = base_url.rstrip("/") + "/services/search/jobs/export"
    form = {"search": search_str, "output_mode": output_mode}

    auth = httpx.BasicAuth(username, password) if (username and password) else None

    items: List[Dict[str, Any]] = []
    async with _client(verify=verify_ssl) as c:
        r = await c.post(url, data=form, auth=auth)
        r.raise_for_status()
        async for line in r.aiter_lines():
            if not line:
                continue
            try:
                obj = httpx.Response(200, text=line).json()
            except Exception:
                continue
            row = obj.get("result", obj)
            if isinstance(row, dict):
                items.append(row)
            elif isinstance(row, list):
                items.extend([x for x in row if isinstance(x, dict)])
            if len(items) >= max_results:
                break

    return {"count": len(items), "items": items}

# ServiceNow: Incident 作成
@app.tool()
async def servicenow_create_incident(
    instance_url: str,
    short_description: str,
    description: str,
    severity: str = "medium",
    verify_ssl: bool = True,
    username: Optional[str] = None,
    password: Optional[str] = None,
    bearer_token: Optional[str] = None,
    assignment_group: Optional[str] = None,
    caller_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    ServiceNow Table API で incident を作成。
    - instance_url: https://devXXXXX.service-now.com
    - 認証: Basic( username/password ) or Bearer(bearer_token)
    """
    url = instance_url.rstrip("/") + "/api/now/table/incident"

    # severity → priority/impact/urgency の簡易マッピング
    sev = (severity or "medium").lower()
    # 値域は環境により 1(高)?5(低) など。ここでは一般的な設定を仮定。
    mapping = {
        "critical": {"priority": "1", "impact": "1", "urgency": "1"},
        "high":     {"priority": "2", "impact": "2", "urgency": "2"},
        "medium":   {"priority": "3", "impact": "3", "urgency": "3"},
        "low":      {"priority": "4", "impact": "4", "urgency": "4"},
    }
    sev_map = mapping.get(sev, mapping["medium"])

    payload: Dict[str, Any] = {
        "short_description": short_description,
        "description": description,
        "priority": sev_map["priority"],
        "impact": sev_map["impact"],
        "urgency": sev_map["urgency"],
    }
    if assignment_group:
        payload["assignment_group"] = assignment_group
    if caller_id:
        payload["caller_id"] = caller_id

    headers = {"Accept": "application/json", "Content-Type": "application/json"}
    auth = None
    if bearer_token:
        headers["Authorization"] = f"Bearer {bearer_token}"
    elif username and password:
        auth = httpx.BasicAuth(username, password)
    else:
        raise ValueError("ServiceNowの認証情報を指定してください（username/password か bearer_token）。")

    async with _client(verify=verify_ssl, headers=headers) as c:
        r = await c.post(url, json=payload, auth=auth)
        r.raise_for_status()
        data = r.json()
        # 代表的な返却の拾い上げ
        result = (data or {}).get("result") or {}
        return {
            "number": result.get("number"),
            "sys_id": result.get("sys_id"),
            "url": instance_url.rstrip("/") + "/nav_to.do?uri=incident.do?sys_id=" + str(result.get("sys_id")),
            "raw": data,
        }

@app.tool()
async def redmine_create_issue(
    base_url: str,
    api_key: str,
    project_id: str,
    subject: str,
    description: str,
    tracker_id: Optional[int] = None,
    priority_id: Optional[int] = None,
    assigned_to_id: Optional[int] = None,
    verify_ssl: bool = True,
) -> Dict[str, Any]:
    """
    RedmineのIssueを作成します。
    - base_url: 例) https://redmine.example.com
    - 認証: X-Redmine-API-Key ヘッダ
    - 必須: project_id, subject, description
    - 任意: tracker_id, priority_id, assigned_to_id
    """
    url = base_url.rstrip("/") + "/issues.json"
    headers = {
        "X-Redmine-API-Key": api_key,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    payload = {
        "issue": {
            "project_id": project_id,
            "subject": subject[:255] if subject else "",
            "description": description or "",
        }
    }
    if tracker_id is not None:
        payload["issue"]["tracker_id"] = tracker_id
    if priority_id is not None:
        payload["issue"]["priority_id"] = priority_id
    if assigned_to_id is not None:
        payload["issue"]["assigned_to_id"] = assigned_to_id

    async with _client(verify=verify_ssl, headers=headers) as c:
        r = await c.post(url, json=payload)
        r.raise_for_status()
        data = r.json() or {}
        issue = (data.get("issue") or {})
        issue_id = issue.get("id")
        issue_url = f"{base_url.rstrip('/')}/issues/{issue_id}" if issue_id else None
        return {
            "id": issue_id,
            "url": issue_url,
            "raw": data,
        }

if __name__ == "__main__":
    app.run()