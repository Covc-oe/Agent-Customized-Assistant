import re
import os
import asyncio
from typing import Annotated, Optional, Sequence, TypedDict
from pathlib import Path
from langchain_core.tools import tool, StructuredTool
from pydantic import BaseModel, Field
from langchain_core.messages import (
    BaseMessage,
    HumanMessage,
    AIMessage,
    ToolMessage,
    SystemMessage,
)
from langgraph.graph import StateGraph, END
from langgraph.graph.message import add_messages
from langchain_deepseek import ChatDeepSeek
from Common.utils.config_handler import agent_config
from Common.utils.path_tools import get_abs_path
from RAG.RAG import KnowledgeBaseService
import traceback
import json
import aiomysql
import hashlib
import sqlglot
from sqlglot import exp
from langgraph.checkpoint.redis.aio import AsyncRedisSaver
from redis.asyncio import Redis


_rag_service: Optional[KnowledgeBaseService] = None

SYSTEM_PROMPT = SystemMessage(
    content=(
        "你是「智能运维助手」，为设备用户与管理员提供售后咨询、"
        "故障排查、设备数据查询与录入服务。\n"
        "\n"
        "【核心原则】\n"
        "1. 只依据事实回答：知识库检索结果、数据库查询结果、用户明确提供的信息。"
        "严禁凭空捏造设备型号、字段名、保修信息或故障原因。\n"
        "2. 信息不足时先追问：调用工具所需的必填参数（如 SN 码、日期）用户未提供时，"
        "先向用户索要，不要用默认值或猜测值强行调用工具。\n"
        "3. 查询无结果时如实告知，并建议联系人工客服；不要用常识补全或编造。\n"
        "\n"
        "【行为规范】\n"
        "4. 工具的具体用途与参数格式，请阅读每个工具的说明；只调用语义匹配的工具。\n"
        "5. 对涉及数据写入（add_or_update_device、write_file）的操作，"
        "执行前须与用户确认关键字段值。\n"
        "6. 回答保持简洁、结构化；涉及设备状态时按字段分条呈现；"
        "不确定的内容明确说明不确定。\n"
    )
)


def get_rag_service() -> KnowledgeBaseService:
    """延迟单例初始化：首次实际调用知识库检索时才加载模型与建立连接"""
    global _rag_service
    if _rag_service is None:
        _rag_service = KnowledgeBaseService()
    return _rag_service


async def get_mysql_connection():
    return await aiomysql.connect(
        host=agent_config.get("mysql_host", "127.0.0.1"),
        port=int(agent_config.get("mysql_port", 3306)),
        user=agent_config.get("user"),
        password=agent_config.get("password"),
        db=agent_config.get("database"),
        cursorclass=aiomysql.DictCursor,
        connect_timeout=agent_config.get("connect_timeout",5),
    )


class KnowledgeBaseSearchInput(BaseModel):
    query: str = Field(
        description=
        "检索扫地机器人内部知识库。"
        "当用户询问保养方法、故障排查、使用规范、硬件配置（如AI芯片、传感器）及科普类问题时调用。"
        "不适用于查询具体设备状态或数据库统计。"
    )


def _knowledge_base_search(query: str) -> str:
    """ 从知识库检索与 query 最相关的参考片段。

    Args:
        query: 用户问题提炼后的检索关键词或完整问句。
               例：扫地机器人滤网多久清洗一次、报错E3怎么处理。
    """

    try:
        service = get_rag_service()
        docs = service.search(query)

        if not docs:
            return "知识库未找到匹配规则，建议联系人工售后客服。"
        context_parts = []
        for i, doc in enumerate(docs):
            score = doc.metadata.get("rerank_score")
            score_text = f"相关度: {score:.2f}" if isinstance(score, (int, float)) else "未精排"
            context_parts.append(
                f"[参考片段 {i + 1} ({score_text})]:\n{doc.page_content}"
            )
        return "\n\n".join(context_parts)

    except Exception as e:
        traceback.print_exc()
        return f"查询数据库异常: {e}"


