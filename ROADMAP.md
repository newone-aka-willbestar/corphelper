# DeepInsight Agent 改造全流程记录

---

## 进度总览

| 阶段 | 项数 | 已完成 | 待处理 |
|------|------|--------|--------|
| 阶段一：问题诊断 | 10 | 10 | 0 |
| 阶段二：代码修复 | 9 | 9 | 0 |
| 阶段三：上线必做 | 3 | 3 | 0 |
| 阶段四：运维加固 | 5 | 5 | 0 |
| 阶段五：结构补全 | 5 | 5 | 0 |
| 阶段六：按需扩展 | 3 | 3 | 0 |
| **合计** | **35** | **35** | **0** |

---

## 阶段一：问题诊断

> 通过阅读全部源码，逐文件分析，整理出以下问题。

| # | 严重程度 | 所在文件 | 问题描述 | 根本原因 |
|---|----------|----------|----------|----------|
| D-01 | 🔴 严重 | `.env` | 真实 API 密钥（DeepSeek + Tavily）存在项目目录中，无 `.gitignore` 保护，存在被提交泄露风险 | 项目初始化时未创建 `.gitignore` |
| D-02 | 🔴 严重 | `agents/reviewer.py:16` | `if state.get("review_feedback") and state.get("steps", 0) == 1` 两个子条件同时为 False，整个分支永远不执行，"打回重搜"的核心迭代功能完全失效 | 初始状态 `review_feedback=""` 为 falsy；且经过 planner+researcher 后 `steps` 已是 2，不等于 1 |
| D-03 | 🔴 严重 | `graph.py:45` | 路由安全阀 `steps >= 3` 与迭代逻辑冲突：即使 D-02 修复后，reviewer 打回时 `steps=3` 也会触发安全阀直接结束，迭代仍无法进行 | 安全阀阈值设计时未考虑节点执行累计步数 |
| D-04 | 🟠 高 | `agents/researcher.py:28` | 每次搜索固定使用 `plan[0]`，无论迭代几轮都重复搜索同一方向，planner 生成的另外 2 个调研方向从未被使用 | 索引硬编码为 0，未考虑多轮迭代 |
| D-05 | 🟡 中 | `agents/reviewer.py:14` | `time.sleep(1.2)` 硬编码在业务节点内，每次请求无条件等待，纯粹浪费时间 | 开发阶段为演示动画加入，未在上线前移除 |
| D-06 | 🟡 中 | `agents/planner.py:21` | `import json` 写在函数体内部，每次调用都重复导入 | 不规范的代码习惯 |
| D-07 | 🟡 中 | `backend/main.py:41` | `BackgroundTasks` 出现在函数签名中但函数体内完全未使用，属于死代码 | 可能是复制模板后未清理 |
| D-08 | 🟡 中 | `backend/main.py:38` | 内存缓存 `research_cache: Dict` 无大小上限，长期运行持续增长，存在 OOM 风险 | 只有写入逻辑，没有淘汰逻辑 |
| D-09 | 🟡 中 | `app.py:63-82` | 前端用 `time.sleep` 在请求发出前就依次显示"Planner 正在工作…""Researcher 正在工作…"，实际上这些步骤是在后端同步完成的，UI 展示与真实执行顺序完全脱节 | 将视觉进度动画误设计为预先播放，而非事后展示 |
| D-10 | 🟡 中 | `.gitignore` | 文件内容为一段 PowerShell 脚本（历史误操作写入），完全无法起到保护作用 | 使用 PowerShell `Set-Content` 时将命令本身写入了文件 |

---

## 阶段二：代码修复

> 针对阶段一的每个问题逐一修复，以下记录修复方案和具体改动。

---

### Fix-01 · 修复 `.gitignore` 内容错误并补全保护规则
**对应问题：** D-01、D-10

**修复前：**
`.gitignore` 文件内容是一段 PowerShell 脚本，不起任何作用：
```
@"
.venv/
...
"@ | Set-Content -Path .gitignore -Encoding UTF8
```

