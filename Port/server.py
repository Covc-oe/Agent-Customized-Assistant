import asyncio
import json
import os
from contextlib import asynccontextmanager
from typing import AsyncGenerator, Dict

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage, ToolMessage
from pydantic import BaseModel, Field

from Agent.agent import app, _ensure_tools_initialized, checkpointer, redis_client


@asynccontextmanager
async def lifespan(_: FastAPI):
    await _ensure_tools_initialized()
    await checkpointer.asetup()
    yield
    await redis_client.aclose()


server = FastAPI(
    title="可定制化 Agent 服务",
    description="基于 LangGraph + RAG + FastAPI 的企业级接口",
    version="1.0.1",
    lifespan=lifespan,
)

_origins = [
    o.strip()
    for o in os.environ.get(
        "CORS_ORIGINS", "http://127.0.0.1:8501,http://localhost:8501"
    ).split(",")
    if o.strip()
]

server.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)

ACTIVE_TASKS: Dict[str, asyncio.Task] = {}


class ChatRequest(BaseModel):
    message: str = Field(..., description="用户输入的提问内容", example="扫地机器人提示电池故障怎么办？")
    session_id: str = Field(default="default_session", description="用于追踪多轮对话的会话ID",
                            example="user_1001")


class CancelRequest(BaseModel):
    session_id: str = Field(..., description="要取消的会话ID")


class ChatResponse(BaseModel):
    session_id: str
    reply: str


class ConfirmRequest(BaseModel):
    session_id: str = Field(..., description="会话ID")
    action: str = Field(..., description="用户动作：'approve' 或 'reject'")


async def generate_agent_stream(query: str, session_id: str) -> AsyncGenerator[str, None]:
    """处理 LangGraph 异步流式输出（SSE）。"""
    config = {"configurable": {"thread_id": session_id}}
    input_message = {"messages": [HumanMessage(content=query)]}

    try:
        has_streamed_token = False

        async for event in app.astream_events(input_message, config=config, version="v2"):
            event_type = event["event"]

            if event_type == "on_tool_start":
                tool_name = event.get("name", "工具")
                tool_msg = f"\n> 🛠️ *[系统提示: 正在调用工具 `{tool_name}` 查询数据...]*\n\n"
                yield f"data: {json.dumps({'type': 'tool', 'content': tool_msg}, ensure_ascii=False)}\n\n"

            elif event_type == "on_chat_model_stream":
                chunk = event["data"].get("chunk")
                if isinstance(chunk, AIMessageChunk) and chunk.content:
                    has_streamed_token = True
                    payload = json.dumps({'type': 'token', 'content': chunk.content}, ensure_ascii=False)
                    yield f"data: {payload}\n\n"

            elif event_type == "on_chain_end" and event.get("name") == "agent":
                output = event["data"].get("output")
                if output and "messages" in output and not has_streamed_token:
                    for msg in output["messages"]:
                        if isinstance(msg, AIMessage) and msg.content:
                            payload = json.dumps({'type': 'token', 'content': msg.content}, ensure_ascii=False)
                            yield f"data: {payload}\n\n"

            await asyncio.sleep(0.001)

        state = await app.aget_state(config)
        if state.next:
            interrupt_msg = "⚠️ 检测到敏感写操作，请确认是否继续执行？"
            yield f"data: {json.dumps({'type': 'interrupt', 'content': interrupt_msg}, ensure_ascii=False)}\n\n"
            return

        yield f"data: {json.dumps({'type': 'done', 'content': ''})}\n\n"

    except asyncio.CancelledError:
        payload = json.dumps(
            {"type": "cancelled", "content": "\n\n⚠️ *[任务已由用户手动取消]*"},
            ensure_ascii=False,
        )
        yield f"data: {payload}\n\n"
    except Exception as e:
        error_msg = f"处理异常: {str(e)}"
        yield f"data: {json.dumps({'type': 'error', 'content': error_msg}, ensure_ascii=False)}\n\n"


@server.get("/healthz")
async def healthz():
    """存活探针：供 docker-compose healthcheck / K8s liveness 使用。"""
    return {"status": "ok"}


