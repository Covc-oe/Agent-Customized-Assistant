import asyncio
import os
import sys
import traceback
from pathlib import Path
from typing import List

from langchain_core.tools import BaseTool

from Common.utils.path_tools import get_abs_path

_AGENT_DIR = Path(__file__).resolve().parent
_MCP_SERVER_SCRIPT = _AGENT_DIR / "mcp_tools" / "file_mcp_server.py"
# 与 Agent/agent.py 完全相同的解析规则：相对路径一律相对项目根，与 cwd 无关
_WORKSPACE_ABS = Path(
    get_abs_path(os.environ.get("AGENT_WORKSPACE_DIR") or "Agent/mcp_tools/workspace")
)

_ENV = {
    **os.environ,
    "AGENT_WORKSPACE_DIR": str(_WORKSPACE_ABS),
    "PYTHONIOENCODING": "utf-8",
    "PYTHONUTF8": "1",
    "PYTHONLEGACYWINDOWSSTDIO": "0",
}

_adapter_context = None


def _mcp_enabled() -> bool:
    return os.environ.get("MCP_ENABLED", "1").strip().lower() not in ("0", "false", "no")


async def _fetch_mcp_tools() -> List[BaseTool]:
    """启动 MCP 文件服务子进程并拉取其工具列表。

    - 通过 ``MCP_ENABLED=0`` 可完全关闭（无 fastmcp 环境下的降级运行）
    - 连接只建立一次，后续复用同一个 adapter 上下文
    """
    global _adapter_context

    if not _mcp_enabled():
        return []

    try:
        from langchain.mcp import MCPAdapter
    except ImportError as exc:
        print(f"[MCP] 未安装 MCP 适配器，已跳过 MCP 工具加载: {exc}")
        return []

    if not _MCP_SERVER_SCRIPT.exists():
        print(f"[MCP] 未找到 MCP 服务脚本: {_MCP_SERVER_SCRIPT}")
        return []

    config = {
        "mcpServers": {
            "files": {
                "command": sys.executable,
                "args": ["-X", "utf8", str(_MCP_SERVER_SCRIPT)],
                "env": _ENV,
                "transport": "stdio",
            }
        }
    }

    if _adapter_context is None:
        _adapter_context = MCPAdapter(config)
        await _adapter_context.__aenter__()

    return await _adapter_context.list_tools()


def get_all_tools(local_tools: List[BaseTool]) -> List[BaseTool]:
    """同步入口：合并本地工具和 MCP 工具（兼容异步环境）。"""
    try:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop and loop.is_running():
            import threading

            mcp_tools_container: List[BaseTool] = []

            def run_in_thread():
                mcp_tools_container.extend(asyncio.run(_fetch_mcp_tools()))

            thread = threading.Thread(target=run_in_thread)
            thread.start()
            thread.join()

            mcp_tools = mcp_tools_container
        else:
            mcp_tools = asyncio.run(_fetch_mcp_tools())

    except Exception as e:
        print(f"MCP 工具加载失败: {e}")
        traceback.print_exc()
        mcp_tools = []

    all_tools = list(local_tools)
    existing_names = {t.name for t in all_tools}

    for mt in mcp_tools:
        if mt.name in existing_names:
            print(f"警告：MCP 工具 {mt.name} 与本地工具同名，已跳过")
            continue
        all_tools.append(mt)
    print(f"✅ 成功加载了 {len(mcp_tools)} 个 MCP 工具: {[t.name for t in mcp_tools]}")
    return all_tools
