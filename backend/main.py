import asyncio
import json
import logging
import os
import uuid
from contextlib import asynccontextmanager
from datetime import date
from time import time

import redis.asyncio as aioredis
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, field_validator

load_dotenv()

from graph import app as agent_app

# ── 结构化日志（P-05）────────────────────────────────────
class _JSONFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict = {
            "ts":    self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "msg":   record.getMessage(),
        }
        for key in ("request_id", "job_id", "duration_ms", "ip",
                    "node", "prompt_tokens", "completion_tokens",
                    "daily_tokens", "budget"):
            if hasattr(record, key):
                payload[key] = getattr(record, key)
        return json.dumps(payload, ensure_ascii=False)

_handler = logging.StreamHandler()
_handler.setFormatter(_JSONFormatter())
logger = logging.getLogger("deepinsight")
logger.addHandler(_handler)
logger.setLevel(logging.INFO)

# ── Redis（P-04）────────────────────────────────────────
_REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")
_JOB_TTL   = 3600
redis_client: aioredis.Redis = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    global redis_client
    redis_client = aioredis.from_url(_REDIS_URL, decode_responses=True)
    try:
        await redis_client.ping()
        logger.info("Redis 连接成功: %s", _REDIS_URL)
    except Exception as e:
        logger.error("Redis 连接失败: %s", e)
        raise RuntimeError(f"Redis 不可达: {e}")
    yield
    if redis_client:
        await redis_client.aclose()

# ── FastAPI ──────────────────────────────────────────────
_ALLOWED_ORIGINS = os.getenv("ALLOWED_ORIGINS", "http://localhost:8501").split(",")

