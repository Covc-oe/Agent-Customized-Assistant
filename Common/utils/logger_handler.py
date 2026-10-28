import logging
import os
from datetime import datetime

from Common.utils.path_tools import get_abs_path

LOG_ROOT = get_abs_path(os.environ.get("LOG_DIR", "logs"))
os.makedirs(LOG_ROOT, exist_ok=True)

DEFAULT_LOG_FORMAT = logging.Formatter(
    "%(asctime)s - %(name)s - %(levelname)s - %(filename)s:%(lineno)d - %(message)s"
)


def get_logger(
        name: str = "agent",
        console_level: int = logging.INFO,
        file_level: int = logging.DEBUG,
        log_file=None,
) -> logging.Logger:
    """获取日志器，同时输出到控制台与按天分割的日志文件。"""
    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG)

    if logger.handlers:
        return logger

    formatter = DEFAULT_LOG_FORMAT

    console_handler = logging.StreamHandler()
    console_handler.setLevel(console_level)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    if not log_file:
        log_file = os.path.join(LOG_ROOT, f"{name}_{datetime.now().strftime('%Y%m%d')}.log")

    try:
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setLevel(file_level)
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
    except OSError as exc:
        logger.warning(f"日志文件不可写，已降级为仅控制台输出: {log_file} ({exc})")

    return logger


logger = get_logger()
