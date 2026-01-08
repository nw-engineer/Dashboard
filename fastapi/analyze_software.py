import os
import re
import time
from typing import Dict, List, Any

from ddgs import DDGS
import trafilatura

from langchain_openai import AzureChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage


# =========================
# Config
# =========================

ASPECT_QUERIES = {
    "セキュリティ": "{name} security policy vulnerability disclosure CVE",
    "ライセンス": "{name} license MIT GPL proprietary EULA",
    "更新頻度": "{name} release cadence update frequency changelog releases",
    "依存性": "{name} dependencies package manager build dependencies",
    "総合評価": "{name} reviews reputation popular rating developer community",
}

DEFAULT_RESULTS_PER_ASPECT = 5        # 検索結果の件数（観点ごと）
DEFAULT_MAX_CHARS_PER_SOURCE = 2500   # 1ソースあたりの最大文字数（LLMに渡す量制限）
REQUEST_SLEEP_SEC = 0.2              # 検索/取得の負荷軽減用

SYSTEM_PROMPT = """あなたはソフトウェア調査アシスタントです。
- 必ず与えられたSourcesだけを根拠に答えてください。
- 文章中の根拠には必ず [S1] のようにソースIDを付けてください。
- Sourcesに書かれていないことは推測で断定しないでください。
- 重要な主張には必ず少なくとも1つ、できれば複数の出典をつけてください。
- 出典が不足する場合は「Sourcesに明記なし」と書いてください。
- 可能なら箇条書きで簡潔にまとめてください。
"""

# =========================
# Utilities
# =========================

def clean_text(text: str) -> str:
    """簡易クリーニング（改行や空白を整形）"""
    if not text:
        return ""
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    return text.strip()


def ddg_search(query: str, max_results: int = 5, region: str = "jp-jp") -> List[Dict[str, str]]:
    """DuckDuckGo検索（ddgs）"""
    results = []
    with DDGS() as ddgs:
        for r in ddgs.text(
            keywords=query,
            region=region,
            safesearch="moderate",
            max_results=max_results,
        ):
            url = r.get("href") or r.get("url")
            if not url:
                continue
            results.append({
                "title": r.get("title", ""),
                "url": url,
                "snippet": r.get("body", ""),
            })
    return results


def fetch_page_text(url: str, max_chars: int = 2500, timeout: int = 20) -> str:
    """trafilaturaで本文抽出（失敗したら空文字）"""
    try:
        downloaded = trafilatura.fetch_url(url, timeout=timeout)
        if not downloaded:
            return ""
        text = trafilatura.extract(
            downloaded,
            include_comments=False,
            include_tables=True,
            favor_recall=True,
        )
        if not text:
            return ""
        text = clean_text(text)
        return text[:max_chars]
    except Exception:
        return ""


def build_labeled_sources(search_results: List[Dict[str, str]],
                          max_sources: int = 5,
                          max_chars: int = 2500) -> List[Dict[str, str]]:
    """
    検索結果 -> [{id,title,url,text}] に変換
    """
    sources = []
    for i, r in enumerate(search_results[:max_sources], start=1):
        url = r["url"]
        title = r.get("title", "")
        snippet = r.get("snippet", "")

        # 本文取得（失敗したら snippet を使う）
        time.sleep(REQUEST_SLEEP_SEC)
        text = fetch_page_text(url, max_chars=max_chars)
        if not text:
            text = clean_text(snippet)[:max_chars]

        sources.append({
            "id": f"S{i}",
            "title": title,
            "url": url,
            "text": text
        })
    return sources


def sources_to_prompt_block(sources: List[Dict[str, str]]) -> str:
    """LLMに渡すSourcesブロック文字列を作る"""
    blocks = []
    for s in sources:
        blocks.append(
            f"[{s['id']}] {s['title']}\n"
            f"URL: {s['url']}\n"
            f"CONTENT:\n{s['text']}\n"
        )
    return "\n\n".join(blocks)


# =========================
# Azure OpenAI (LangChain)
# =========================

