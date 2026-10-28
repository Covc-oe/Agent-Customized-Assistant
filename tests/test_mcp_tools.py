"""MCP 文件工具联通性测试。

用法：
    python -m tests.test_mcp_tools
    # 或者
    pytest tests/test_mcp_tools.py -s

不再依赖任何绝对路径：工作区由 AGENT_WORKSPACE_DIR 决定，
未设置时回落到 <repo>/Agent/mcp_tools/workspace。
"""

import asyncio
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

os.environ.setdefault(
    "AGENT_WORKSPACE_DIR", str(REPO_ROOT / "Agent" / "mcp_tools" / "workspace")
)

from Agent.mcp_manager import _fetch_mcp_tools  # noqa: E402


async def _fetch():
    return await _fetch_mcp_tools()


def test_read_file_tool():
    tools = asyncio.run(_fetch())
    names = [t.name for t in tools]
    assert tools, f"未加载到任何 MCP 工具，请检查 MCP_ENABLED 与 fastmcp 依赖: {names}"

    read_file = next((t for t in tools if t.name == "read_file"), None)
    assert read_file is not None, f"未找到 read_file 工具，实际为: {names}"

    sample = Path(os.environ["AGENT_WORKSPACE_DIR"]) / "test.txt"
    sample.parent.mkdir(parents=True, exist_ok=True)
    if not sample.exists():
        sample.write_text("hello mcp\n", encoding="utf-8")

    result = asyncio.run(read_file.ainvoke({"file_path": "test.txt"}))
    assert "hello mcp" in str(result)


if __name__ == "__main__":
    test_read_file_tool()
    print("MCP 工具联通性测试通过")
