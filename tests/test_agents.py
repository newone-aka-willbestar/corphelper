"""Agent 节点单元测试：全部使用 mock，不发起真实 LLM / Tavily 请求"""
from unittest.mock import patch

import pytest

from tests.conftest import make_llm_response


# ── planner ──────────────────────────────────────────────

def test_planner_parses_valid_json():
    from agents.planner import planner_node

    mock_res = make_llm_response('{"directions": ["方向A", "方向B", "方向C"]}')
    with patch("agents.planner._invoke_llm", return_value=mock_res):
        result = planner_node({"topic": "固态电池"})

    assert result["plan"] == ["方向A", "方向B", "方向C"]
    assert result["steps"] == 1


def test_planner_falls_back_on_invalid_json():
    from agents.planner import planner_node

    mock_res = make_llm_response("方向A\n方向B\n方向C\n多余行")
    with patch("agents.planner._invoke_llm", return_value=mock_res):
        result = planner_node({"topic": "固态电池"})

    assert len(result["plan"]) == 3
    assert result["steps"] == 1


# ── researcher ───────────────────────────────────────────

def test_researcher_uses_first_plan_on_first_pass():
    from agents.researcher import researcher_node

    mock_res    = make_llm_response("整理后的摘要")
    mock_search = [{"content": "搜索结果"}]

    with patch("agents.researcher._invoke_llm", return_value=mock_res), \
         patch("agents.researcher._search", return_value=mock_search) as mock_s:

        state = {"topic": "固态电池", "plan": ["方向A", "方向B", "方向C"], "content": [], "steps": 2}
        researcher_node(state)

    called_query = mock_s.call_args[0][0]
    assert "方向A" in called_query


def test_researcher_cycles_to_second_direction_on_second_pass():
    from agents.researcher import researcher_node

    mock_res    = make_llm_response("整理后的摘要")
    mock_search = [{"content": "搜索结果"}]

    with patch("agents.researcher._invoke_llm", return_value=mock_res), \
         patch("agents.researcher._search", return_value=mock_search) as mock_s:

        state = {
            "topic": "固态电池",
            "plan": ["方向A", "方向B", "方向C"],
            "content": ["第一轮已有内容"],   # len=1 → idx=1
            "steps": 4,
        }
        researcher_node(state)

    called_query = mock_s.call_args[0][0]
    assert "方向B" in called_query


def test_researcher_appends_step_label():
    from agents.researcher import researcher_node

    mock_res = make_llm_response("摘要内容")
    with patch("agents.researcher._invoke_llm", return_value=mock_res), \
         patch("agents.researcher._search", return_value=[]):

        state = {"topic": "主题", "plan": ["方向A"], "content": [], "steps": 3}
        result = researcher_node(state)

    assert "第 3 轮" in result["content"][0]
    assert result["steps"] == 4


# ── reviewer ─────────────────────────────────────────────

def test_reviewer_rejects_on_first_pass():
    from agents.reviewer import reviewer_node

    state = {"topic": "主题", "review_feedback": "", "content": ["素材"], "steps": 2}
    result = reviewer_node(state)

    assert result["review_feedback"] != "合格"
    assert "补充" in result["review_feedback"]
    assert result["steps"] == 3


def test_reviewer_generates_report_on_second_pass():
    from agents.reviewer import reviewer_node

    mock_res = make_llm_response("最终研报正文")
    with patch("agents.reviewer._invoke_llm", return_value=mock_res):
        state = {
            "topic": "主题",
            "review_feedback": "需要补充案例",
            "content": ["素材一", "素材二"],
            "steps": 4,
        }
        result = reviewer_node(state)

    assert result["review_feedback"] == "合格"
    assert result["report"] == "最终研报正文"
    assert result["steps"] == 5
