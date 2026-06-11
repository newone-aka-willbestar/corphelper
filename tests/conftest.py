import pytest
from unittest.mock import MagicMock


def make_llm_response(content: str) -> MagicMock:
    res = MagicMock()
    res.content = content
    res.response_metadata = {"token_usage": {"prompt_tokens": 100, "completion_tokens": 50}}
    return res
