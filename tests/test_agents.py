"""Agent 节点单元测试：全部使用 mock，不发起真实 LLM / Tavily 请求"""
import asyncio
import json
from unittest.mock import AsyncMock, patch

import pytest

from tests.conftest import make_llm_response


# ── planner ──────────────────────────────────────────────

def test_planner_parses_valid_json():
    from agents.planner import planner_node

    mock_res = make_llm_response('{"directions": ["方向A", "方向B", "方向C"]}')
    with patch("agents.planner._invoke_llm", new=AsyncMock(return_value=mock_res)):
        result = asyncio.run(planner_node({"topic": "固态电池"}))

    assert result["plan"] == ["方向A", "方向B", "方向C"]
    assert result["steps"] == 1


def test_planner_falls_back_on_invalid_json():
    from agents.planner import planner_node

    mock_res = make_llm_response("方向A\n方向B\n方向C\n多余行")
    with patch("agents.planner._invoke_llm", new=AsyncMock(return_value=mock_res)):
        result = asyncio.run(planner_node({"topic": "固态电池"}))

    assert len(result["plan"]) == 3
    assert result["steps"] == 1


# ── researcher ───────────────────────────────────────────

def test_researcher_uses_first_plan_on_first_pass():
    from agents.researcher import researcher_node

    mock_res    = make_llm_response("整理后的摘要")
    mock_search = AsyncMock(return_value=[{"content": "搜索结果"}])

    with patch("agents.researcher._invoke_llm", new=AsyncMock(return_value=mock_res)), \
         patch("agents.researcher._search", new=mock_search):

        state = {"topic": "固态电池", "plan": ["方向A", "方向B", "方向C"], "content": [], "steps": 2}
        asyncio.run(researcher_node(state))

    called_query = mock_search.call_args[0][0]
    assert "方向A" in called_query


def test_researcher_cycles_to_second_direction_on_second_pass():
    from agents.researcher import researcher_node

    mock_res    = make_llm_response("整理后的摘要")
    mock_search = AsyncMock(return_value=[{"content": "搜索结果"}])

    with patch("agents.researcher._invoke_llm", new=AsyncMock(return_value=mock_res)), \
         patch("agents.researcher._search", new=mock_search):

        state = {
            "topic": "固态电池",
            "plan": ["方向A", "方向B", "方向C"],
            "content": ["第一轮已有内容"],   # len=1 → idx=1
            "steps": 4,
        }
        asyncio.run(researcher_node(state))

    called_query = mock_search.call_args[0][0]
    assert "方向B" in called_query


def test_researcher_appends_step_label():
    from agents.researcher import researcher_node

    mock_res = make_llm_response("摘要内容")
    with patch("agents.researcher._invoke_llm", new=AsyncMock(return_value=mock_res)), \
         patch("agents.researcher._search", new=AsyncMock(return_value=[])):

        state = {"topic": "主题", "plan": ["方向A"], "content": [], "steps": 3}
        result = asyncio.run(researcher_node(state))

    assert "第 3 轮" in result["content"][0]
    assert result["steps"] == 4


# ── reviewer ─────────────────────────────────────────────

def _verdict_response(score: int, feedback: str = "") -> object:
    return make_llm_response(json.dumps({"score": score, "feedback": feedback}, ensure_ascii=False))


def test_reviewer_rejects_when_score_below_threshold():
    from agents.reviewer import reviewer_node

    eval_mock = AsyncMock(return_value=_verdict_response(4, "缺少数据支撑，请补充统计数字"))
    with patch("agents.reviewer._evaluate_llm", new=eval_mock):
        state = {"topic": "主题", "content": ["素材"], "steps": 2}
        result = asyncio.run(reviewer_node(state))

    assert result["review_feedback"] == "缺少数据支撑，请补充统计数字"
    assert "report" not in result
    assert result["steps"] == 3


def test_reviewer_generates_report_when_score_passes():
    from agents.reviewer import reviewer_node

    eval_mock  = AsyncMock(return_value=_verdict_response(9))
    write_mock = AsyncMock(return_value=make_llm_response("最终研报正文"))
    with patch("agents.reviewer._evaluate_llm", new=eval_mock), \
         patch("agents.reviewer._write_llm", new=write_mock):

        state = {"topic": "主题", "content": ["素材一", "素材二"], "steps": 4}
        result = asyncio.run(reviewer_node(state))

    assert result["review_feedback"] == "合格"
    assert result["report"] == "最终研报正文"
    assert result["steps"] == 5


def test_reviewer_force_writes_at_iteration_cap():
    """steps 达到上限时跳过评审直接成稿，保证流程必有产出"""
    from agents.reviewer import reviewer_node

    eval_mock  = AsyncMock()
    write_mock = AsyncMock(return_value=make_llm_response("兜底研报"))
    with patch("agents.reviewer._evaluate_llm", new=eval_mock), \
         patch("agents.reviewer._write_llm", new=write_mock):

        state = {"topic": "主题", "content": ["素材"], "steps": 6}
        result = asyncio.run(reviewer_node(state))

    eval_mock.assert_not_awaited()
    assert result["report"] == "兜底研报"
    assert result["review_feedback"] == "合格"


def test_reviewer_passes_on_unparseable_verdict():
    """评分输出不是合法 JSON 时放行成稿，避免格式问题导致死循环"""
    from agents.reviewer import reviewer_node

    eval_mock  = AsyncMock(return_value=make_llm_response("这不是 JSON"))
    write_mock = AsyncMock(return_value=make_llm_response("研报正文"))
    with patch("agents.reviewer._evaluate_llm", new=eval_mock), \
         patch("agents.reviewer._write_llm", new=write_mock):

        state = {"topic": "主题", "content": ["素材"], "steps": 2}
        result = asyncio.run(reviewer_node(state))

    assert result["review_feedback"] == "合格"
    assert result["report"] == "研报正文"
