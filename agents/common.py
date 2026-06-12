"""共享 LLM 工厂与带重试的异步调用封装"""
import json
import logging
import os

from langchain_openai import ChatOpenAI
from tenacity import retry, stop_after_attempt, wait_exponential

logger = logging.getLogger("deepinsight")


def make_llm(temperature: float = 0.3) -> ChatOpenAI:
    """各角色共享同一套连接配置，仅按需差异化 temperature"""
    return ChatOpenAI(
        model="deepseek-chat",
        api_key=os.getenv("DEEPSEEK_API_KEY"),
        base_url="https://api.deepseek.com",
        temperature=temperature,
        timeout=90,
    )


def make_invoker(llm: ChatOpenAI, node: str):
    """返回带指数退避重试和 token 用量日志的异步调用函数"""

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=10), reraise=True)
    async def _ainvoke(messages):
        res = await llm.ainvoke(messages)
        usage = (res.response_metadata or {}).get("token_usage", {})
        if usage:
            logger.info("LLM 调用", extra={
                "node": node,
                "prompt_tokens": usage.get("prompt_tokens", 0),
                "completion_tokens": usage.get("completion_tokens", 0),
            })
        return res

    return _ainvoke


def extract_json(text: str) -> dict:
    """从 LLM 输出中提取 JSON 对象，兼容 ```json 围栏和前后缀杂质"""
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end <= start:
        raise ValueError(f"输出中未找到 JSON 对象: {text[:80]}")
    return json.loads(text[start:end + 1])
