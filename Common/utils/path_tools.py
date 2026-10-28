import os
import re

#: 显式覆盖项目根目录（容器内或特殊目录结构下使用）
PROJECT_ROOT_ENV = "PROJECT_ROOT"

#: Windows 盘符形式的绝对路径（Linux 下 os.path.isabs 不认）
_WINDOWS_ABS = re.compile(r"^[A-Za-z]:[\\/]")


def get_project_root() -> str:
    """获取工程根目录。

    优先使用环境变量 ``PROJECT_ROOT``，否则以本文件位置向上回溯两级
    （``<root>/Common/utils/path_tools.py`` -> ``<root>``）。

    返回值与当前工作目录无关，避免 ``cd`` 到别的目录后配置、日志、向量库
    路径全部失效。
    """
    override = os.environ.get(PROJECT_ROOT_ENV)
    if override:
        return os.path.abspath(override)

    current_dir = os.path.dirname(os.path.abspath(__file__))
    return os.path.dirname(os.path.dirname(current_dir))


def is_absolute_path(path: str) -> bool:
    """判断是否绝对路径（兼容 Windows 盘符与 POSIX 路径）。"""
    return os.path.isabs(path) or bool(_WINDOWS_ABS.match(path))


def get_abs_path(relative_path: str) -> str:
    """把用户给的路径统一成绝对路径。

    - 已是绝对路径（含 ``D:\\xxx``）：原样返回
    - 相对路径：一律相对**项目根目录**解析，与当前工作目录无关
    """
    if is_absolute_path(relative_path):
        return os.path.normpath(relative_path)
    return os.path.normpath(os.path.join(get_project_root(), relative_path))