**修复后：**
覆盖写入正确的 gitignore 规则：
```
.env
venv/
__pycache__/
*.pyc
*.pyo
.DS_Store
```

**改动文件：** `.gitignore`
**状态：** ✅ 已完成

---

### Fix-02 · 修复 reviewer 迭代条件永远为 False 的逻辑 Bug
**对应问题：** D-02

**修复前：**
```python
# agents/reviewer.py:16
if state.get("review_feedback") and state.get("steps", 0) == 1:
    return {"review_feedback": "...请研究员补充。", ...}
```
- 条件一：初始 `review_feedback=""` 为 falsy，短路，整个 `if` 不进入
- 条件二：即使条件一为真，`steps` 流经 planner+researcher 后已是 2，`== 1` 永远为 False
- 结果：reviewer 每次直接跳过打回，生成报告，核心迭代功能不存在

**修复后：**
```python
# 无反馈 = 首轮，打回要求补充
if not state.get("review_feedback"):
    return {"review_feedback": "...请研究员补充。", ...}
# 有反馈 = 已补充，生成报告
```

**改动文件：** `agents/reviewer.py`
**状态：** ✅ 已完成

---

### Fix-03 · 修复路由安全阀阈值与迭代逻辑冲突
**对应问题：** D-03

**修复前：**
```python
# graph.py:45
if state.get("review_feedback") == "合格" or state.get("steps", 0) >= 3:
    return "end"
```
完整执行路径中各节点步数累计：
```
planner(+1=1) → researcher(+1=2) → reviewer打回(+1=3) → 路由判断 steps>=3 → 直接END
```
reviewer 刚打回，路由就因 `steps=3` 强制结束，第二轮 researcher 永远跑不到。

**修复后：**
```python
# steps >= 6 作为兜底：最多允许 2 次完整迭代（每轮 researcher+reviewer 各+1）
if state.get("review_feedback") == "合格" or state.get("steps", 0) >= 6:
    return "end"
```
修复后完整流程：
```
planner(1) → researcher(2) → reviewer打回(3)
→ researcher(4) → reviewer生成报告(5, feedback="合格")
→ 路由判断 feedback=="合格" → END ✓
```

**改动文件：** `graph.py`
**状态：** ✅ 已完成

---

### Fix-04 · 修复 researcher 每轮重复搜索同一方向
**对应问题：** D-04

**修复前：**
```python
# agents/researcher.py:28
search_query = f"{topic} {plan[0] if plan else ''} 最新市场 数据 趋势 案例"
```
索引硬编码为 0，无论迭代几轮，始终只使用第一个调研方向。

**修复后：**
```python
# 用已积累的 content 条数推算当前是第几轮，轮换调研方向
idx = len(state.get("content", [])) % len(plan) if plan else 0
search_query = f"{topic} {plan[idx]} 最新市场 数据 趋势 案例"
```
第一轮使用 `plan[0]`，第二轮使用 `plan[1]`，充分利用 planner 拆分的所有方向。

**改动文件：** `agents/researcher.py`
**状态：** ✅ 已完成

---

### Fix-05 · 删除 reviewer 中的硬编码等待
**对应问题：** D-05

**修复前：**
```python
def reviewer_node(state):
    time.sleep(1.2)  # 演示动画用，生产环境可删除
    ...
```

**修复后：**
整行删除，同时移除顶部 `import time`。

**改动文件：** `agents/reviewer.py`
**状态：** ✅ 已完成

---

### Fix-06 · 将 import json 移到文件顶部
**对应问题：** D-06

**修复前：**
```python
def planner_node(state):
    ...
    try:
        import json   # ← 在函数体内
        directions = json.loads(res.content)["directions"]
```

**修复后：**
```python
import json   # ← 文件顶部，与其他 import 并列
import os
from langchain_core.messages import SystemMessage
...
```

**改动文件：** `agents/planner.py`
**状态：** ✅ 已完成

---

### Fix-07 · 删除未使用的 BackgroundTasks 参数
**对应问题：** D-07

