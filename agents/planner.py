import json
import logging
import os

from langchain_core.messages import SystemMessage
from langchain_openai import ChatOpenAI
from tenacity import retry, stop_after_attempt, wait_exponential

logger = logging.getLogger("deepinsight")

llm = ChatOpenAI(
    model="deepseek-chat",
    api_key=os.getenv("DEEPSEEK_API_KEY"),
    base_url="https://api.deepseek.com",
    temperature=0.3,
    timeout=90,
)

@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=10), reraise=True)
def _invoke_llm(messages):
    res = llm.invoke(messages)
    usage = (res.response_metadata or {}).get("token_usage", {})
    if usage:
        logger.info("LLM 调用", extra={
            "node": "planner",
            "prompt_tokens": usage.get("prompt_tokens", 0),
            "completion_tokens": usage.get("completion_tokens", 0),
        })
    return res

def planner_node(state):
    prompt = f"""你是一个顶级行业分析师。
请严格将主题 '{state['topic']}' 拆解成 **正好 3 个核心调研方向**。
每个方向用一句话描述，直接返回 JSON 格式：
{{"directions": ["方向1", "方向2", "方向3"]}}"""

    res = _invoke_llm([SystemMessage(content=prompt)])
    try:
        directions = json.loads(res.content)["directions"]
    except Exception:
        directions = [line.strip() for line in res.content.split("\n") if line.strip()][:3]

    return {"plan": directions, "steps": 1}