knowledge_base_search = StructuredTool.from_function(
    func=_knowledge_base_search,
    name="knowledge_base_search",
    description=
    "检索扫地机器人内部知识库。"
    "当用户询问保养方法、故障排查、使用规范、硬件配置（如AI芯片、传感器）及科普类问题时调用。"
    "不适用于查询具体设备状态或数据库统计。",
    args_schema=KnowledgeBaseSearchInput,
)



"""任务:添加通用读取文件工具"""




@tool(description=
      "根据设备序列号（SN 码）查询单台设备的型号、激活日期、保修截止日期、"
      "运行状态和电池健康度。"
      "仅在用户明确提供了 SN 码时调用；未提供 SN 时先向用户索要。")
async def device_search(device_sn: str) -> str:
    """查询单台设备的实时状态与保修信息。

    Args:
        device_sn: 设备序列号，字母数字组合，不区分大小写。
                   例："SN20250110A001"、"abc123xyz"。
    """
    device_sn = device_sn.strip().upper()
    sql = """
            SELECT 
                device_sn, model, activation_date, warranty_expire_date, 
                status, battery_health
            FROM devices
            WHERE device_sn = %s
            LIMIT 1
        """
    conn = None
    try:
        conn = await get_mysql_connection()
        async with conn.cursor() as cursor:
            await cursor.execute(sql, (device_sn,))
            row =  await cursor.fetchone()

        if not row:
            return f"数据库未查到序列号 {device_sn} 的设备信息，请核对后重试。"

        return (
            f"【设备状态】\n"
            f"- 序列号: {row.get('device_sn')}\n"
            f"- 型号: {row.get('model')}\n"
            f"- 激活日期: {row.get('activation_date')}\n"
            f"- 保修截止: {row.get('warranty_expire_date')}\n"
            f"- 运行状态: {row.get('status', '正常')}\n"
            f"- 电池健康度: {row.get('battery_health', 'N/A')}%\n"
        )
    except Exception as e:
        return f"查询数据库异常: {e}"
    finally:
        if conn:
            conn.close()


@tool(description=
      "查看数据库的表清单或指定表的字段结构（Schema）。"
      "在执行 execute_sql_query 之前，若不确定表名或字段名，应先调用此工具确认。")
async def get_db_schema(table_name: Optional[str] = None) -> str:
    """获取数据库 Schema 信息。

    Args:
        table_name: 可选。指定表名则返回该表的字段定义；不填则返回所有表名列表。
                    例："devices"。请勿加反引号或引号。
    """
    conn = None
    try:
        conn = await get_mysql_connection()
        async with conn.cursor() as cursor:
            if not table_name:
                await cursor.execute("SHOW TABLES;")
                tables = [list(row.values())[0] for row in  await cursor.fetchall()]
                return f"数据库中的表列表: {tables}\n如需查看具体表结构，请传入 table_name 重新调用本工具。"
            table_name = table_name.strip("`'\" ")
            await cursor.execute(f"DESCRIBE `{table_name}`;")
            columns = await cursor.fetchall()
            if not columns:
                return f"未找到表 {table_name} 的结构信息。"

            schema_info = [f"表 [{table_name}] 结构:"]
            for col in columns:
                schema_info.append(
                    f"- 字段: {col.get('Field')}, 类型: {col.get('Type')}, 允许NULL: {col.get('Null')}, 键: {col.get('Key')}, 默认值: {col.get('Default')}"
                )
            return "\n".join(schema_info)
    except Exception as e:
        return f"获取 Schema 失败: {e}"
    finally:
        if conn:
            conn.close()


FORBIDDEN_SQL_KEYWORDS = (
    "DROP", "TRUNCATE", "ALTER", "DELETE", "UPDATE", "INSERT", "GRANT", "REVOKE"
)


@tool(description=
      "执行只读 SQL 查询，用于数据统计、多条件筛选、查看全量数据等场景。"
      "仅支持 SELECT / SHOW / DESCRIBE / EXPLAIN 语句；禁止任何写操作。"
      "若不确定字段名，请先用 get_db_schema 查看表结构。")