**修复前：**
```python
from fastapi import FastAPI, HTTPException, BackgroundTasks
...
async def start_research(request: ResearchRequest, background_tasks: BackgroundTasks):
```

**修复后：**
```python
from fastapi import FastAPI, HTTPException
...
async def start_research(request: ResearchRequest):
```

**改动文件：** `backend/main.py`
**状态：** ✅ 已完成

---

### Fix-08 · 为内存缓存添加上限和 LRU 淘汰
**对应问题：** D-08

**修复前：**
```python
research_cache: Dict[str, ResearchResponse] = {}
# 只有写入，没有淘汰
research_cache[topic] = response
```

**修复后：**
```python
_MAX_CACHE = 100
research_cache: Dict[str, ResearchResponse] = {}

# 写入前检查容量，超限淘汰最旧的一条
if len(research_cache) >= _MAX_CACHE:
    oldest_key = next(iter(research_cache))
    del research_cache[oldest_key]
research_cache[topic] = response
```

**改动文件：** `backend/main.py`
**状态：** ✅ 已完成

---

### Fix-09 · 修复前端假进度，改为基于真实数据展示步骤
**对应问题：** D-09

**修复前：**
```python
with st.status("...", expanded=True) as status:
    st.write("Planner 正在工作...")
    time.sleep(1.5)                        # ← 假等待
    response = requests.post(...)          # ← 真正的请求
    st.write("Researcher 正在工作...")
    time.sleep(1.5)                        # ← 假等待
    st.write("Reviewer 正在审核...")
    time.sleep(2)                          # ← 假等待
    data = response.json()
    status.update(state="complete")
```
问题：三个"正在工作"都在请求发出前就显示完毕，用户看到的进度与实际执行顺序完全无关。

**修复后：**
```python
with st.status("...", expanded=True) as status:
    response = requests.post(...)          # ← 先等真实请求完成
    data = response.json()
    # 根据实际返回数据展示各阶段结果
    st.write("Planner：调研大纲已生成")
    st.write(f"Researcher：完成 {data['steps']} 轮数据检索")
    st.write("Reviewer：报告审核通过")
    status.update(state="complete")
```
进度信息在请求完成后展示，内容来自真实返回数据（如实际迭代轮数）。

**改动文件：** `app.py`
**状态：** ✅ 已完成

---

## 阶段三：上线必做（已完成）

| # | 改造项 | 状态 | 涉及文件 |
|---|--------|------|----------|
| P-01 | 异步任务模式 | ✅ 已完成 | `backend/main.py`, `app.py` |
| P-02 | LLM 和 Tavily 调用加重试 | ✅ 已完成 | `agents/planner.py`, `agents/researcher.py`, `agents/reviewer.py` |
| P-03 | IP 速率限制 | ✅ 已完成 | `backend/main.py` |

---

### Fix-P01 · 同步长请求改为异步任务 + 前端实时轮询
**对应问题：** 单次请求最长 60s，超过浏览器/代理默认超时；多 worker 下无法感知进度

**修复前：**
```
POST /research ──── 阻塞等待 60s ────► 返回完整结果
前端：time.sleep + 假动画
```

**修复后：**
```
POST /research ──► 立即返回 { job_id }（<100ms）
GET /research/{job_id} ──► 每 2s 轮询一次，返回实时阶段 stage
前端：根据后端返回的 stage 字段动态更新进度文案
```

后端使用 `asyncio.create_task` 在事件循环中异步运行 LangGraph；
用 `astream(stream_mode="updates")` 逐节点更新 `jobs[job_id]["stage"]`，
前端轮询时可拿到当前正在执行的 agent 名称（`planner` / `researcher` / `reviewer`）。

Job 存储加入 TTL（3600s）和 500 条上限，定期清理防内存泄漏。

> ⚠️ 当前 job 存储在进程内存中，多 worker 部署需先完成 P-04（Redis）。

**改动文件：** `backend/main.py`（全量重写）、`app.py`（轮询逻辑）

