import asyncio, re
import asyncssh
import httpx

from typing import Any, Dict, List, Optional

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


# Splunk: Search Job API（Enterprise/Cloud想定）
@app.tool()
async def splunk_search_job(
    base_url: str,
    query: str,
    earliest: str = "-15m",
    latest: Optional[str] = None,
    output_mode: str = "json",
    verify_ssl: bool = False,
    username: Optional[str] = None,
    password: Optional[str] = None,
    max_results: int = 200,
    poll_interval: int = 2,
    timeout: int = 60,
) -> Dict[str, Any]:
    """
    商用版 Splunk の Search Job API を使った検索。
    1. /services/search/jobs に POST してジョブを作成
    2. /services/search/jobs/{sid}/results から結果取得
    """

    search_str = query.strip()
    if not search_str.startswith("search "):
        search_str = "search " + search_str
    if "earliest=" not in search_str and earliest:
        search_str = f"{search_str} earliest={earliest}"
    if latest and "latest=" not in search_str:
        search_str = f"{search_str} latest={latest}"

    job_url = base_url.rstrip("/") + "/services/search/jobs"
    auth = httpx.BasicAuth(username, password) if (username and password) else None

    async with _client(verify=verify_ssl) as c:
        # 1. ジョブ作成
        r = await c.post(job_url, data={"search": search_str, "output_mode": "json"}, auth=auth)
        r.raise_for_status()
        sid = r.json()["sid"]

        # 2. ポーリングで完了待ち
        status_url = f"{job_url}/{sid}"
        import asyncio, time
        start = time.time()
        while True:
            rr = await c.get(status_url, params={"output_mode": "json"}, auth=auth)
            rr.raise_for_status()
            entry = rr.json()["entry"][0]
            dispatch_state = entry["content"].get("dispatchState")
            if dispatch_state in ("DONE", "FAILED"):
                break
            if time.time() - start > timeout:
                raise TimeoutError(f"Splunk job {sid} did not complete in {timeout} seconds")
            await asyncio.sleep(poll_interval)

        # 3. 結果取得
        results_url = f"{job_url}/{sid}/results"
        rr = await c.get(
            results_url,
            params={"output_mode": output_mode, "count": max_results},
            auth=auth,
        )
        rr.raise_for_status()
        results = rr.json()["results"]

    return {"sid": sid, "count": len(results), "items": results}




