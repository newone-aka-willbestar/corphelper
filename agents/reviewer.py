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
            "node": "reviewer",
            "prompt_tokens": usage.get("prompt_tokens", 0),
            "completion_tokens": usage.get("completion_tokens", 0),
        })
    return res

def reviewer_node(state):
    if not state.get("review_feedback"):
        return {
            "review_feedback": "内容初稿已生成，但缺乏具体案例、数据支撑和未来风险预测，请研究员补充。",
            "steps": state.get("steps", 0) + 1
        }

    prompt = f"""请根据以下素材，撰写一份专业、结构清晰的行业深度研报：
主题：{state['topic']}
现有素材：{''.join(state.get('content', []))}
要求：包含背景、现状、趋势、案例、风险、结论。"""

    res = _invoke_llm([SystemMessage(content=prompt)])
    return {
        "report": res.content,
        "review_feedback": "合格",
        "steps": state.get("steps", 0) + 1
    }
