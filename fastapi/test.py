import boto3
import json

client = boto3.client(service_name='bedrock-runtime', region_name="us-east-1")

# 履歴用リスト
history = []

def get_history():
    return "\n".join(history)

def get_configuration():
    return json.dumps({
            "inputText": get_history(),
            "textGenerationConfig": {
                "maxTokenCount": 4096,
                "stopSequences": [],
                "temperature": 0,
                "topP": 1
            }
    })

print(
    "Bot: こんにちは！チャットボットです。どんなことでもお気軽にご相談ください。"
)

while True:
    user_input = input("User: ")
    history.append("User: " + user_input)
    if user_input.lower() == "exit":
        break
    response = client.invoke_model(
        body=get_configuration(), 
        modelId="amazon.titan-text-express-v1", 
        accept="application/json", 
        contentType="application/json")
    response_body = json.loads(response.get('body').read())
    output_text = response_body.get('results')[0].get('outputText').strip()
    print(output_text)
    history.append(output_text)
    #print(response_body.get('results')[0].get('outputText'))
