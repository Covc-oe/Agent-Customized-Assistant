import hashlib
import os
from typing import Optional

from langchain_community.document_loaders import PyPDFLoader, TextLoader
from langchain_core.documents import Document

from Common.utils.logger_handler import logger


def get_file_md5_hex(filepath: str) -> Optional[str]:
    """计算文件的 MD5 十六进制字符串，失败返回 None。"""
    if not os.path.exists(filepath):
        logger.error(f"[md5计算] 文件 {filepath} 不存在")
        return None
    if not os.path.isfile(filepath):
        logger.error(f"[md5计算] 路径 {filepath} 不是文件")
        return None

    md5_obj = hashlib.md5()
    chunk_size = 4096

    try:
        with open(filepath, "rb") as f:
            while chunk := f.read(chunk_size):
                md5_obj.update(chunk)
        return md5_obj.hexdigest()
    except Exception as e:
        logger.error(f"计算文件 {filepath} md5 失败，{str(e)}")
        return None


def listdir_with_allowed_type(path: str, allowed_types: tuple[str, ...]):
    """列出目录下所有指定后缀的文件路径。"""
    if not os.path.isdir(path):
        logger.error(f"[listdir_with_allowed_type] {path} 不是文件夹")
        return []

    files = []
    for f in os.listdir(path):
        if f.endswith(allowed_types):
            files.append(os.path.join(path, f))
    return tuple(files)


def pdf_loader(filepath: str, passwd=None) -> list[Document]:
    """加载 PDF 文档。"""
    return PyPDFLoader(filepath, passwd).load()


def txt_loader(filepath: str, encoding: str = "utf-8") -> list[Document]:
    """加载文本文档（显式指定编码，避免 Windows 默认 GBK 造成乱码）。"""
    return TextLoader(filepath, encoding=encoding).load()