---

### Fix-P02 · LLM 和 Tavily 调用加重试
**对应问题：** 网络抖动或 API 限流时直接报错，无任何容错

**修复方案：**
在三个 agent 文件中各自抽出私有 `_invoke_llm` / `_search` 函数，
并统一装饰 `@retry`：

```python
from tenacity import retry, stop_after_attempt, wait_exponential

@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=10), reraise=True)
def _invoke_llm(messages):
    return llm.invoke(messages)
```

策略：最多重试 3 次，首次等 2s，每次翻倍，最长等 10s；全部失败后向上抛出异常。

**改动文件：** `agents/planner.py`、`agents/researcher.py`、`agents/reviewer.py`

---

### Fix-P03 · IP 速率限制（无需新依赖）
**对应问题：** 无限制调用会耗尽 DeepSeek/Tavily 配额，存在费用失控风险

**修复方案：**
在 `backend/main.py` 中用标准库实现滑动窗口限流，不引入 `slowapi` 等额外依赖：

```python
_rate_store: Dict[str, List[float]] = defaultdict(list)

def check_rate_limit(ip: str):
    now = time()
    _rate_store[ip] = [t for t in _rate_store[ip] if now - t < 60]  # 清理窗口外记录
    if len(_rate_store[ip]) >= 5:
        raise HTTPException(429, "请求过于频繁，每分钟最多 5 次")
    _rate_store[ip].append(now)
```

每次 `POST /research` 前调用，超限返回 HTTP 429。

**改动文件：** `backend/main.py`

---

## 阶段四：运维加固（已完成）

| # | 改造项 | 状态 | 涉及文件 |
|---|--------|------|----------|
| P-04 | Redis 替换进程内 dict 缓存 | ✅ 已完成 | `backend/main.py`, `requirements.txt` |
| P-05 | 结构化 JSON 日志 + LangSmith 配置 | ✅ 已完成 | `backend/main.py`, `.env.example` |
| P-06 | CORS 来源配置化 | ✅ 已完成 | `backend/main.py` |
| P-07 | Docker 化部署 | ✅ 已完成 | `Dockerfile`, `docker-compose.yml`, `.dockerignore` |
| P-08 | 健康检查实质化 + 前端 URL 外化 | ✅ 已完成 | `backend/main.py`, `app.py` |

---

### Fix-P04 · Redis 替换进程内 dict 缓存
**对应问题：** 多 worker 部署时各进程 job 存储互相隔离，跨进程轮询失效

**修复方案：**
引入 `redis.asyncio`，job 数据以 JSON 字符串存入 Redis，key 为 `job:{job_id}`，TTL 3600s：

```python
async def _job_save(job_id: str, data: dict):
    await redis_client.setex(f"job:{job_id}", _JOB_TTL, json.dumps(data))

async def _job_update(job_id: str, **fields):
    job = await _job_get(job_id) or {}
    job.update(fields)
    await _job_save(job_id, job)
```

启动时连接 Redis，连接失败直接抛出异常阻止服务启动，而非静默失败。

**新增文件：** `requirements.txt` 加入 `redis>=5.0.0`，`docker-compose.yml` 含 Redis 服务

---

### Fix-P05 · 结构化 JSON 日志 + LangSmith 配置
**对应问题：** 无日志，出错完全黑盒，无法定位是哪个 agent 节点失败

**修复方案：**
自定义 `_JSONFormatter`，每条日志输出为 JSON，包含时间戳、级别、消息、可选字段：

```python
{"ts": "2026-05-28T10:00:00", "level": "INFO", "msg": "节点完成: planner",
 "job_id": "abc123", "request_id": "f3a1", "duration_ms": 1240}
```

HTTP 中间件为每个请求注入 `request_id`（8 位 UUID 前缀），写入响应头 `X-Request-ID`，便于前后端日志关联。

LangSmith 链路追踪通过 `.env.example` 中的环境变量开启：
```
LANGCHAIN_TRACING_V2=true
LANGCHAIN_API_KEY=your-key
LANGCHAIN_PROJECT=deepinsight-agent
```

