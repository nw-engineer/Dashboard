import asyncio, json
from mcp.client.session import ClientSession
from mcp.client.sse import sse_client, SseServerParameters

SERVER_URL = "http://<サーバIP>:8000"

async def main():
    params = SseServerParameters(url=SERVER_URL)
    async with sse_client(params) as (read, write):
        async with ClientSession(read, write) as s:
            await s.initialize()

            tools = await s.list_tools()
            print("== tools ==", [t.name for t in tools.tools])

            # 休み抽出
            res = await s.call_tool("pdf_calendar_extract", {
                "pdf_path": "/app/calendars/2025_calendar.pdf"
            })
            print(json.loads(res.content[0].text))

            # QA
            res = await s.call_tool("pdf_calendar_answer", {
                "question": "10月の休み一覧をだして",
                "pdf_path": "/app/calendars/2025_calendar.pdf",
                "prefer_year": 2025
            })
            print(json.loads(res.content[0].text))

if __name__ == "__main__":
    asyncio.run(main())
