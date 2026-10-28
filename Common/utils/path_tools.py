import os

PROJECT_ROOT_ENV = "PROJECT_ROOT"


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

    # Common/utils/path_tools.py -> Common/utils -> Common -> <root>
    current_dir = os.path.dirname(os.path.abspath(__file__))
    return os.path.dirname(os.path.dirname(current_dir))


def get_abs_path(relative_path: str) -> str:
    """将相对路径转换为基于项目根目录的绝对路径；绝对路径原样返回。"""
    if os.path.isabs(relative_path):
        return os.path.normpath(relative_path)
    return os.path.normpath(os.path.join(get_project_root(), relative_path))
