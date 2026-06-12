"""Checkpoint 断点续跑集成测试：mock 全部外部调用，LangGraph 图真实执行"""
import asyncio
import json
from unittest.mock import AsyncMock, patch

from tests.conftest import make_llm_response


def test_graph_resumes_from_checkpoint_after_restart(tmp_path):
    """中断后换一个新的 checkpointer 连接续跑，模拟进程重启场景"""
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

    from graph import workflow

    db = str(tmp_path / "ckpt.sqlite")
    config = {"configurable": {"thread_id": "job-1"}}

    planner_res  = make_llm_response('{"directions": ["方向A", "方向B", "方向C"]}')
    research_res = make_llm_response("素材摘要")
    verdict_res  = make_llm_response(json.dumps({"score": 9, "feedback": ""}, ensure_ascii=False))
    report_res   = make_llm_response("最终研报")

    async def first_run():
        async with AsyncSqliteSaver.from_conn_string(db) as saver:
            # interrupt_before 人为制造"执行到一半进程挂掉"的检查点
            app = workflow.compile(checkpointer=saver, interrupt_before=["reviewer"])
            init = {"topic": "主题", "plan": [], "content": [],
                    "report": "", "review_feedback": "", "steps": 0}
            await app.ainvoke(init, config)
            snapshot = await app.aget_state(config)
            assert snapshot.next == ("reviewer",)      # 停在 reviewer 之前
            assert snapshot.values["steps"] == 2       # planner + researcher 已执行

    async def resumed_run():
        async with AsyncSqliteSaver.from_conn_string(db) as saver:
            app = workflow.compile(checkpointer=saver)
            result = await app.ainvoke(None, config)   # 输入 None = 从检查点继续
            assert result["report"] == "最终研报"
            assert result["review_score"] == 9
            assert result["plan"] == ["方向A", "方向B", "方向C"]  # 检查点恢复了已积累状态

    with patch("agents.planner._invoke_llm", new=AsyncMock(return_value=planner_res)), \
         patch("agents.researcher._search", new=AsyncMock(return_value=[])), \
         patch("agents.researcher._invoke_llm", new=AsyncMock(return_value=research_res)), \
         patch("agents.reviewer._evaluate_llm", new=AsyncMock(return_value=verdict_res)), \
         patch("agents.reviewer._write_llm", new=AsyncMock(return_value=report_res)):
        asyncio.run(first_run())
        asyncio.run(resumed_run())
