from langchain_openai import ChatOpenAI
from crewai import Agent, Task, Crew, Process
from tools.rag_tool import company_knowledge_search
from langchain_community.tools import DuckDuckGoSearchRun

llm = ChatOpenAI(model="gpt-4o-mini", temperature=0.7)

search_tool = DuckDuckGoSearchRun()

def create_crew(query: str):
    researcher = Agent(
        role="企业知识检索专员",
        goal="精准从公司文档和外部来源收集信息",
        backstory="你是公司知识库专家",
        tools=[company_knowledge_search, search_tool],
        llm=llm,
        verbose=True
    )

    analyst = Agent(
        role="业务分析专员",
        goal="提炼关键洞见和数据支撑",
        backstory="你是资深分析师",
        llm=llm,
        verbose=True
    )

    writer = Agent(
        role="报告撰写专员",
        goal="撰写专业的企业级报告",
        backstory="你是公司官方报告撰写人",
        llm=llm,
        verbose=True
    )

    task1 = Task(
        description=f"针对查询 '{query}'，使用RAG和搜索工具收集信息",
        expected_output="结构化信息列表（来源+关键点）",
        agent=researcher
    )

    task2 = Task(
        description="基于检索结果进行深入分析，找出3-5个核心观点",
        expected_output="分析报告",
        agent=analyst,
        context=[task1]
    )

    task3 = Task(
        description="生成一份专业、清晰的企业报告（控制在600字以内）",
        expected_output="完整的Markdown格式企业报告",
        agent=writer,
        context=[task1, task2]
    )

    crew = Crew(
        agents=[researcher, analyst, writer],
        tasks=[task1, task2, task3],
        process=Process.sequential,
        verbose=True
    )
    return crew