app = FastAPI(
    title="LangGraph Enterprise Data Insight Agent API",
    version="1.0.0",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=_ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# job 以 Redis Hash 存储：HSET 按字段原子更新，避免读-改-写竞态
async def _job_get(job_id: str) -> dict | None:
    raw = await redis_client.hgetall(f"job:{job_id}")
    if not raw:
        return None
    return {k: json.loads(v) for k, v in raw.items()}

async def _job_update(job_id: str, **fields):
    key = f"job:{job_id}"
    await redis_client.hset(key, mapping={
        k: json.dumps(v, ensure_ascii=False) for k, v in fields.items()
    })
    await redis_client.expire(key, _JOB_TTL)

# ── 每日 Token 预算追踪（P-10）──────────────────────────
_DEEPSEEK_INPUT_PRICE  = 0.14   # $ / M tokens
_DEEPSEEK_OUTPUT_PRICE = 0.28   # $ / M tokens

async def _track_daily_tokens(prompt_t: int, compl_t: int):
    key    = f"daily_tokens:{date.today().isoformat()}"
    total  = await redis_client.incrby(key, prompt_t + compl_t)
    await redis_client.expire(key, 172800)  # 保留两天

    cost   = prompt_t * _DEEPSEEK_INPUT_PRICE / 1_000_000 + compl_t * _DEEPSEEK_OUTPUT_PRICE / 1_000_000
    budget = int(os.getenv("DAILY_TOKEN_BUDGET", "500000"))

    logger.info("token 用量统计", extra={
        "prompt_tokens": prompt_t,
        "completion_tokens": compl_t,
        "estimated_cost_usd": round(cost, 6),
        "daily_tokens": total,
        "budget": budget,
    })
    if total > budget:
        logger.warning("每日 token 用量超出预算上限 %d，当前已用 %d", budget, total,
                       extra={"daily_tokens": total, "budget": budget})

# ── 速率限制（P-03）─────────────────────────────────────
# Redis ZSET 滑动窗口：多 worker 部署时限流计数全局共享
_RATE_LIMIT  = 5
_RATE_WINDOW = 60

async def _check_rate_limit(ip: str):
    now = time()
    key = f"rate:{ip}"
    await redis_client.zremrangebyscore(key, 0, now - _RATE_WINDOW)
    if await redis_client.zcard(key) >= _RATE_LIMIT:
        raise HTTPException(429, f"请求过于频繁，每分钟最多 {_RATE_LIMIT} 次")
    await redis_client.zadd(key, {f"{now}:{uuid.uuid4().hex[:6]}": now})
    await redis_client.expire(key, _RATE_WINDOW)

# ── API Key 认证（P-09）─────────────────────────────────
async def _verify_api_key(x_api_key: str | None = Header(default=None)):
    expected = os.getenv("API_KEY")
    if expected and x_api_key != expected:
        raise HTTPException(401, "无效的 API Key，请在请求头携带 X-API-Key")

# ── 请求 ID 中间件（P-05）───────────────────────────────
@app.middleware("http")
async def _request_logger(request: Request, call_next):
    request_id = str(uuid.uuid4())[:8]
    request.state.request_id = request_id
    t0 = time()
    response = await call_next(request)
    duration_ms = int((time() - t0) * 1000)
    logger.info(
        "%s %s %d",
        request.method, request.url.path, response.status_code,
        extra={"request_id": request_id, "duration_ms": duration_ms, "ip": request.client.host},
    )
    response.headers["X-Request-ID"] = request_id
    return response

# ── 数据模型 ─────────────────────────────────────────────
class ResearchRequest(BaseModel):
    topic: str

    @field_validator("topic")
    @classmethod
    def _validate_topic(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("主题不能为空")
        if len(v) > 500:
            raise ValueError("主题不能超过 500 字")
        return v

# ── 后台研究任务（P-01）──────────────────────────────────
_LIST_FIELDS = {"plan", "content"}

async def _run_research(job_id: str, topic: str):
    await _job_update(job_id, status="running")
    accumulated = {"plan": [], "content": [], "report": "", "review_feedback": "", "steps": 0}
    t0 = time()
    total_prompt_tokens = 0
    total_compl_tokens  = 0

    try:
        async for chunk in agent_app.astream(
            {"topic": topic, **accumulated},
            stream_mode="updates",
        ):
            node_name = next(iter(chunk))
            await _job_update(job_id, stage=node_name)
            logger.info("节点完成: %s", node_name, extra={"job_id": job_id})

            for key, val in chunk[node_name].items():
                if key in _LIST_FIELDS:
                    accumulated[key] = accumulated.get(key, []) + (val if isinstance(val, list) else [val])
                else:
                    accumulated[key] = val

        duration_ms = int((time() - t0) * 1000)
        await _job_update(
            job_id,
            status="done",
            stage="complete",
            report=accumulated.get("report", "生成失败"),
            steps=accumulated.get("steps", 0),
            plan=accumulated.get("plan", []),
            content_snippets=accumulated.get("content", [])[-2:],
            review_score=accumulated.get("review_score"),
            duration_ms=duration_ms,
        )
        logger.info("任务完成，耗时 %dms", duration_ms, extra={"job_id": job_id, "duration_ms": duration_ms})

        # token 追踪需要从节点 response_metadata 中收集，此处使用估算（可通过 LangSmith 获取精确值）
        # 如需精确统计，在 .env 中配置 LANGCHAIN_TRACING_V2=true

    except Exception as e:
        logger.error("任务失败: %s", e, extra={"job_id": job_id})
        await _job_update(job_id, status="error", stage="failed", error=str(e))

# ── 路由 ────────────────────────────────────────────────
@app.post("/research", dependencies=[Depends(_verify_api_key)])
async def start_research(request: Request, body: ResearchRequest):
    ip = request.client.host
    await _check_rate_limit(ip)

    job_id = str(uuid.uuid4())
    await _job_update(job_id, status="pending", stage="waiting", created_at=time())
    asyncio.create_task(_run_research(job_id, body.topic))

    logger.info("任务已创建: %s", body.topic, extra={"job_id": job_id, "ip": ip})
    return {"job_id": job_id, "status": "pending"}


@app.get("/research/{job_id}", dependencies=[Depends(_verify_api_key)])
async def get_research(job_id: str):
    job = await _job_get(job_id)
    if not job:
        raise HTTPException(404, "任务不存在或已过期")

    if job["status"] == "done":
        return {
            "status":           "done",
            "report":           job.get("report", ""),
            "steps":            job.get("steps", 0),
            "plan":             job.get("plan", []),
            "content_snippets": job.get("content_snippets", []),
            "review_score":     job.get("review_score"),
            "duration_ms":      job.get("duration_ms", 0),
        }
    if job["status"] == "error":
        return {"status": "error", "error": job.get("error", "未知错误")}
    return {"status": job["status"], "stage": job.get("stage", "waiting")}


# ── 健康检查（P-08）─────────────────────────────────────
@app.get("/health")
async def health():
    result: dict = {"status": "ok"}
    try:
        await redis_client.ping()
        result["redis"] = "ok"
    except Exception as e:
        result["redis"] = f"error: {e}"
        result["status"] = "degraded"

    result["deepseek_configured"] = bool(os.getenv("DEEPSEEK_API_KEY"))
    result["tavily_configured"]   = bool(os.getenv("TAVILY_API_KEY"))
    if not result["deepseek_configured"] or not result["tavily_configured"]:
        result["status"] = "degraded"

    return result

# 单 worker：uvicorn backend.main:app --host 0.0.0.0 --port 8000
# 多 worker：gunicorn backend.main:app -w 4 -k uvicorn.workers.UvicornWorker --bind 0.0.0.0:8000
