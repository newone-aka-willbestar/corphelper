FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000 8501

# 根据 SERVICE 环境变量决定启动哪个服务
CMD ["sh", "-c", "if [ \"$SERVICE\" = \"api\" ]; then uvicorn api:app --host 0.0.0.0 --port 8000; elif [ \"$SERVICE\" = \"worker\" ]; then celery -A tasks worker --loglevel=info --concurrency=4; elif [ \"$SERVICE\" = \"streamlit\" ]; then streamlit run streamlit_app.py --server.port 8501 --server.address 0.0.0.0; fi"]