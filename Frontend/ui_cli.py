import json
import os
import uuid

import requests
import streamlit as st

st.set_page_config(
    page_title="可定制化 Agent",
    page_icon="🤖",
    layout="wide",
)

BACKEND_BASE_URL = os.environ.get("BACKEND_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
BACKEND_STREAM_URL = f"{BACKEND_BASE_URL}/api/chat/stream"
BACKEND_CANCEL_URL = f"{BACKEND_BASE_URL}/api/chat/cancel"
BACKEND_CONFIRM_URL = f"{BACKEND_BASE_URL}/api/chat/confirm"

with st.sidebar:
    st.title("⚙️ 控制面板")

    if "session_id" not in st.session_state:
        st.session_state.session_id = f"user_{uuid.uuid4().hex[:8]}"

    if "awaiting_confirmation" not in st.session_state:
        st.session_state.awaiting_confirmation = False

    st.markdown(f"**当前会话 ID:** `{st.session_state.session_id}`")

    if st.button("➕ 开启新对话", use_container_width=True):
        st.session_state.session_id = f"user_{uuid.uuid4().hex[:8]}"
        st.session_state.messages = []
        st.session_state.awaiting_confirmation = False
        st.rerun()

    st.markdown("---")
    st.markdown("""
    **💡 功能示例：**
    1. **查规则/故障 (RAG)**：
       * *扫地机器人提示“电池故障”怎么办？*
       * *它是如何实现自主导航的？*
    2. **查实时状态 (SQL/设备)**：
       * *帮我查询设备 `SN1001` 的保修期*
       * *查一下 `SN2002` 的磨损情况*
    3. **复合场景 (多跳规划)**：
       * *设备 SN1001 出现异响，帮我看看保修期并指导维修*
    """)

if "messages" not in st.session_state:
    st.session_state.messages = [
        {"role": "assistant", "content": "你好！我是部署在企业内部的可定制化 AI 助手，可以问我设备状态、保修期或故障排查。"}
    ]

for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])

if st.session_state.awaiting_confirmation:
    st.warning("⚠️ 检测到敏感写操作，请确认是否允许 Agent 修改数据库/文件？")
    col1, col2 = st.columns(2)
    with col1:
        if st.button("✅ 允许写入", use_container_width=True):
            resp = requests.post(BACKEND_CONFIRM_URL, json={"session_id": st.session_state.session_id, "action": "approve"}, timeout=120).json()
            if resp.get("status") == "interrupted":
                st.session_state.awaiting_confirmation = True
            else:
                st.session_state.awaiting_confirmation = False
            st.session_state.messages.append({"role": "assistant", "content": resp["message"]})
            st.rerun()
    with col2:
        if st.button("❌ 拒绝操作", use_container_width=True):
            requests.post(BACKEND_CONFIRM_URL, json={"session_id": st.session_state.session_id, "action": "reject"}, timeout=30)
            st.session_state.awaiting_confirmation = False
            st.session_state.messages.append({"role": "assistant", "content": "*(操作已拒绝)*"})
            st.rerun()

elif user_input := st.chat_input("请输入您的问题或设备 SN 码..."):
    st.session_state.messages.append({"role": "user", "content": user_input})
    with st.chat_message("user"):
        st.markdown(user_input)

    with st.chat_message("assistant"):
        response_placeholder = st.empty()
        full_response = ""

        payload = {
            "message": user_input,
            "session_id": st.session_state.session_id,
        }

        try:
            with requests.post(BACKEND_STREAM_URL, json=payload, stream=True, timeout=120) as resp:
                if resp.status_code != 200:
                    st.error(f"后端接口异常，状态码: {resp.status_code}")
                else:
                    for line in resp.iter_lines():
                        if line:
                            line_str = line.decode("utf-8")
                            if line_str.startswith("data: "):
                                data = json.loads(line_str[6:])
                                data_type = data.get("type")
                                content = data.get("content", "")

                                if data_type in ("tool", "token"):
                                    full_response += content
                                    response_placeholder.markdown(full_response + "▌")
                                elif data_type == "interrupt":
                                    st.session_state.awaiting_confirmation = True
                                    st.rerun()
                                elif data_type == "error":
                                    st.error(f"调用出错: {content}")
                                    break
                                elif data_type == "done":
                                    break

                    response_placeholder.markdown(full_response)
                    st.session_state.messages.append({"role": "assistant", "content": full_response})

        except requests.exceptions.ConnectionError:
            st.error("无法连接到后端服务，请确认 `server.py` 是否已在 8000 端口启动。")
        except Exception as e:
            st.error(f"请求失败: {str(e)}")