async def execute_sql_query(query: str) -> str:
    """执行一条只读 SQL 并返回结果（默认最多 50 行）。

    Args:
        query: 合法的 MySQL SELECT 语句，不要带结尾分号。
               例："SELECT model, COUNT(*) FROM devices GROUP BY model"。
    """
    try:
        expressions = sqlglot.parse(query, read="mysql")
        if not expressions:
            return "安全拦截：未识别到有效 SQL 语句。"

        for expression in expressions:
            if not isinstance(expression, (exp.Select, exp.Describe)):
                if isinstance(expression, exp.Command) and str(expression.this).strip().upper().startswith("SHOW"):
                    continue
                return f"安全拦截：只读工具仅允许执行 SELECT/SHOW/DESCRIBE，拒绝执行高危操作 ({type(expression).__name__})。"
            if isinstance(expression, exp.Select) and expression.args.get("into"):
                return "安全拦截：禁止使用 INTO 导出数据到文件。"

    except sqlglot.errors.ParseError as e:
        return f"安全拦截：SQL 语法不合法或存在混淆注入，AST 解析失败。"

    cleaned_sql = query.strip().rstrip(";")
    if "LIMIT" not in cleaned_sql.upper() and cleaned_sql.upper().startswith("SELECT"):
        cleaned_sql += " LIMIT 50"

    conn = None
    try:
        conn = await get_mysql_connection()
        async with conn.cursor() as cursor:
            await cursor.execute(cleaned_sql)
            rows = await cursor.fetchall()

        if not rows:
            return "查询成功，但返回结果为空（无匹配记录）。"

        def json_serial(obj):
            if hasattr(obj, "isoformat"):
                return obj.isoformat()
            return str(obj)

        result_str = json.dumps(rows, ensure_ascii=False, indent=2, default=json_serial)
        if len(result_str) > 2000:
            result_str = result_str[:2000] + f"\n... [警告：数据过长已截断。原结果共 {len(rows)} 条，请要求用户细化 SQL 筛选条件（如加 WHERE 或更严格的 LIMIT）]"
        return f"查询到 {len(rows)} 条记录：\n{result_str}"

    except Exception as e:
        return f"SQL 执行错误: {e}\n原 SQL: {query}"
    finally:
        if conn:
            conn.close()


@tool(description=
      "向设备数据库录入全新设备。"
      "仅在用户明确给出所有必填信息时调用，缺字段先向用户询问。若需修改已有设备请用 update_device。")
async def add_device(
        device_sn: str,
        model: str,
        activation_date: str,
        warranty_expire_date: str,
        status: str = "正常",
        battery_health: float = 100.0,
) -> str:
    """新增一条设备记录。

        Args:
            device_sn: 设备序列号，字母数字组合，不区分大小写，作为主键。
            model: 设备型号名称，可为中文或英文。
            activation_date: 激活日期，格式 YYYY-MM-DD。
                             例："2025-01-10"。
            warranty_expire_date: 保修截止日期，格式 YYYY-MM-DD。
                                  例："2027-01-10"。
            status: 设备运行状态，默认 "正常"。
                    常见取值："正常"、"维修中"、"已停用"。
            battery_health: 电池健康百分比，0.00-100.00 的数，默认 100.00。
                            例：95.00。
        """
    device_sn = device_sn.strip().upper()
    sql = """
        INSERT INTO devices (
            device_sn, model, activation_date, warranty_expire_date, status, battery_health
        ) VALUES (
            %s, %s, %s, %s, %s, %s
        )
    """
    conn = None
    try:
        conn = await get_mysql_connection()
        async with conn.cursor() as cursor:
            await cursor.execute(
                sql,
                (device_sn, model, activation_date, warranty_expire_date, status, battery_health),
            )
        await conn.commit()
        return f"全新设备 {device_sn} 成功新增至数据库！(型号: {model}, 状态: {status})"
    except Exception as e:
        if conn:
            conn.rollback()
        return f"写入数据库失败（可能是设备已存在）: {e}"
    finally:
        if conn:
            conn.close()


