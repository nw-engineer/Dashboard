import asyncssh

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

# SSH command execution tool
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