"""Common 包入口：加载项目根目录的 .env（不存在时静默跳过）。

这样 ``python -m Port.server`` / ``streamlit run`` / ``pytest`` 等任何入口
都能拿到与 ``.env.example`` 一致的环境变量，而无需在每个入口重复 load_dotenv。
真实存在的环境变量优先级更高（override=False）。
"""

from Common.utils.path_tools import get_abs_path

try:
    from dotenv import load_dotenv

    load_dotenv(get_abs_path(".env"), override=False)
except ImportError:
    pass
