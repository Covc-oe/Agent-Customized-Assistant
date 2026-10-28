from pathlib import Path
import os
from typing import Annotated
from pydantic import Field
from fastmcp import FastMCP

# 本文件是独立子进程，刻意不依赖 Common 包，但路径解析规则必须与 Agent 侧一致
_REPO_ROOT = Path(__file__).resolve().parents[2]          # <root>/Agent/mcp_tools/file_mcp_server.py
_DEFAULT_WORKSPACE = _REPO_ROOT / "Agent" / "mcp_tools" / "workspace"


def _resolve_workspace_dir() -> Path:
    """相对路径一律相对项目根解析，不依赖当前工作目录。"""
    raw = os.environ.get("AGENT_WORKSPACE_DIR")
    if not raw:
        return _DEFAULT_WORKSPACE
    path = Path(os.path.expanduser(raw))
    if path.is_absolute() or path.drive:
        return path
    return _REPO_ROOT / path


WORKSPACE_DIR = _resolve_workspace_dir().resolve()
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

mcp = FastMCP(name="File-Server")


@mcp.tool(
    name="read_file",
    description=(
        "读取工作区（workspace 目录）内的文本文件内容。"
        "仅能访问工作区内的文件，禁止读取工作区外的路径。"
    ),
)
def read_file(
    file_path: Annotated[
        str,
        Field(
            description=(
                '相对工作区的路径，不带 "workspace/" 前缀。'
                '例："device.txt"、"reports/issue_01.txt"。'
                "禁止传入绝对路径或 ../ 形式的越权路径。"
            )
        ),
    ],
) -> str:
    try:
        safe_path = _resolve_safe_path(file_path)
        if not safe_path.exists():
            return (
                f"错误：文件 {file_path} 不存在。"
                f"当前工作区绝对路径为: {WORKSPACE_DIR}。请确认文件已放入该目录。"
            )
        if not safe_path.is_file():
            return f"错误：{file_path} 不是一个有效的文件。"

        with open(safe_path, "r", encoding="utf-8") as f:
            content = f.read()
        return content if content else "【提示：该文件内容为空】"
    except Exception as e:
        return f"读取文件失败: {e}"

if __name__ == "__main__":
    mcp.run(transport="stdio")