@tool(description=
      "更新已有设备的部分信息。"
      "仅需提供设备序列号(device_sn)以及需要修改的字段，无需修改的字段切勿传入。")
async def update_device(
        device_sn: str,
        model: Optional[str] = None,
        activation_date: Optional[str] = None,
        warranty_expire_date: Optional[str] = None,
        status: Optional[str] = None,
        battery_health: Optional[float] = None,
) -> str:
    device_sn = device_sn.strip().upper()

    update_fields = []
    params = []

    if model is not None:
        update_fields.append("model = %s")
        params.append(model)
    if activation_date is not None:
        update_fields.append("activation_date = %s")
        params.append(activation_date)
    if warranty_expire_date is not None:
        update_fields.append("warranty_expire_date = %s")
        params.append(warranty_expire_date)
    if status is not None:
        update_fields.append("status = %s")
        params.append(status)
    if battery_health is not None:
        update_fields.append("battery_health = %s")
        params.append(battery_health)

    if not update_fields:
        return "未提供任何需要修改的字段，更新终止。"

    sql = f"UPDATE devices SET {', '.join(update_fields)} WHERE device_sn = %s"
    params.append(device_sn)

    conn = None
    try:
        conn = await get_mysql_connection()
        async with conn.cursor() as cursor:
            affected_rows = await cursor.execute(sql, tuple(params))
        await conn.commit()

        if affected_rows == 0:
            return f"更新失败：未找到序列号为 {device_sn} 的设备，或提供的修改值与原值完全相同。"
        return f"设备 {device_sn} 局部信息已成功更新！"

    except Exception as e:
        if conn:
            conn.rollback()
        return f"更新数据库失败: {e}"
    finally:
        if conn:
            conn.close()



WORKSPACE_DIR = Path(
    get_abs_path(os.environ.get("AGENT_WORKSPACE_DIR") or "Agent/mcp_tools/workspace")
).resolve()
WORKSPACE_DIR.mkdir(parents=True, exist_ok=True)


def _resolve_safe_path(file_path: str) -> Path:
    """解析并校验文件路径，防止跨目录遍历"""
    clean_path = str(file_path).replace("\\", "/")
    if clean_path.startswith("./workspace/"):
        clean_path = clean_path[len("./workspace/"):]
    elif clean_path.startswith("workspace/"):
        clean_path = clean_path[len("workspace/"):]
    elif clean_path.startswith("/workspace/"):
        clean_path = clean_path[len("/workspace/"):]
    elif clean_path == "workspace":
        clean_path = ""

    p = Path(clean_path)
    target = p.resolve() if p.is_absolute() else (WORKSPACE_DIR / p).resolve()
    if not target.is_relative_to(WORKSPACE_DIR):
        raise PermissionError(f"禁止越权访问工作区以外的路径: {file_path}")
    return target


@tool(description=
      "向工作区内的文件写入或追加文本内容。"
      "若父目录不存在会自动创建。仅能操作工作区内的路径。")
def write_file(file_path: str, content: str, mode: str = "w") -> str:
    """向工作区内指定文件写入文本。

    Args:
        file_path: 相对工作区的路径，不带 "workspace/" 前缀。
                   例："reports/issue_01.txt"。
        content: 要写入的文本内容，UTF-8 编码。
        mode: 写入模式，仅支持 "w"（覆盖，默认）或 "a"（追加）。
              当需要保留原内容并新增时，必须显式传 "a"。
    """
    if mode not in ("w", "a"):
        return "参数错误：mode 仅支持 'w' (覆盖) 或 'a' (追加)。"

    try:
        safe_path = _resolve_safe_path(file_path)
        safe_path.parent.mkdir(parents=True, exist_ok=True)

        with open(safe_path, mode, encoding="utf-8") as f:
            f.write(content)
        return f"文件写入成功: {file_path} (模式: {mode}, 字符数: {len(content)})"
    except Exception as e:
        return f"写入文件失败: {e}"