---

### Fix-P06 · CORS 来源配置化
**对应问题：** `allow_origins=["*"]` 允许任意域名跨域调用后端

**修复方案：**
从环境变量读取，默认仅允许本地 Streamlit：

```python
_ALLOWED_ORIGINS = os.getenv("ALLOWED_ORIGINS", "http://localhost:8501").split(",")
app.add_middleware(CORSMiddleware, allow_origins=_ALLOWED_ORIGINS, ...)
```

生产部署时在 `.env` 或 `docker-compose.yml` 中设置 `ALLOWED_ORIGINS=https://yourdomain.com`。

---

### Fix-P07 · Docker 化部署
**对应问题：** 无容器化配置，依赖手动环境安装；裸 `uvicorn` 无进程守护

**新增文件：**

`Dockerfile`：Python 3.11-slim 镜像，先复制 `requirements.txt` 再复制源码（利用层缓存），
生产模式用 `gunicorn + UvicornWorker` 启动 4 个 worker：
```dockerfile
CMD ["gunicorn", "backend.main:app", "--workers", "4",
     "--worker-class", "uvicorn.workers.UvicornWorker", "--bind", "0.0.0.0:8000"]
```

`docker-compose.yml`：包含 `redis`（7-alpine）和 `backend` 两个服务，
backend 的 `REDIS_URL` 自动指向 compose 内网的 redis 容器，
redis 配置健康检查，backend 等健康检查通过后再启动：
```yaml
depends_on:
  redis:
    condition: service_healthy
```

`.dockerignore`：排除 `.env`、`venv/`、`.git/`，避免敏感文件和大体积目录进入镜像。

---

### Fix-P08 · 健康检查实质化 + 前端 URL 外化
**对应问题：** `/health` 只返回硬编码 ok；前端后端地址硬编码

**修复方案：**

`/health` 实际探测 Redis 连通性并检查关键环境变量是否配置（不发起 LLM 请求，避免产生费用）：
```json
{"status": "ok", "redis": "ok", "deepseek_configured": true, "tavily_configured": true}
```
任一项异常则 `status` 返回 `"degraded"`，负载均衡器可据此摘除故障节点。

前端 `BACKEND_URL` 已在阶段三（P-01）中外化为环境变量，此项合并完成。

---

## 阶段五：结构补全（已完成）

| # | 改造项 | 状态 | 涉及文件 |
|---|--------|------|----------|
| S-01 | 创建 `agents/__init__.py` 和 `backend/__init__.py`，确保包导入正确 | ✅ 已完成 | `agents/__init__.py`, `backend/__init__.py` |
| S-02 | 三个 agent 加 `timeout=90`，防止 LLM 调用无限挂起 | ✅ 已完成 | `agents/*.py` |
| S-03 | 各 agent 通过 `response_metadata` 记录每次 LLM 调用的 token 用量 | ✅ 已完成 | `agents/*.py` |
| S-04 | FastAPI `on_event` 迁移到 `lifespan`；`TavilySearchResults` 升级到 `langchain_tavily.TavilySearch` | ✅ 已完成 | `backend/main.py`, `agents/researcher.py` |
| S-05 | 将 Streamlit 纳入 `docker-compose.yml`，完整栈一键启动 | ✅ 已完成 | `docker-compose.yml` |

---

### Fix-S01～S05 · 结构补全说明

**S-01 包初始化**：`agents/` 和 `backend/` 缺少 `__init__.py`，Python 在某些调用方式下无法正确识别为包，补全后 `uvicorn backend.main:app` 和 `pytest` 均可稳定运行。

**S-02 LLM 超时**：`ChatOpenAI(timeout=90)` 防止 DeepSeek 接口无响应时请求永远挂起，超时后 tenacity 重试机制接管。

**S-03 Token 日志**：每次 `_invoke_llm` 调用后读取 `res.response_metadata["token_usage"]`，以 JSON 格式记录 `prompt_tokens` 和 `completion_tokens`，可接入任意日志聚合工具（ELK、Loki、CloudWatch）做费用监控。

