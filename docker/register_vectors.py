import json
import openai
import os
import requests

openai.api_key = os.getenv("OPENAI_API_KEY")

es_url = "http://10.2.0.204:9200/nw_knowledge/_doc"

with open("network_config.json", "r", encoding="utf-8") as f:
    data = json.load(f)

for site, config in data.items():
    for device_type, details in config.items():
        content = f"{site} の {device_type} 設定: {json.dumps(details, ensure_ascii=False)}"

        embedding = openai.embeddings.create(
            model="text-embedding-3-small",
            input=content
        ).data[0].embedding

        doc = {
            "site": site,
            "device_type": device_type,
            "content": content,
            "embedding": embedding
        }

        response = requests.post(
            es_url,
            headers={"Content-Type": "application/json"},
            data=json.dumps(doc)
        )

        if response.status_code not in [200, 201]:
            print(f"登録失敗: {response.status_code} → {response.text}")
        else:
            print(f"登録成功: {site} - {device_type}")

print("全データの登録完了")