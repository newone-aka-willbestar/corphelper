from langchain_core.messages import SystemMessage

from agents.common import extract_json, make_invoker, make_llm

# 规划要求输出稳定的 JSON 结构，低温度减少格式漂移
llm = make_llm(temperature=0.2)
_invoke_llm = make_invoker(llm, "planner")


async def planner_node(state):
    prompt = f"""你是一个顶级行业分析师。
请严格将主题 '{state['topic']}' 拆解成 **正好 3 个核心调研方向**。
每个方向用一句话描述，直接返回 JSON 格式：
{{"directions": ["方向1", "方向2", "方向3"]}}"""

    res = await _invoke_llm([SystemMessage(content=prompt)])
    try:
        directions = extract_json(res.content)["directions"]
    except Exception:
        directions = [line.strip() for line in res.content.split("\n") if line.strip()][:3]

    return {"plan": directions, "steps": 1}
