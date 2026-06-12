from langchain_core.messages import SystemMessage
from langchain_tavily import TavilySearch
from tenacity import retry, stop_after_attempt, wait_exponential

from agents.common import make_invoker, make_llm

tavily_tool = TavilySearch(
    max_results=5,
    search_depth="advanced",
    include_answer=True,
)

llm = make_llm(temperature=0.3)
_invoke_llm = make_invoker(llm, "researcher")


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=10), reraise=True)
async def _search(query: str):
    return await tavily_tool.ainvoke(query)


async def researcher_node(state):
    topic = state["topic"]
    plan = state.get("plan", ["通用调研"])

    idx = len(state.get("content", [])) % len(plan) if plan else 0
    search_query = f"{topic} {plan[idx]} 最新市场 数据 趋势 案例"
    search_results = await _search(search_query)

    prompt = f"""请将以下搜索结果整理成清晰的调研素材，每条素材控制在 2-3 句话：
搜索主题：{topic}
搜索结果：{search_results}
要求：提取关键数据、趋势、公司/案例、政策等。"""

    summary = (await _invoke_llm([SystemMessage(content=prompt)])).content

    return {
        "content": [f"【第 {state.get('steps', 1)} 轮搜索结果】\n{summary}"],
        "steps": state.get("steps", 0) + 1
    }
