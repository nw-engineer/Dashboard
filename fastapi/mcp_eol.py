import asyncio
import requests
from mcp.server.fastmcp import FastMCP, Context

app = FastMCP("eol-server")

API_BASE = "https://endoflife.date/api"

@app.tool()
def get_all_eol_info(ctx: Context, products: list[str]):
    """
    Fetch ALL version EOL info for a list of products
    from https://endoflife.date API.

    Args:
        products: List of product names (e.g., ["python", "ubuntu", "nodejs"])
    Returns:
        Dictionary with all version EOL info per product
    """
    results = {}
    for product in products:
        url = f"{API_BASE}/{product.lower()}.json"
        try:
            res = requests.get(url, timeout=10)
            if res.status_code == 200:
                data = res.json()
                versions = []
                for item in data:
                    versions.append({
                        "cycle": item.get("cycle"),
                        "releaseDate": item.get("releaseDate"),
                        "eol": item.get("eol"),
                        "latest": item.get("latest"),
                        "latestReleaseDate": item.get("latestReleaseDate"),
                        "link": item.get("link"),
                        "lts": item.get("lts"),
                    })
                results[product] = versions
            else:
                results[product] = {"error": f"API returned {res.status_code}"}
        except Exception as e:
            results[product] = {"error": str(e)}
    return results

if __name__ == "__main__":
    asyncio.run(app.run())