def _normalize_tool_output(output) -> str:
    """MCP 工具返回 content block 列表时，抽取出纯文本。"""
    if isinstance(output, str):
        return output
    if isinstance(output, list):
        texts = []
        for item in output:
            if isinstance(item, dict) and "text" in item:
                texts.append(str(item["text"]))
            else:
                texts.append(str(item))
        return "\n".join(texts) if texts else str(output)
    return str(output)

local_tools = [
    knowledge_base_search,
    device_search,
    write_file,
    add_device,
    update_device,
    execute_sql_query,
    get_db_schema
]

class AgentState(TypedDict):
    messages: Annotated[Sequence[BaseMessage], add_messages]

_LLM_KWARGS = {
    "model": agent_config.get("chat_model_name"),
    "temperature": agent_config.get("temperature", 0.1),
}

if os.environ.get("DEEPSEEK_API_KEY"):
    _LLM_KWARGS["api_key"] = os.environ["DEEPSEEK_API_KEY"]
if os.environ.get("DEEPSEEK_BASE_URL"):
    _LLM_KWARGS["base_url"] = os.environ["DEEPSEEK_BASE_URL"]

llm = ChatDeepSeek(**_LLM_KWARGS)

_ALL_TOOLS: list = []
_ALL_TOOLS_BY_NAME: dict = {}
_BOUND_LLM = None


async def _ensure_tools_initialized():
    global _ALL_TOOLS, _ALL_TOOLS_BY_NAME, _BOUND_LLM
    if _BOUND_LLM is not None:
        return

    from Agent.mcp_manager import _fetch_mcp_tools
    mcp_tools = await _fetch_mcp_tools()

    existing = {t.name for t in local_tools}
    _ALL_TOOLS = list(local_tools) + [t for t in mcp_tools if t.name not in existing]
    _ALL_TOOLS_BY_NAME = {t.name: t for t in _ALL_TOOLS}
    _BOUND_LLM = llm.bind_tools(_ALL_TOOLS)



_IDENTITY_PATTERN = re.compile(
    r"(你是谁|您是谁|你叫什么|你是什么|你的身份|介绍一下你自己|介绍下你自己|你叫什么名字|whoareyou)",
    re.IGNORECASE,
)

def is_identity_question(message) -> bool:
    """判断是否为询问身份的问题。"""
    content = getattr(message, "content", None)
    if not isinstance(content, str):
        return False
    normalized = re.sub(r"\s", "", content).lower()
    return bool(_IDENTITY_PATTERN.search(normalized))


async def Node_model(state: AgentState) -> dict:
    """Agent 思考与决策节点。"""
    messages = state["messages"]
    last_message = messages[-1] if messages else None

    if isinstance(last_message, HumanMessage) and is_identity_question(last_message):
        return {
            "messages": [
                AIMessage(
                    content="我是基于内部数据库训练后的私人 AI 助手，能基于内部数据回答您的问题！"
                )
            ]
        }

    call_messages = list(messages)
    if not call_messages or not isinstance(call_messages[0], SystemMessage):
        call_messages.insert(0, SYSTEM_PROMPT)

    response = await _BOUND_LLM.ainvoke(call_messages)
    return {"messages": [response]}


_FORBIDDEN_PATH_PATTERNS = (
    re.compile(r"^[A-Za-z]:[\\/]"),
    re.compile(r"^/mnt/[a-z]/", re.IGNORECASE),
)


def guard_tool_call(tool_name: str, tool_args: dict) -> Optional[str]:
    """工具执行前的安全校验（第二道防线，第一道在 MCP 侧的 _resolve_safe_path）。"""
    for key in ("path", "file_path", "filename", "file", "dir", "directory"):
        path = tool_args.get(key)
        if path and isinstance(path, str) and any(
            p.match(path.strip()) for p in _FORBIDDEN_PATH_PATTERNS
        ):
            return f"安全拦截：禁止访问宿主机路径 {path}"
    return None

