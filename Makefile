.PHONY: backend frontend up down logs test

# ── 本地开发（需先手动启动 Redis） ───────────────────────
backend:
	uvicorn backend.main:app --host 0.0.0.0 --port 8000 --reload

frontend:
	streamlit run app.py

# ── Docker 完整部署 ───────────────────────────────────────
up:
	docker-compose up --build -d

down:
	docker-compose down

logs:
	docker-compose logs -f backend

# ── 测试 ────────────────────────────────────────────────
test:
	pytest tests/ -v

install-dev:
	pip install -r requirements-dev.txt
