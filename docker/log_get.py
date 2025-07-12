import re
from datetime import datetime, timedelta, timezone
import paramiko
import openai

openai.api_key = ""

def ask_llm(prompt: str, model="gpt-4") -> str:
    response = openai.ChatCompletion.create(
        model=model,
        messages=[
            {"role": "system", "content": "あなたはUNIXログ解析に詳しいエンジニアです。"},
            {"role": "user", "content": prompt}
        ]
    )
    return response['choices'][0]['message']['content'].strip()


def ssh_read_file(ip, username, key_path, remote_path) -> list:
    # リモートファイルを読み込んで行リストとして返す
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(hostname=ip, username=username, key_filename=key_path)

    stdin, stdout, stderr = client.exec_command(f"cat {remote_path}")
    lines = stdout.readlines()
    client.close()
    return lines

def detect_log_timestamp_format(line: str) -> str:
    # ログ行からタイムスタンプフォーマットを推定
    patterns = [
        (r"^\d{4}/\d{2}/\d{2} \d{2}:\d{2}:\d{2}", "%Y/%m/%d %H:%M:%S"),
        (r"\[\d{2}/[A-Za-z]{3}/\d{4}:\d{2}:\d{2}:\d{2} [+-]\d{4}\]", "%d/%b/%Y:%H:%M:%S %z"),
        (r"\[\d{2}/[A-Za-z]{3}/\d{4}:\d{2}:\d{2}:\d{2}\]", "%d/%b/%Y:%H:%M:%S"),
        (r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}", "%Y-%m-%dT%H:%M:%S"),
        (r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", "%Y-%m-%d %H:%M:%S"),
    ]
    for regex, fmt in patterns:
        if re.search(regex, line):
            return fmt
    return "unknown"

def extract_recent_logs_remote(ip: str, username: str, key_path: str, log_path: str, minutes: int = 30):
    lines = ssh_read_file(ip, username, key_path, log_path)
    if not lines:
        print("ログが空です。")
        return

    timestamp_format = detect_log_timestamp_format(lines[0])
    if timestamp_format == "unknown":
        print("未対応のログフォーマットです。")
        return

    now = datetime.now(timezone.utc).astimezone()
    since = now - timedelta(minutes=minutes)

    print(f"[INFO] タイムスタンプ形式: {timestamp_format}")
    print(f"[INFO] {minutes}分以内のログを抽出中...\n")

    for line in lines:
        ts_match = None

        try:
            if timestamp_format == "%Y/%m/%d %H:%M:%S":
                ts_match = re.match(r"^\d{4}/\d{2}/\d{2} \d{2}:\d{2}:\d{2}", line)
                if ts_match:
                    ts = datetime.strptime(ts_match.group(), timestamp_format)
                    if ts >= since.replace(tzinfo=None):
                        print(line.strip())
                        answer = detect_anomalies_in_log(line.strip())
                        print(answer)

            elif timestamp_format == "%d/%b/%Y:%H:%M:%S %z":
                ts_match = re.search(r"\[\d{2}/[A-Za-z]{3}/\d{4}:\d{2}:\d{2}:\d{2} [+-]\d{4}\]", line)
                if ts_match:
                    raw = ts_match.group().strip("[]")
                    ts = datetime.strptime(raw, timestamp_format)
                    if ts >= since:
                        print(line.strip())
                        answer = detect_anomalies_in_log(line.strip())
                        print(answer)

            elif timestamp_format == "%d/%b/%Y:%H:%M:%S":
                ts_match = re.search(r"\[\d{2}/[A-Za-z]{3}/\d{4}:\d{2}:\d{2}:\d{2}\]", line)
                if ts_match:
                    raw = ts_match.group().strip("[]")
                    ts = datetime.strptime(raw, timestamp_format)
                    if ts >= since.replace(tzinfo=None):
                        print(line.strip())
                        answer = detect_anomalies_in_log(line.strip())
                        print(answer)

            elif timestamp_format == "%Y-%m-%dT%H:%M:%S":
                ts_match = re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}", line)
                if ts_match:
                    ts = datetime.strptime(ts_match.group(), timestamp_format)
                    if ts >= since.replace(tzinfo=None):
                        print(line.strip())
                        answer = detect_anomalies_in_log(line.strip())
                        print(answer)

            elif timestamp_format == "%Y-%m-%d %H:%M:%S":
                ts_match = re.match(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", line)
                if ts_match:
                    ts = datetime.strptime(ts_match.group(), timestamp_format)
                    if ts >= since.replace(tzinfo=None):
                        print(line.strip())
                        answer = detect_anomalies_in_log(line.strip())
                        print(answer)

        except Exception as e:
            print(f"[WARN] ログ解析失敗: {line.strip()} → {e}")


def detect_anomalies_in_log(log_text: str, model="gpt-4") -> str:
    prompt = f"""
        以下はサーバーログの一部です。エラー、セキュリティ上の異常、不審なアクセス、繰り返される失敗など、
        運用上の問題と考えられる行が含まれているかどうかを分析してください。

        ログ:
        {log_text}

        【あなたのタスク】
        - 異常があるかどうかを "あり / なし" で簡潔に述べたうえで
        - あれば、どのような異常か簡潔に要約してください
    """
    return ask_llm(prompt, model=model)


extract_recent_logs_remote(
    ip="10.2.0.17",
    username="tadashi",
    key_path="/root/.ssh/id_rsa",
    log_path="/var/log/nginx/access.log",
    #log_path="/var/log/nginx/error.log",
    minutes=30
)