#!/usr/bin/env python
"""启动自检：一键检查环境变量、本地依赖、外部服务连通性。

用法：
    python -m scripts.preflight

面试/交接场景下先跑这个，能快速定位「为什么跑不起来」。
全部通过时退出码为 0。
"""

import os
import socket
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

OK, WARN, FAIL = "✅", "⚠️ ", "❌"
_results: list[tuple[str, str, str]] = []


def record(level: str, name: str, detail: str = "") -> None:
    _results.append((level, name, detail))
    print(f"{level} {name}{(' — ' + detail) if detail else ''}")


def check_python() -> None:
    major, minor = sys.version_info[:2]
    if (major, minor) < (3, 12):
        record(WARN, "Python 版本", f"{major}.{minor}，建议 3.12+")
    else:
        record(OK, "Python 版本", f"{major}.{minor}")


def check_import(module: str, package_hint: str | None = None) -> None:
    try:
        __import__(module)
        record(OK, f"依赖 {module}")
    except Exception as exc:
        record(FAIL, f"依赖 {module}", f"{exc}（尝试 pip install {package_hint or module}）")


def check_env_file() -> None:
    if (REPO_ROOT / ".env").exists():
        record(OK, ".env 文件")
    else:
        record(WARN, ".env 文件", "不存在，将只使用系统环境变量；可 cp .env.example .env")


def check_env_var(name: str, required: bool = True, secret: bool = False) -> None:
    value = os.environ.get(name)
    if value:
        shown = "***" if secret else str(value)[:60]
        record(OK, f"环境变量 {name}", shown)
    elif required:
        record(FAIL, f"环境变量 {name}", "未设置")
    else:
        record(WARN, f"环境变量 {name}", "未设置（使用默认值）")


def check_tcp(host: str, port: int, name: str, required: bool = True) -> None:
    try:
        with socket.create_connection((host, port), timeout=3):
            record(OK, f"{name} 连通", f"{host}:{port}")
    except OSError as exc:
        level = FAIL if required else WARN
        record(level, f"{name} 连通", f"{host}:{port} 不可达（{exc}）")


def main() -> int:
    print("=" * 62)
    print("可定制化 Agent 环境自检")
    print("=" * 62)

    check_python()
    check_env_file()

    for module, hint in [
        ("yaml", "PyYAML"),
        ("dotenv", "python-dotenv"),
        ("langchain_core", "langchain-core"),
        ("langgraph", "langgraph"),
        ("langchain_chroma", "langchain-chroma"),
        ("langchain_ollama", "langchain-ollama"),
        ("aiomysql", "aiomysql"),
        ("redis", "redis"),
        ("sqlglot", "sqlglot"),
        ("jieba", "jieba"),
        ("fastmcp", "fastmcp"),
        ("fastapi", "fastapi"),
        ("streamlit", "streamlit"),
    ]:
        check_import(module, hint)

    print("-" * 62)
    check_env_var("DEEPSEEK_API_KEY", required=True, secret=True)
    check_env_var("MYSQL_PASSWORD", required=False, secret=True)
    check_env_var("OLLAMA_BASE_URL", required=False)

    print("-" * 62)
    try:
        from Common.utils.config_handler import agent_config, chroma_config

        record(OK, "配置加载", f"模型={agent_config.get('chat_model_name')}")
        record(OK, "向量库目录", str(chroma_config.get("persist_directory")))
        reranker = chroma_config.get("reranker_path")
        if reranker and Path(reranker).exists():
            record(OK, "Reranker 权重", reranker)
        else:
            record(WARN, "Reranker 权重", f"{reranker} 不存在，将跳过精排")
    except Exception as exc:
        record(FAIL, "配置加载", str(exc))
        agent_config, chroma_config = {}, {}

    print("-" * 62)
    check_tcp(agent_config.get("mysql_host", "127.0.0.1"), int(agent_config.get("mysql_port", 3306)),
              "MySQL")
    check_tcp(agent_config.get("redis_host", "127.0.0.1"), int(agent_config.get("redis_port", 6379)),
              "Redis")
    ollama_url = chroma_config.get("ollama_base_url", "http://127.0.0.1:11434")
    host = ollama_url.split("//")[-1].split(":")[0]
    port = int(ollama_url.rsplit(":", 1)[-1]) if ":" in ollama_url.split("//")[-1] else 11434
    check_tcp(host, port, "Ollama", required=False)

    workspace = Path(os.environ.get(
        "AGENT_WORKSPACE_DIR", REPO_ROOT / "Agent" / "mcp_tools" / "workspace"))
    if not workspace.is_absolute():
        workspace = REPO_ROOT / workspace
    if workspace.exists():
        record(OK, "Agent 工作区", str(workspace))
    else:
        record(WARN, "Agent 工作区", f"{workspace} 不存在（首次运行会自动创建）")

    failures = [r for r in _results if r[0] == FAIL]
    print("=" * 62)
    print(f"通过 {len(_results) - len(failures)} 项，失败 {len(failures)} 项")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
