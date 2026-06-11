"""图路由逻辑单元测试：无外部依赖"""
from graph import route_after_review


def test_route_exits_when_qualified():
    assert route_after_review({"review_feedback": "合格", "steps": 5}) == "end"


def test_route_continues_when_feedback_pending():
    assert route_after_review({"review_feedback": "需要补充", "steps": 3}) == "researcher"


def test_route_safety_valve_triggers_at_six():
    assert route_after_review({"review_feedback": "需要补充", "steps": 6}) == "end"


def test_route_safety_valve_triggers_above_six():
    assert route_after_review({"review_feedback": "", "steps": 10}) == "end"


def test_route_continues_below_safety_valve():
    assert route_after_review({"review_feedback": "待补充", "steps": 4}) == "researcher"
