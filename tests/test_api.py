"""API 层输入校验、速率限制与 job 存储测试：使用 fakeredis，不依赖真实服务"""
import asyncio
from time import time

import pytest
from fakeredis import FakeAsyncRedis
from pydantic import ValidationError

import backend.main as main
from backend.main import ResearchRequest


@pytest.fixture()
def fake_redis(monkeypatch):
    client = FakeAsyncRedis(decode_responses=True)
    monkeypatch.setattr(main, "redis_client", client)
    return client


# ── 输入校验 ─────────────────────────────────────────────

def test_topic_empty_raises():
    with pytest.raises(ValidationError):
        ResearchRequest(topic="")


def test_topic_whitespace_only_raises():
    with pytest.raises(ValidationError):
        ResearchRequest(topic="   ")


def test_topic_too_long_raises():
    with pytest.raises(ValidationError):
        ResearchRequest(topic="x" * 501)


def test_topic_strips_whitespace():
    req = ResearchRequest(topic="  固态电池  ")
    assert req.topic == "固态电池"


def test_topic_at_max_length_is_valid():
    ResearchRequest(topic="x" * 500)  # 不应抛出


# ── 速率限制（Redis 滑动窗口）────────────────────────────

def test_rate_limit_allows_under_limit(fake_redis):
    async def run():
        for _ in range(5):
            await main._check_rate_limit("ip-allow")  # 5 次不应抛出

    asyncio.run(run())


def test_rate_limit_blocks_on_sixth_request(fake_redis):
    from fastapi import HTTPException

    async def run():
        for _ in range(5):
            await main._check_rate_limit("ip-block")
        with pytest.raises(HTTPException) as exc_info:
            await main._check_rate_limit("ip-block")
        assert exc_info.value.status_code == 429

    asyncio.run(run())


def test_rate_limit_resets_after_window(fake_redis):
    async def run():
        # 填满限额，但时间戳全部在窗口之外
        old = time() - 61
        await fake_redis.zadd("rate:ip-reset", {f"{old}:{i}": old for i in range(5)})
        await main._check_rate_limit("ip-reset")  # 旧记录应被清除并放行

    asyncio.run(run())


# ── job 存储（Redis Hash 原子更新）───────────────────────

def test_job_update_preserves_existing_fields(fake_redis):
    async def run():
        await main._job_update("j1", status="pending", stage="waiting")
        await main._job_update("j1", stage="planner")
        job = await main._job_get("j1")
        assert job["status"] == "pending"   # 未触碰的字段保持不变
        assert job["stage"] == "planner"

    asyncio.run(run())


def test_job_get_returns_none_for_missing(fake_redis):
    async def run():
        assert await main._job_get("不存在的任务") is None

    asyncio.run(run())
