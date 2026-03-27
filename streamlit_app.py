import streamlit as st
import requests
import time

st.set_page_config(page_title="CorpHelper", page_icon="🏢", layout="wide")
st.title("🏢 CorpHelper - 企业分布式智能助手")

query = st.text_area("请输入你的任务需求：", height=120, placeholder="例如：生成2026年Q2 AI工具应用趋势报告...")

if st.button("🚀 生成报告", type="primary"):
    if query:
        with st.spinner("任务已进入分布式队列，多Worker正在协作处理..."):
            response = requests.post("http://api:8000/generate", json={"query": query})
            data = response.json()
            task_id = data["task_id"]

            # 轮询结果
            for _ in range(60):
                result_resp = requests.get(f"http://api:8000/result/{task_id}")
                result_data = result_resp.json()
                if result_data.get("status") == "completed":
                    st.success("报告生成完成！")
                    st.markdown("### 生成报告")
                    st.markdown(result_data["report"])
                    break
                time.sleep(2)
            else:
                st.warning("处理时间较长，请稍后查询任务ID: " + task_id)
    else:
        st.warning("请输入查询内容")