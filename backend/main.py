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
from fastapi.responses import StreamingResponse
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from pydantic import BaseModel, field_validator

load_dotenv()

from graph import workflow

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

# ── LangGraph Checkpointer：任务断点续跑 ─────────────────
# SQLite 单文件适合单机部署（4 worker 共享同一文件，每个 job 只有一个写方）；
# 集群部署可平滑替换为 Postgres checkpointer。
_CHECKPOINT_DB = os.getenv("CHECKPOINT_DB", "checkpoints.sqlite")
agent_app = None
_checkpoint_cm = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    global redis_client, agent_app, _checkpoint_cm
    redis_client = aioredis.from_url(_REDIS_URL, decode_responses=True)
    try:
        await redis_client.ping()
        logger.info("Redis 连接成功: %s", _REDIS_URL)
    except Exception as e:
        logger.error("Redis 连接失败: %s", e)
        raise RuntimeError(f"Redis 不可达: {e}")

    _checkpoint_cm = AsyncSqliteSaver.from_conn_string(_CHECKPOINT_DB)
    saver = await _checkpoint_cm.__aenter__()
    agent_app = workflow.compile(checkpointer=saver)
    logger.info("Checkpointer 已启用: %s", _CHECKPOINT_DB)

    # 进程重启后接管未完成的任务（从最近的检查点续跑）
    asyncio.create_task(_resume_interrupted_jobs())
    yield
    if _checkpoint_cm:
        await _checkpoint_cm.__aexit__(None, None, None)
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

# ── SSE 事件发布（Redis Pub/Sub）─────────────────────────
async def _publish_event(job_id: str, **event):
    await redis_client.publish(f"job:{job_id}:events", json.dumps(event, ensure_ascii=False))

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

async def _run_research(job_id: str, topic: str, resume: bool = False):
    await _job_update(job_id, status="running")
    config = {"configurable": {"thread_id": job_id}}
    accumulated = {"plan": [], "content": [], "report": "", "review_feedback": "", "steps": 0}
    graph_input = {"topic": topic, **accumulated}
    t0 = time()

    try:
        if resume:
            snapshot = await agent_app.aget_state(config)
            if snapshot and snapshot.values:
                # 有检查点：输入 None 从上次中断的节点继续，已积累状态从检查点取回
                graph_input = None
                accumulated.update(snapshot.values)
                logger.info("从检查点恢复，已执行 %s 步", snapshot.values.get("steps", 0),
                            extra={"job_id": job_id})

        async for chunk in agent_app.astream(graph_input, config, stream_mode="updates"):
            node_name = next(iter(chunk))
            await _job_update(job_id, stage=node_name)
            await _publish_event(job_id, type="stage", stage=node_name)
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
        await _publish_event(
            job_id,
            type="done",
            report=accumulated.get("report", "生成失败"),
            steps=accumulated.get("steps", 0),
            review_score=accumulated.get("review_score"),
            duration_ms=duration_ms,
        )
        logger.info("任务完成，耗时 %dms", duration_ms, extra={"job_id": job_id, "duration_ms": duration_ms})

        # token 追踪需要从节点 response_metadata 中收集，此处使用估算（可通过 LangSmith 获取精确值）
        # 如需精确统计，在 .env 中配置 LANGCHAIN_TRACING_V2=true

    except Exception as e:
        logger.error("任务失败: %s", e, extra={"job_id": job_id})
        await _job_update(job_id, status="error", stage="failed", error=str(e))
        await _publish_event(job_id, type="error", error=str(e))


async def _resume_interrupted_jobs():
    """进程启动时扫描 Redis 中状态为 pending/running 的任务并接管续跑"""
    async for key in redis_client.scan_iter("job:*"):
        job_id = key.split(":", 1)[1]
        job = await _job_get(job_id)
        if not job or job.get("status") not in ("pending", "running"):
            continue
        topic = job.get("topic")
        if not topic:
            continue
        # NX 锁保证多 worker 同时启动时每个任务只被一个进程接管
        acquired = await redis_client.set(f"resume:{job_id}", "1", nx=True, ex=600)
        if not acquired:
            continue
        logger.info("接管中断任务", extra={"job_id": job_id})
        asyncio.create_task(_run_research(job_id, topic, resume=True))

# ── 路由 ────────────────────────────────────────────────
@app.post("/research", dependencies=[Depends(_verify_api_key)])
async def start_research(request: Request, body: ResearchRequest):
    ip = request.client.host
    await _check_rate_limit(ip)

    job_id = str(uuid.uuid4())
    # topic 持久化到 job，进程重启后续跑需要
    await _job_update(job_id, status="pending", stage="waiting", topic=body.topic, created_at=time())
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


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _final_event(job: dict) -> dict:
    if job["status"] == "error":
        return {"type": "error", "error": job.get("error", "未知错误")}
    return {
        "type":         "done",
        "report":       job.get("report", ""),
        "steps":        job.get("steps", 0),
        "review_score": job.get("review_score"),
        "duration_ms":  job.get("duration_ms", 0),
    }


@app.get("/research/{job_id}/stream", dependencies=[Depends(_verify_api_key)])
async def stream_research(job_id: str):
    """SSE 实时进度流：逐节点推送 stage 事件，结束时推送 done/error 事件"""
    job = await _job_get(job_id)
    if not job:
        raise HTTPException(404, "任务不存在或已过期")

    async def event_stream():
        # 终态任务直接吐最终事件
        if job["status"] in ("done", "error"):
            yield _sse(_final_event(job))
            return

        pubsub = redis_client.pubsub()
        await pubsub.subscribe(f"job:{job_id}:events")
        try:
            yield _sse({"type": "stage", "stage": job.get("stage", "waiting")})
            idle = 0.0
            while True:
                msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=2.0)
                if msg:
                    idle = 0.0
                    yield f"data: {msg['data']}\n\n"
                    if json.loads(msg["data"]).get("type") in ("done", "error"):
                        return
                    continue
                # 订阅建立前任务可能已结束，错过终态事件 → 回查状态兜底
                current = await _job_get(job_id)
                if not current:
                    yield _sse({"type": "error", "error": "任务已过期"})
                    return
                if current["status"] in ("done", "error"):
                    yield _sse(_final_event(current))
                    return
                idle += 2.0
                if idle >= 10.0:
                    idle = 0.0
                    yield ": keepalive\n\n"
        finally:
            await pubsub.unsubscribe(f"job:{job_id}:events")
            await pubsub.aclose()

    return StreamingResponse(event_stream(), media_type="text/event-stream")


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