def should_continue(state: AgentState) -> str:
    """判断是直接结束，还是调用普通工具，或是拦截敏感工具。"""
    last_message = state["messages"][-1]
    if getattr(last_message, "tool_calls", None):
        for tool_call in last_message.tool_calls:
            if tool_call["name"] in ["add_device", "update_device", "write_file"]:
                return "sensitive_tools"
        return "call_tools"
    return END


async def Node_tool(state: AgentState) -> dict:
    """工具实际执行节点。"""
    messages = state["messages"]
    last_message = messages[-1]
    tool_results = []

    recent_tool_hashes = set()
    for msg in messages[-6:-1]:
        if isinstance(msg, ToolMessage):
            recent_tool_hashes.add(hashlib.md5(f"{msg.name}:{msg.content}".encode("utf-8")).hexdigest())

    for tool_call in last_message.tool_calls:
        tool_name = tool_call["name"]
        tool_args = tool_call["args"]

        blocked = guard_tool_call(tool_name, tool_args)
        if blocked:
            output = blocked
        else:
            selected_tool = _ALL_TOOLS_BY_NAME.get(tool_name)
            if selected_tool is None:
                output = f"错误：不存在名为 {tool_name} 的工具，请重新选择。"
            else:
                call_hash = hashlib.md5(f"{tool_name}:{json.dumps(tool_args, sort_keys=True)}".encode("utf-8")).hexdigest()
                if call_hash in [hashlib.md5(f"{tc['name']}:{json.dumps(tc['args'], sort_keys=True)}".encode("utf-8")).hexdigest()
                    for msg in messages[-6:-1]
                    if getattr(msg, 'tool_calls', None)
                    for tc in msg.tool_calls]:
                    output = "系统提示：您已多次尝试使用相同参数调用该工具但未获有效进展。请停止调用该工具，并直接告知用户当前遇到的困难或请求用户提供更多信息。"
                else:
                    try:
                        output = await selected_tool.ainvoke(tool_args)
                    except Exception as e:
                        output = f"工具 {tool_name} 执行失败：{e}。请调整参数后重试。"

        tool_results.append(
            ToolMessage(
                content=_normalize_tool_output(output),
                tool_call_id=tool_call["id"],
                name=tool_name,
            )
        )
    return {"messages": tool_results}

workflow = StateGraph(AgentState)
workflow.add_node("agent", Node_model)
workflow.add_node("call_tools", Node_tool)
workflow.add_node("sensitive_tools", Node_tool)
workflow.set_entry_point("agent")
workflow.add_conditional_edges(
    "agent",
    should_continue,
    {
        "call_tools": "call_tools",
        "sensitive_tools": "sensitive_tools",
        END: END,
    },
)

workflow.add_edge("call_tools", "agent")
workflow.add_edge("sensitive_tools", "agent")

redis_host = agent_config.get("redis_host", "127.0.0.1")
redis_port = int(agent_config.get("redis_port") or 6379)
redis_db = int(agent_config.get("redis_db") or 0)

redis_client = Redis(
    host=redis_host,
    port=redis_port,
    db=redis_db,
    password=agent_config.get("redis_password") or None,
)
checkpointer = AsyncRedisSaver(redis_client=redis_client)

app = workflow.compile(checkpointer=checkpointer,interrupt_before=["sensitive_tools"])


async  def run_agent(user_input: str, thread_id: str = "user_session_001") -> str:
    """统一接口封装"""
    await _ensure_tools_initialized()
    config = {
        "configurable": {"thread_id": thread_id},
        "recursion_limit": agent_config.get("recursion_limit", 15),
    }
    result = await app.ainvoke(
        {"messages": [HumanMessage(content=user_input)]},
        config=config,
    )
    return result["messages"][-1].content


if __name__ == "__main__":
    async def _demo():
        print(await run_agent("请读取 workspace 下 test.txt 的内容", thread_id="demo_1"))
        print(await run_agent("读取 out.txt", thread_id="demo_2"))
    asyncio.run(_demo())

