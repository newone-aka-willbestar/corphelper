from celery import Celery
from crew import create_crew
from dotenv import load_dotenv
import os

load_dotenv()

celery_app = Celery(
    "corphelper",
    broker="redis://redis:6379/0",
    backend="redis://redis:6379/1"
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="Asia/Shanghai",
)

@celery_app.task(name="corphelper.run_crew_task")
def run_crew_task(query: str):
    """在Worker中执行CrewAI任务"""
    crew = create_crew(query)
    result = crew.kickoff(inputs={"query": query})
    return str(result)