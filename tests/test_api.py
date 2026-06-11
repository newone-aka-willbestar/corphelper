"""API 层输入校验和速率限制测试：不依赖 Redis 或真实 LLM"""
from time import time
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from backend.main import ResearchRequest, _check_rate_limit, _rate_store


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


# ── 速率限制 ─────────────────────────────────────────────

def test_rate_limit_allows_under_limit():
    ip = "test-ip-allow"
    _rate_store.pop(ip, None)
    for _ in range(5):
        _check_rate_limit(ip)  # 5 次不应抛出


def test_rate_limit_blocks_on_sixth_request():
    from fastapi import HTTPException

    ip = "test-ip-block"
    _rate_store.pop(ip, None)
    for _ in range(5):
        _check_rate_limit(ip)

    with pytest.raises(HTTPException) as exc_info:
        _check_rate_limit(ip)

    assert exc_info.value.status_code == 429


def test_rate_limit_resets_after_window():
    from fastapi import HTTPException

    ip = "test-ip-reset"
    # 填满限额，然后把时间戳推到窗口之外
    _rate_store[ip] = [time() - 61] * 5   # 全部超过 60s

    _check_rate_limit(ip)  # 应该放行（旧记录被清除）
