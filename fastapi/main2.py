from langchain_core.language_models import BaseLLM
from langchain_core.outputs import LLMResult, Generation
from langchain_core.callbacks.manager import CallbackManagerForLLMRun
from typing import Optional, List, Dict, Any
import requests
from pydantic import BaseModel, Field
import json
import tiktoken
import re


class CHATOLLAMA(BaseLLM, BaseModel):

    endpoint: str = Field(default="http://10.2.0.204:8000/chat")

    def __repr__(self):
        return f"CHATOLLAMA(endpoint='{self.endpoint}', params={self._default_params()})"

    def _truncate_prompt(self, prompt: Any, max_tokens: int = 512) -> str:

        if isinstance(prompt, list):
            prompt = " ".join(map(str, prompt))

        if not isinstance(prompt, str):
            raise ValueError(f"Invalid prompt type: expected str, got {type(prompt)}")

        enc = tiktoken.get_encoding("cl100k_base")
        tokens = enc.encode(prompt)

        if len(tokens) > max_tokens:
            print(f"DEBUG: Truncating prompt from {len(tokens)} to {max_tokens} tokens")
            tokens = tokens[:max_tokens]
            prompt = enc.decode(tokens)

        return prompt


    def clean_response(self, response: str) -> str:
        response = re.sub(r"Llama\.generate:.*", "", response)
        response = re.sub(r"\[Task history memory ends here\].*?\[Current state starts here\]", "", response, flags=re.DOTALL)
        response = re.sub(r"Current url: about:blank.*?Current date and time:.*", "", response, flags=re.DOTALL)
        return response.strip()


    def extract_text_only(self, api_response: dict) -> str:
        if not isinstance(api_response, dict):
            print(f"DEBUG: extract_text_only() received unexpected type: {type(api_response)}")
            return "No valid response received."

        text_responses = [item["text"] for item in api_response.get("messages", []) if item.get("type") == "text"]

        return "\n".join(text_responses) if text_responses else "No text response available."


    def _call(self, prompt: str, stop: Optional[List[str]] = None, **kwargs) -> str:
        print("DEBUG: _call() has been called")
        print(f"DEBUG: Endpoint: {self.endpoint}")
        print(f"DEBUG: Sending request with JSON: {{'content': '{prompt}'}}")

        max_context_window = 1024
        max_response_tokens = 200
        prompt_max_tokens = max_context_window - max_response_tokens

        prompt = self._truncate_prompt(prompt, max_tokens=prompt_max_tokens)

        if isinstance(prompt, list):
            prompt = json.dumps(prompt, ensure_ascii=False)


        payload = {
            "content": prompt,
            "max_tokens": max_response_tokens
        }

        print(f"DEBUG: Sending request with JSON: {payload}")

        try:
            response = requests.post(self.endpoint, json=payload, proxies={})
            response.raise_for_status()
            result = response.json()

            if isinstance(result, str):
                print("DEBUG: API response is string, attempting to parse JSON.")
                result = json.loads(result)

            if isinstance(result, list) and len(result) > 0:
                result = result[0]

            print(f"DEBUG: Received response: {result}")

            if not isinstance(result, dict):
                print("DEBUG: Unexpected API response format:", result)
                return json.dumps({"error": "Unexpected response format"})

            extracted_text = self.extract_text_only(result)
            cleaned_response = self.clean_response(extracted_text)

            if not cleaned_response:
                cleaned_response = "No meaningful response received."

            if "response" not in result:
                print("DEBUG: Unexpected API response format:", result)
                return json.dumps({"error": "Unexpected response format"})

            agent_response = {
                "current_state": {
                    "page_summary": result["response"],
                    "evaluation_previous_goal": "Unknown",
                    "memory": "Task started.",
                    "next_goal": "Provide the requested information."
                },
                "action": [
                    {
                        "done": {
                            "text": result["response"]
                        }
                    }
                ]
            }

            print(f"DEBUG: Formatted Agent Response: {agent_response}")

            return json.dumps(agent_response)

        except requests.exceptions.HTTPError as http_err:
            print(f"DEBUG: HTTP error occurred: {http_err}")
            print(f"DEBUG: Response Content: {response.text}")

            return json.dumps({
                "current_state": {
                    "page_summary": f"HTTP Error {response.status_code}: {response.text}",
                    "evaluation_previous_goal": "Failed",
                    "memory": "N/A",
                    "next_goal": "Check API endpoint or request format"
                },
                "action": [
                    {
                        "done": {
                            "text": f"HTTP Error {response.status_code}. Check request format."
                        }
                    }
                ]
            })

        except requests.exceptions.RequestException as e:
            print("DEBUG: Request failed!")
            import traceback
            print(traceback.format_exc())

            return json.dumps({
                "current_state": {
                    "page_summary": "Request Exception",
                    "evaluation_previous_goal": "Failed",
                    "memory": "N/A",
                    "next_goal": "Check network or API availability"
                },
                "action": [
                    {
                        "done": {
                            "text": f"Error: {str(e)}"
                        }
                    }
                ]
            })

    def _generate(
        self,
        prompts: List[str],
        stop: Optional[List[str]] = None,
        run_manager: Optional[CallbackManagerForLLMRun] = None,
        **kwargs
    ) -> LLMResult:
        print("DEBUG: _generate() has been called")
        generations = []
        for prompt in prompts:
            response_text = self._call(prompt, stop=stop, **kwargs)
            generations.append([Generation(text=response_text)])

        return LLMResult(generations=generations)

    def _llm_type(self) -> str:
        return "CHATOLLAMA"

    def _default_params(self) -> Dict[str, Any]:
        return {"max_tokens": 100}

    # `with_structured_output()` を追加
    def with_structured_output(self, output_model, include_raw=False, **kwargs):
        """
        LangChain の `with_structured_output()` を模倣する。
        """
        class StructuredLLM:
            def __init__(self, llm_instance):
                self.llm = llm_instance

            async def ainvoke(self, input_messages):
                """
                LangChain の非同期メソッド `ainvoke()` を模倣し、適切な形式で結果を返す。
                """
                prompt = input_messages[-1].content  # 最後の HumanMessage の内容を取得
                response_json = self.llm._call(prompt)  # 直接 _call() を実行

                try:
                    parsed_response = json.loads(response_json)  # JSON パース
                    return {"parsed": output_model(**parsed_response)}  # Pydantic モデルに変換
                except json.JSONDecodeError:
                    return {"parsed": output_model(
                        current_state={"page_summary": "Failed to parse JSON", "evaluation_previous_goal": "Failed",
                                       "memory": "N/A", "next_goal": "Check JSON format"},
                        action=[{"done": {"text": "JSON parsing failed"}}]
                    )}

        return StructuredLLM(self)