def create_llm() -> AzureChatOpenAI:
    """
    AzureChatOpenAI の生成
    必須環境変数:
      - AZURE_OPENAI_ENDPOINT
      - AZURE_OPENAI_API_KEY
      - AZURE_OPENAI_API_VERSION
      - AZURE_OPENAI_DEPLOYMENT
    """
    endpoint = os.environ["AZURE_OPENAI_ENDPOINT"]
    api_key = os.environ["AZURE_OPENAI_API_KEY"]
    api_version = os.environ.get("AZURE_OPENAI_API_VERSION", "2024-10-21")
    deployment = os.environ["AZURE_OPENAI_DEPLOYMENT"]

    llm = AzureChatOpenAI(
        azure_endpoint=endpoint,
        api_key=api_key,
        api_version=api_version,
        azure_deployment=deployment,
        temperature=0.2,
    )
    return llm


def summarize_aspect(llm: AzureChatOpenAI,
                     software: str,
                     aspect: str,
                     sources: List[Dict[str, str]]) -> str:
    """
    観点ごとに出典付き要約を生成
    """
    src_block = sources_to_prompt_block(sources)

    user_prompt = f"""対象ソフト: {software}
観点: {aspect}

以下のSourcesに基づいて、観点について簡潔に要点整理してください。
重要な主張には必ず出典（例: [S1]）を付けてください。

Sources:
{src_block}
"""

    messages = [
        SystemMessage(content=SYSTEM_PROMPT),
        HumanMessage(content=user_prompt),
    ]
    resp = llm.invoke(messages)
    return resp.content.strip()


# =========================
# Main Analysis
# =========================

def analyze_software(software: str,
                     results_per_aspect: int = DEFAULT_RESULTS_PER_ASPECT,
                     max_chars_per_source: int = DEFAULT_MAX_CHARS_PER_SOURCE,
                     region: str = "jp-jp") -> Dict[str, Any]:
    """
    指定ソフトについて観点別に調査し、
    outputs と sources_map を返す
    """
    llm = create_llm()

    outputs: Dict[str, str] = {}
    sources_map: Dict[str, List[Dict[str, str]]] = {}

    for aspect, qtmpl in ASPECT_QUERIES.items():
        query = qtmpl.format(name=software)
        print(f"\n[INFO] Searching aspect='{aspect}' query='{query}' ...")

        time.sleep(REQUEST_SLEEP_SEC)
        search_results = ddg_search(query, max_results=results_per_aspect, region=region)
        if not search_results:
            outputs[aspect] = "Sourcesに明記なし（検索結果が取得できませんでした）。"
            sources_map[aspect] = []
            continue

        sources = build_labeled_sources(
            search_results,
            max_sources=results_per_aspect,
            max_chars=max_chars_per_source
        )
        sources_map[aspect] = sources

        print(f"[INFO] Generating summary for aspect='{aspect}' ...")
        outputs[aspect] = summarize_aspect(llm, software, aspect, sources)

    return {
        "software": software,
        "outputs": outputs,
        "sources_map": sources_map
    }


def print_report(result: Dict[str, Any]):
    """見やすく出力"""
    software = result["software"]
    outputs = result["outputs"]
    sources_map = result["sources_map"]

    print("\n" + "=" * 90)
    print(f"Software: {software}")
    print("=" * 90)

    for aspect, text in outputs.items():
        print("\n" + "-" * 90)
        print(f"[{aspect}]")
        print("-" * 90)
        print(text)

        # 観点ごとのsources一覧
        sources = sources_map.get(aspect, [])
        if sources:
            print("\nSources:")
            for s in sources:
                print(f"- [{s['id']}] {s['url']}")
        else:
            print("\nSources: (none)")

    print("\n" + "=" * 90)


if __name__ == "__main__":
    # ここを好きなソフト名に変更してください
    software_name = "Visual Studio Code"

    result = analyze_software(
        software=software_name,
        results_per_aspect=5,        # 観点ごとの検索件数
        max_chars_per_source=2500,   # 各ソース本文の最大文字数
        region="jp-jp"
    )

    print_report(result)
