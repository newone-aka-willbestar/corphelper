# CorpHelper - 企业级分布式多Agent助手

适合应届生的企业级AI Agent项目，支持RAG + 多Agent协作 + Celery分布式任务队列。

## 快速启动
1. 复制 `.env.example` 为 `.env` 并填入 OPENAI_API_KEY
2. 在 `docs/` 文件夹放入企业文档（pdf/txt/md）
3. 执行：
   ```bash
   docker compose up --build --scale worker=2

---

### 运行步骤（超级简单）
1. 把上面所有文件按目录结构放好。
2. `docs/` 文件夹里放几份测试文档（例如新建 `company_policy.txt` 写点公司政策）。
3. `cp .env.example .env` 并填入你的 OpenAI Key。
4. 在项目根目录运行：
   ```bash
   docker compose up --build --scale worker=2




### 逻辑架构
（Streamlit UI） 
    ↓ HTTP POST
FastAPI Gateway (api.py) 
    ↓ Celery Task Queue (Redis Broker)
Celery Worker × N （分布式节点） 
    ↓ 执行 CrewAI 多Agent
        → RAG Tool（Chroma）检索企业文档
        → DuckDuckGo 外部搜索
    ↓ 返回报告
Redis Backend 存储结果 → Streamlit 轮询展示