import os
from typing import TypedDict, List, Annotated
import operator
from dotenv import load_dotenv
from langgraph.graph import StateGraph, START, END


load_dotenv()  # 必须放在所有 import agents 之前！

# 安全检查（防止你忘记配 .env）
if not os.getenv("DEEPSEEK_API_KEY"):
    raise ValueError(
        "❌ DEEPSEEK_API_KEY 未找到！\n"
        "请在项目根目录创建 .env 文件，内容为：\n"
        "DEEPSEEK_API_KEY=sk-你的真实密钥\n"
        "TAVILY_API_KEY=tav-你的tavily密钥（第二步已接入）"
    )


from agents.planner import planner_node
from agents.researcher import researcher_node
from agents.reviewer import reviewer_node

# 1. 定义全局状态
class ResearchState(TypedDict):
    topic: str
    plan: Annotated[List[str], operator.add]
    content: Annotated[List[str], operator.add]
    report: str
    review_feedback: str
    steps: int

# 2. 构建状态机
workflow = StateGraph(ResearchState)

workflow.add_node("planner", planner_node)
workflow.add_node("researcher", researcher_node)
workflow.add_node("reviewer", reviewer_node)

workflow.set_entry_point("planner")
workflow.add_edge("planner", "researcher")
workflow.add_edge("researcher", "reviewer")

def route_after_review(state: ResearchState):
    # "合格" 正常结束；steps >= 6 是兜底安全阀（最多允许 2 次完整迭代）
    if state.get("review_feedback") == "合格" or state.get("steps", 0) >= 6:
        return "end"
    return "researcher"

workflow.add_conditional_edges(
    "reviewer",
    route_after_review,
    {"end": END, "researcher": "researcher"}
)

app = workflow.compile()

# 导出供 app.py 使用
__all__ = ["app"]