**S-04 弃用 API 清理**：`on_event` 改为 `lifespan`（FastAPI 0.93+ 推荐方式）；`TavilySearchResults` 升级为官方最新 `langchain_tavily.TavilySearch`，消除全部 DeprecationWarning，测试以 `-W error::DeprecationWarning` 模式验证。

**S-05 前端 Docker 化**：`docker-compose.yml` 新增 `frontend` 服务，复用相同镜像，`BACKEND_URL` 指向 compose 内网 `http://backend:8000`，`docker-compose up --build -d` 一条命令启动全栈。

---

## 阶段六：按需扩展（已完成）

| # | 改造项 | 状态 | 涉及文件 |
|---|--------|------|----------|
| P-09 | API Key 认证（可选，通过环境变量开关） | ✅ 已完成 | `backend/main.py`, `app.py` |
| P-10 | Token 用量统计 + 每日预算告警 | ✅ 已完成 | `backend/main.py` |
| P-11 | 20 个单元测试（mock LLM / Tavily，0 外部依赖） | ✅ 已完成 | `tests/` |

---

### Fix-P09 · API Key 认证（可选开关）
**方案：** FastAPI `Depends` 注入 `_verify_api_key`，从请求头 `X-API-Key` 读取并与 `API_KEY` 环境变量比对。若 `API_KEY` 未设置，跳过校验（开发模式）；设置后自动生效（生产模式），无需改代码。

前端 `app.py` 同步读取 `API_KEY` 环境变量，通过 `_HEADERS` 自动携带。

---

### Fix-P10 · Token 用量统计 + 每日预算告警
**方案：**
- 各 agent `_invoke_llm` 函数记录每次调用的 token 数到结构化日志
- 后端每个 job 完成后调用 `_track_daily_tokens`，用 Redis 累计当天 token 总量
- 超过 `DAILY_TOKEN_BUDGET`（默认 500,000）时以 `WARNING` 级别告警
- 按 DeepSeek-V3 定价计算估算成本（`$0.14/M` 输入 + `$0.28/M` 输出）

---

### Fix-P11 · 单元测试（20 个，全 mock）

| 测试文件 | 覆盖内容 | 用例数 |
|----------|----------|--------|
| `tests/test_agents.py` | planner 解析、fallback；researcher 方向轮换、步骤标签；reviewer 打回、生成报告 | 7 |
| `tests/test_api.py` | 输入校验（空/空白/超长/正常）、速率限制（允许/拦截/窗口重置） | 8 |
| `tests/test_graph.py` | 路由逻辑（合格退出/继续/安全阀触发） | 5 |

运行命令：`pytest tests/ -v`，耗时 ~4s，无需任何外部服务。

---

## 架构演进

### 当前架构

```
用户浏览器
    │  HTTP POST /research（同步阻塞，最长 60s）
    ▼
Streamlit (app.py)
    │
    ▼
FastAPI (backend/main.py)  ←── 进程内 dict 缓存
    │
    ▼
LangGraph (graph.py)
    ├─ planner_node   → DeepSeek LLM
    ├─ researcher_node → Tavily + DeepSeek LLM
    └─ reviewer_node  → DeepSeek LLM
```

### 目标架构（阶段三完成后）

```
用户浏览器
    │  POST /research → job_id（立即返回）
    │  GET  /research/{job_id} → 轮询（2s/次）
    ▼
Streamlit (app.py)
    │
    ▼
FastAPI (backend/main.py)
    │  任务入队                  ←── Redis 共享缓存
    ▼
异步任务队列（asyncio / Celery）
    │
    ▼
LangGraph (graph.py)
    ├─ planner_node   → DeepSeek LLM（tenacity 重试）
    ├─ researcher_node → Tavily + DeepSeek LLM（tenacity 重试）
    └─ reviewer_node  → DeepSeek LLM（tenacity 重试）
```
