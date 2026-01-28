from typing import Optional, Tuple, Union
from pydantic import BaseModel

from openai import AzureOpenAI, AsyncAzureOpenAI
from deepeval.models import DeepEvalBaseLLM

class AzureGPT5SafeModel(DeepEvalBaseLLM):
    """
    GPT-5系(Azure)向け: temperature / top_p / logprobs 等を送らない安全版
    """
    def __init__(self, *, model: str, azure_endpoint: str, api_version: str, deployment_name: str, api_key: str):
        super().__init__(model)
        self.azure_endpoint = azure_endpoint.rstrip("/")
        self.api_version = api_version
        self.deployment_name = deployment_name
        self.api_key = api_key

    def _client(self) -> AzureOpenAI:
        return AzureOpenAI(
            api_key=self.api_key,
            api_version=self.api_version,
            azure_endpoint=self.azure_endpoint,
            azure_deployment=self.deployment_name,
        )

    def _aclient(self) -> AsyncAzureOpenAI:
        return AsyncAzureOpenAI(
            api_key=self.api_key,
            api_version=self.api_version,
            azure_endpoint=self.azure_endpoint,
            azure_deployment=self.deployment_name,
        )

    def generate(self, prompt: str, schema: Optional[BaseModel] = None) -> Tuple[Union[str, BaseModel], float]:
        client = self._client()
        # 重要: temperature を渡さない
        completion = client.chat.completions.create(
            model=self.deployment_name,
            messages=[{"role": "user", "content": [{"type": "text", "text": prompt}]}],
        )
        return completion.choices[0].message.content, 0.0

    async def a_generate(self, prompt: str, schema: Optional[BaseModel] = None) -> Tuple[Union[str, BaseModel], float]:
        client = self._aclient()
        completion = await client.chat.completions.create(
            model=self.deployment_name,
            messages=[{"role": "user", "content": [{"type": "text", "text": prompt}]}],
        )
        return completion.choices[0].message.content, 0.0