@server.post("/api/chat/stream")
async def chat_stream_endpoint(request: ChatRequest, req: Request):
    if not request.message.strip():
        raise HTTPException(status_code=400, detail="消息内容不能为空")

    config = {"configurable": {"thread_id": request.session_id}}
    state = await app.aget_state(config)
    if state.next:
        async def error_stream():
            yield f"data: {json.dumps({'type': 'error', 'content': '当前有待确认的敏感操作。请先点击【允许/拒绝】，或开启新对话！'}, ensure_ascii=False)}\n\n"

        return StreamingResponse(error_stream(), media_type="text/event-stream")

    if request.session_id in ACTIVE_TASKS:
        old_task = ACTIVE_TASKS[request.session_id]
        if not old_task.done():
            old_task.cancel()

    async def event_wrapper():
        current_task = asyncio.current_task()
        ACTIVE_TASKS[request.session_id] = current_task
        try:
            async for chunk in generate_agent_stream(request.message, request.session_id):
                if await req.is_disconnected():
                    current_task.cancel()
                    break
                yield chunk
        finally:
            if ACTIVE_TASKS.get(request.session_id) is current_task:
                ACTIVE_TASKS.pop(request.session_id, None)

    return StreamingResponse(event_wrapper(), media_type="text/event-stream")


@server.post("/api/chat/confirm")
async def confirm_action_endpoint(request: ConfirmRequest):
    """处理用户的敏感操作确认/拒绝，并唤醒挂起的 Agent。"""
    config = {"configurable": {"thread_id": request.session_id}}
    state = await app.aget_state(config)

    if not state.next:
        return {"status": "error", "message": "当前会话没有挂起的待确认任务"}

    if request.action == "approve":
        result = await app.ainvoke(None, config=config)

        new_state = await app.aget_state(config)
        if new_state.next:
            return {"status": "interrupted",
                    "message": "*(Agent 调整了参数并自动发起了第二次工具调用，已再次拦截，请根据界面提示重新授权)*"}
        final_reply = result["messages"][-1].content
        if not final_reply:
            final_reply = "*(操作已成功执行，Agent 未返回额外说明)*"
        return {"status": "ok", "message": final_reply}

    elif request.action == "reject":
        last_message = state.values["messages"][-1]
        tool_results = []
        for tool_call in getattr(last_message, "tool_calls", []):
            tool_results.append(
                ToolMessage(
                    content="执行失败：用户已拒绝此敏感操作。",
                    tool_call_id=tool_call["id"],
                    name=tool_call["name"],
                )
            )
        await app.aupdate_state(config, {"messages": tool_results}, as_node="sensitive_tools")
        await app.ainvoke(None, config=config)
        return {"status": "ok", "message": "操作已拒绝"}

    return {"status": "error", "message": "未知的 action"}


@server.post("/api/chat", response_model=ChatResponse)
async def chat_endpoint(request: ChatRequest):
    """标准阻塞接口：等待 Agent 完整执行后一次性返回 JSON 结果。"""
    if not request.message.strip():
        raise HTTPException(status_code=400, detail="消息内容不能为空")

    config = {"configurable": {"thread_id": request.session_id}}

    result = await app.ainvoke(
        {"messages": [HumanMessage(content=request.message)]},
        config=config,
    )
    return ChatResponse(
        session_id=request.session_id,
        reply=result["messages"][-1].content,
    )


@server.post("/api/chat/cancel")
async def cancel_task(payload: CancelRequest):
    """主动取消指定 session 的执行。"""
    task = ACTIVE_TASKS.get(payload.session_id)
    if task and not task.done():
        task.cancel()
        ACTIVE_TASKS.pop(payload.session_id, None)
        return {"status": "ok", "message": f"Session {payload.session_id} 任务已取消"}
    return {"status": "not_running", "message": "该会话没有正在运行的任务"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "Port.server:server",
        host=os.environ.get("API_HOST", "0.0.0.0"),
        port=int(os.environ.get("API_PORT", "8000")),
        reload=False,
    )
