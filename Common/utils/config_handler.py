"""配置加载：YAML + 环境变量占位符 + 路径归一化。

YAML 中可以使用 ``${VAR}`` 或 ``${VAR:-default}`` 引用环境变量，
例如 ``password: ${MYSQL_PASSWORD}``。这样仓库里不再出现明文口令，
同一份配置也能同时用于本地开发、CI 和容器内运行。
"""

import os
import re
from typing import Any

import yaml

from Common.utils.path_tools import get_abs_path

_ENV_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")

_PATH_KEYS = frozenset(
    {
        "path",
        "data_path",
        "md5_path",
        "persist_directory",
        "reranker_path",
        "external_data_path",
        "workspace_dir",
    }
)

_MISSING_ENV_VARS: set[str] = set()


def _coerce(text: str) -> Any:
    """把环境变量字符串还原成 YAML 里期望的基础类型。"""
    lowered = text.strip().lower()
    if lowered in ("true", "false"):
        return lowered == "true"
    if lowered in ("null", "none", "~"):
        return None
    try:
        return int(text)
    except ValueError:
        pass
    try:
        return float(text)
    except ValueError:
        return text


def _expand_env(value: Any) -> Any:
    """递归展开 dict / list / str 中的 ``${VAR:-default}`` 占位符。"""
    if isinstance(value, dict):
        return {k: _expand_env(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_expand_env(v) for v in value]
    if not isinstance(value, str):
        return value

    def _replace(match: re.Match) -> str:
        var_name, default = match.group(1), match.group(2)
        raw = os.environ.get(var_name)
        if raw is not None:
            return raw
        if default is not None:
            return default
        _MISSING_ENV_VARS.add(var_name)
        return ""

    if not _ENV_PATTERN.search(value):
        return value

    expanded = _ENV_PATTERN.sub(_replace, value)
    if _ENV_PATTERN.fullmatch(value):
        return _coerce(expanded)
    return expanded


def _is_path_key(key: str) -> bool:
    """判断某个配置项是不是路径。

    除显式白名单外，``*_path`` / ``*_dir`` / ``*_directory`` 结尾的键也按路径处理，
    避免以后新增路径配置时忘记登记、又退回成相对路径。
    """
    return (
        key in _PATH_KEYS
        or key.endswith("_path")
        or key.endswith("_dir")
        or key.endswith("_directory")
        or key == "directory"
    )


def _normalize_paths(config: dict) -> dict:
    """把路径类配置项转换为基于项目根目录的绝对路径。"""
    for key, value in config.items():
        if _is_path_key(key) and isinstance(value, str) and value:
            config[key] = get_abs_path(os.path.expanduser(value))
    return config


def _warn_missing_env_vars() -> None:
    if not _MISSING_ENV_VARS:
        return
    missing = ", ".join(sorted(_MISSING_ENV_VARS))
    print(f"[config] 警告：以下环境变量未设置，相关配置将为空值：{missing}")
    _MISSING_ENV_VARS.clear()


def _load_yaml(config_path: str, encoding: str = "utf-8") -> dict:
    if not os.path.exists(config_path):
        raise FileNotFoundError(f"配置文件不存在: {config_path}")
    with open(config_path, "r", encoding=encoding) as f:
        config = yaml.full_load(f) or {}
    if not isinstance(config, dict):
        raise ValueError(f"配置文件内容必须是映射结构: {config_path}")
    config = _expand_env(config)
    _warn_missing_env_vars()
    return _normalize_paths(config)


def load_agent_config(config_path: str = get_abs_path("Common/config/agent.yml"),
                      encoding: str = "utf-8") -> dict:
    """加载 Agent 配置。"""
    return _load_yaml(config_path, encoding)


def load_chroma_config(config_path: str = get_abs_path("Common/config/chroma.yml"),
                       encoding: str = "utf-8") -> dict:
    """加载向量库 / RAG 配置，并将相对路径转换为基于项目根目录的绝对路径。"""
    return _load_yaml(config_path, encoding)

agent_config = load_agent_config()
chroma_config = load_chroma_config()