import os
from browser_use import Agent
from browser_use.browser.browser import Browser, BrowserConfig
import asyncio

os.environ["REQUESTS_CA_BUNDLE"] = "./myCA.pem"
os.environ["SSL_CERT_FILE"] = "./myCA.pem"
os.environ["http_proxy"] = "http://10.2.0.60:8080"
os.environ["https_proxy"] = "http://10.2.0.60:8080"
os.environ["NO_PROXY"] = "localhost,127.0.0.1,10.2.0.204"

#browser_config = BrowserConfig(start_url="https://www.google.com")

chatOllama = CHATOLLAMA()

print(f"chatOllama instance: {repr(chatOllama)}")

print("DEBUG: Testing direct API call...")
#response = chatOllama._call("東京の天気はわかりますか？")
#print("Direct API Call Response:", response)

async def main():
    try:
        print("DEBUG: Creating Agent instance...")
        agent = Agent(
            task="Googleで「東京の天気」を検索してください。",
            llm=chatOllama,
        )

        print("DEBUG: Running Agent...")
        result = await agent.run()
        print("DEBUG: Agent finished execution.")
        print("Agent Result:", result)

        model_outputs = result.model_outputs()
        print("DEBUG: model_outputs =", model_outputs)

        if model_outputs and isinstance(model_outputs, list) and len(model_outputs) > 0:
            agent_output = model_outputs[0]


            if hasattr(agent_output, "action") and isinstance(agent_output.action, list) and len(agent_output.action) > 0:

                action_taken = agent_output.action[0].done.text
                print(f"DEBUG: Action Taken: {action_taken}")
                if agent_output.action and len(agent_output.action) > 0:
                    action_taken = agent_output.action[0].done.text
                    print(f"DEBUG: Action Taken: {action_taken}")

                    if "Google" not in action_taken:
                        print("ERROR: Agent did not perform Google search!")
                        manual_search_action = {
                            "search_google": {
                                "query": "東京の天気"
                            }
                        }
                        agent_output.action.append(manual_search_action)
                        print("DEBUG: Forced search_google action:", model_outputs)
                else:
                    print("ERROR: No actions found in agent_output!")
            else:
                print("ERROR: model_outputs is empty!")

        print("Agent Result:", result)


    except Exception as e:
        import traceback
        print("ERROR: Agent execution failed!")
        print(traceback.format_exc())

asyncio.run(main())