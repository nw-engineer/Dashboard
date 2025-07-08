from fastapi import FastAPI
from pydantic import BaseModel
import paramiko
import openai
import re

app = FastAPI()

openai.api_key = ""

USERNAME = "tadashi"
PRIVATE_KEY_PATH = "/app/amazonlinux2023_key"

class Query(BaseModel):
    question: str

def ask_llm(prompt: str) -> str:
    """OpenAI APIを使ってプロンプトを処理する"""
    response = openai.Completion.create(
        model="gpt-4",
        prompt=prompt,
        max_tokens=150,
        temperature=0.4
    )
    return response.choices[0].text.strip()

def extract_ip(text: str) -> str:
    """質問文からIPアドレスを抽出"""
    match = re.search(r"\b(?:\d{1,3}\.){3}\d{1,3}\b", text)
    return match.group(0) if match else None

def determine_if_remote_needed(question: str) -> bool:
    """LLMでリモート接続の必要性を判断（必要→True, 不要→False）"""
    prompt = (
        f"次の質問にリモートLinuxサーバへの接続は必要ですか？\n"
        f"質問: {question}\n"
        f"必要なら「必要」、不要なら「不要」だけを返してください。"
    )
    result = ask_llm(prompt)
    return "必要" in result

def generate_command(question: str) -> str:
    """LLMで質問に応じたLinuxコマンドを生成"""
    prompt = (
        f"次の質問に対して、Linuxシステムで調査すべきコマンドをsudo付きで1行のコマンドを生成してください。\n"
        f"注意: 'ssh' や 'scp' を含めないでください。\n"
        f"質問: {question}\n"
        f"コマンドのみを1行で返してください。"
    )
    return ask_llm(prompt)

def run_ssh_command(ip: str, command: str) -> str:
    """秘密鍵を使ってSSHでコマンドを実行"""
    try:
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        key = paramiko.RSAKey.from_private_key_file(PRIVATE_KEY_PATH)
        ssh.connect(hostname=ip, username=USERNAME, pkey=key)
        _, stdout, stderr = ssh.exec_command(command)
        output = stdout.read().decode()
        error = stderr.read().decode()
        ssh.close()
        return output if output else error
    except Exception as e:
        return f"SSHエラー: {e}"

def generate_analysis(question: str, command: str = None, result: str = None) -> str:
    """LLMで考察を生成（コマンド・結果がある場合は踏まえて考察）"""
    prompt = f"質問: {question}\n"
    if command:
        prompt += f"実行したコマンド: {command}\n"
    if result:
        prompt += f"コマンドの出力:\n{result}\n"
    prompt += "これについて詳しく説明してください。原因や改善案があれば提案してください。"
    return ask_llm(prompt)

@app.post("/query")
async def query_handler(query: Query):
    question = query.question
    ip = extract_ip(question)
    remote_needed = determine_if_remote_needed(question)

    if remote_needed:
        if not ip:
            return {"error": "リモート接続が必要と判断されましたが、質問にIPアドレスが含まれていません。"}
        command = generate_command(question)
        result = run_ssh_command(ip, command)
        analysis = generate_analysis(question, command, result)
        return {
            "ip": ip,
            "question": question,
            "command": command,
            "result": result,
            "analysis": analysis
        }
    else:
        analysis = generate_analysis(question)
        return {
            "question": question,
            "analysis": analysis
        }