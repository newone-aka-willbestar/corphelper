from langchain_core.messages import SystemMessage

from agents.common import extract_json, make_invoker, make_llm

# 评分需要确定性，低温度；研报撰写允许更高文采
_evaluate_llm = make_invoker(make_llm(temperature=0.1), "reviewer-eval")
_write_llm = make_invoker(make_llm(temperature=0.6), "reviewer-write")

_SCORE_THRESHOLD = 7
_MAX_ITER_STEPS = 6  # 与 graph.route_after_review 的安全阀保持一致


async def reviewer_node(state):
    steps = state.get("steps", 0)
    material = "".join(state.get("content", []))

    # 仍有迭代空间时执行 LLM 质量评审；达到上限则跳过评审直接成稿，保证流程必有产出
    if steps < _MAX_ITER_STEPS:
        eval_prompt = f"""你是一个严格的研报质量评审。请从三个维度评估以下调研素材：
1. 数据支撑：是否有具体数字、统计来源
2. 案例丰富度：是否有公司、产品或政策实例
3. 风险与趋势预测：是否覆盖未来变量和不确定性

主题：{state['topic']}
素材：{material}

返回 JSON：{{"score": 0到10的整数综合分, "feedback": "若不合格，给研究员的具体补充建议"}}"""

        res = await _evaluate_llm([SystemMessage(content=eval_prompt)])
        try:
            verdict = extract_json(res.content)
            score = int(verdict.get("score", 10))
            feedback = str(verdict.get("feedback", "")).strip()
        except Exception:
            # 评分解析失败时放行成稿，避免格式问题导致流程卡死
            score, feedback = 10, ""

        if score < _SCORE_THRESHOLD:
            if not feedback or feedback == "合格":
                feedback = "素材缺乏数据支撑与案例，请补充。"
            return {"review_feedback": feedback, "steps": steps + 1}

    prompt = f"""请根据以下素材，撰写一份专业、结构清晰的行业深度研报：
主题：{state['topic']}
现有素材：{material}
要求：包含背景、现状、趋势、案例、风险、结论。"""

    res = await _write_llm([SystemMessage(content=prompt)])
    return {
        "report": res.content,
        "review_feedback": "合格",
        "steps": steps + 1
    }
