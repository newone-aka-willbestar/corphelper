import os

import requests
import streamlit as st
import time

BACKEND_URL = os.getenv("BACKEND_URL", "http://127.0.0.1:8000")
_API_KEY    = os.getenv("API_KEY", "")
_HEADERS    = {"X-API-Key": _API_KEY} if _API_KEY else {}

# 1. 页面配置与专业主题
st.set_page_config(
    page_title="DeepInsight | 多智能体协作系统",
    page_icon="🤖",
    layout="wide",
    initial_sidebar_state="expanded"
)

# 自定义 CSS 样式（让界面看起来更像企业级工具）
st.markdown("""
    <style>
    .main { background-color: #f8f9fa; }
    .stButton>button { width: 100%; border-radius: 5px; height: 3em; background-color: #007bff; color: white; }
    .report-card { background-color: white; padding: 25px; border-radius: 10px; box-shadow: 0 2px 10px rgba(0,0,0,0.05); }
    .status-text { font-size: 0.9em; color: #6c757d; }
    </style>
""", unsafe_allow_html=True)

# 2. 侧边栏配置：运行状态统计
with st.sidebar:
    st.image("https://cdn-icons-png.flaticon.com/512/2103/2103633.png", width=80)
    st.title("DeepInsight Agent")
    st.info("当前引擎: DeepSeek-V3 + Tavily Search")
    
    st.divider()
    st.subheader("🛠️ 技术栈")
    st.caption("Framework: LangGraph 1.x")
    st.caption("Backend: FastAPI (Async)")
    st.caption("Architecture: Planner-Researcher-Reviewer")

# 3. 主界面布局
st.title("🔍 企业级多智能体数据洞察助手")
st.markdown("---")

# 输入区域
topic = st.text_input(
    "调研主题", 
    placeholder="请输入你想深入研究的内容，例如：2025年中国固态电池产业竞争格局",
    help="支持任何垂直行业或技术方向的深度调研"
)

if st.button("🚀 启动多智能体协作分析"):
    if not topic:
        st.warning("请先输入调研主题")
        st.stop()

    # ── 第一步：提交任务，立即拿到 job_id ────────────────
    try:
        resp = requests.post(
            f"{BACKEND_URL}/research",
            json={"topic": topic},
            headers=_HEADERS,
            timeout=10
        )
        resp.raise_for_status()
    except requests.exceptions.ConnectionError:
        st.error("无法连接到后端，请确认 FastAPI 服务已启动。")
        st.stop()
    except requests.exceptions.HTTPError as e:
        st.error(f"提交失败：{e.response.status_code} — {e.response.json().get('detail', '')}")
        st.stop()
    except Exception as e:
        st.error(f"请求异常：{e}")
        st.stop()

    job_id = resp.json()["job_id"]
    start_time = time.time()

    # 各 agent 阶段对应的提示文案
    STAGE_LABELS = {
        "waiting":    "⏳ 等待调度...",
        "pending":    "⏳ 等待调度...",
        "planner":    "📝 **Planner**：正在解析主题，拆解调研方向...",
        "researcher": "🌐 **Researcher**：正在调用 Tavily 检索实时数据...",
        "reviewer":   "🧐 **Reviewer**：正在审核报告质量...",
    }

    # ── 第二步：轮询进度，每 2 秒刷新一次 ───────────────
    data = None
    with st.status("🛸 Agent 团队正在联合作业...", expanded=True) as status:
        stage_placeholder = st.empty()
        last_stage = ""

        while True:
            try:
                poll = requests.get(f"{BACKEND_URL}/research/{job_id}", headers=_HEADERS, timeout=10)
                poll_data = poll.json()
            except Exception as e:
                status.update(label="❌ 轮询失败", state="error")
                st.error(f"轮询出错：{e}")
                st.stop()

            current_stage = poll_data.get("stage", poll_data.get("status", ""))
            if current_stage != last_stage and current_stage in STAGE_LABELS:
                stage_placeholder.write(STAGE_LABELS[current_stage])
                last_stage = current_stage

            if poll_data["status"] == "done":
                data = poll_data
                break
            if poll_data["status"] == "error":
                status.update(label="❌ Agent 执行失败", state="error")
                st.error(f"错误详情：{poll_data.get('error', '未知错误')}")
                st.stop()

            time.sleep(2)

        end_time = time.time()
        stage_placeholder.write("✅ 所有 Agent 节点执行完毕")
        status.update(label="✨ 调研任务已圆满完成！", state="complete", expanded=False)
        st.toast("分析成功！", icon='✅')

    # ── 第三步：展示结果 ─────────────────────────────────
    st.markdown("### 📊 调研分析结果")
    tab1, tab2, tab3 = st.tabs(["📝 深度研报全文", "📋 调研大纲", "📚 搜索素材摘要"])

    with tab1:
        st.markdown('<div class="report-card">', unsafe_allow_html=True)
        st.markdown(data["report"])
        st.markdown('</div>', unsafe_allow_html=True)
        st.download_button(
            label="📥 下载 Markdown 报告",
            data=data["report"],
            file_name=f"{topic}_调研报告.md",
            mime="text/markdown"
        )

    with tab2:
        st.success("Planner 生成的调研逻辑链：")
        for i, p in enumerate(data["plan"], 1):
            st.write(f"**{i}.** {p}")

    with tab3:
        st.info("以下为 Researcher 采集并由 LLM 总结的原始素材：")
        for snippet in data.get("content_snippets", []):
            with st.expander("查看详情"):
                st.write(snippet)

    st.write("---")
    review_score = data.get("review_score")
    cols = st.columns(5)
    cols[0].write(f"⏱️ **总耗时**: {int(end_time - start_time)}s")
    cols[1].write(f"🔄 **迭代次数**: {data['steps']} 轮")
    cols[2].write(f"🧐 **评审评分**: {f'{review_score}/10' if review_score is not None else '—'}")
    cols[3].write(f"📏 **字数统计**: {len(data['report'])} 字")
    cols[4].write(f"🔗 **数据源**: Tavily Web Search")