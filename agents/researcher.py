import logging
import os

from langchain_core.messages import SystemMessage
from langchain_openai import ChatOpenAI
from langchain_tavily import TavilySearch
from tenacity import retry, stop_after_attempt, wait_exponential

logger = logging.getLogger("deepinsight")

tavily_tool = TavilySearch(
    max_results=5,
    search_depth="advanced",
    include_answer=True,
)

llm = ChatOpenAI(
    model="deepseek-chat",
    api_key=os.getenv("DEEPSEEK_API_KEY"),
    base_url="https://api.deepseek.com",
    temperature=0.3,
    timeout=90,
)

@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=10), reraise=True)
def _search(query: str):
    return tavily_tool.invoke(query)

@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=10), reraise=True)
def _invoke_llm(messages):
    res = llm.invoke(messages)
    usage = (res.response_metadata or {}).get("token_usage", {})
    if usage:
        logger.info("LLM 调用", extra={
            "node": "researcher",
            "prompt_tokens": usage.get("prompt_tokens", 0),
            "completion_tokens": usage.get("completion_tokens", 0),
        })
    return res

def researcher_node(state):
    topic = state["topic"]
    plan = state.get("plan", ["通用调研"])

    idx = len(state.get("content", [])) % len(plan) if plan else 0
    search_query = f"{topic} {plan[idx]} 最新市场 数据 趋势 案例"
    search_results = _search(search_query)

    prompt = f"""请将以下搜索结果整理成清晰的调研素材，每条素材控制在 2-3 句话：
搜索主题：{topic}
搜索结果：{search_results}
要求：提取关键数据、趋势、公司/案例、政策等。"""

    summary = _invoke_llm([SystemMessage(content=prompt)]).content

    return {
        "content": [f"【第 {state.get('steps', 1)} 轮搜索结果】\n{summary}"],
        "steps": state.get("steps", 0) + 1
    }
