import os
import json
import requests
from openai import OpenAI

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

user_query = input("?? 質問を入力してください（例: 拠点Aから拠点Dに通信するには？）\n> ")

embedding = client.embeddings.create(
    model="text-embedding-3-small",
    input=user_query
).data[0].embedding

search_url = "http://10.2.0.204:9200/nw_knowledge/_search"
headers = {"Content-Type": "application/json"}

query_payload = {
    "knn": {
        "field": "embedding",
        "k": 5,
        "num_candidates": 50,
        "query_vector": embedding
    },
    "_source": ["site", "device_type", "content"]
}

response = requests.post(search_url, headers=headers, data=json.dumps(query_payload))
results = response.json()

related_contexts = []
print("\n 関連するネットワーク設定情報:")
for hit in results["hits"]["hits"]:
    src = hit["_source"]
    print(f"【{src['site']} - {src['device_type']}】")
    print(src["content"])
    print("-" * 40)
    related_contexts.append(src["content"])

context_text = "\n".join(related_contexts)
prompt = f"""
以下のネットワーク設定情報に基づいて、質問に答えてください。

【設定情報】
{context_text}

【質問】
{user_query}
"""

chat_response = client.chat.completions.create(
    model="gpt-4",
    messages=[{"role": "user", "content": prompt}]
)

answer = chat_response.choices[0].message.content

print("\nChatGPTの回答:")
print(answer)