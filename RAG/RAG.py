import hashlib
import os
import re

import jieba
import numpy as np
from langchain_chroma import Chroma
from langchain_classic.retrievers.ensemble import EnsembleRetriever
from langchain_community.retrievers import BM25Retriever
from langchain_core.documents import Document
from langchain_ollama import OllamaEmbeddings
from langchain_text_splitters import RecursiveCharacterTextSplitter

from Common.utils.config_handler import chroma_config
from Common.utils.file_handler import get_file_md5_hex, pdf_loader, txt_loader
from Common.utils.logger_handler import logger


def get_string_md5(input_str: str, encoding: str = "utf-8") -> str:
    """计算单个切片文本的 MD5 指纹，用于切块级别的重复校验。"""
    md5_obj = hashlib.md5()
    md5_obj.update(input_str.encode(encoding=encoding))
    return md5_obj.hexdigest()


def check_md5(md5_str: str) -> bool:
    """检查指纹库中是否已存在该 MD5。"""
    md5_path = chroma_config.get("md5_path")
    if not os.path.exists(md5_path):
        os.makedirs(os.path.dirname(os.path.abspath(md5_path)), exist_ok=True)
        open(md5_path, "w", encoding="utf-8").close()
        return False

    with open(md5_path, "r", encoding="utf-8") as f:
        return md5_str in {line.strip() for line in f if line.strip()}


def save_md5(md5_str: str):
    """把新的 MD5 指纹持久化追加到本地文本文件中。"""
    with open(chroma_config.get("md5_path"), "a", encoding="utf-8") as f:
        f.write(md5_str + "\n")
    logger.info(f"[MD5持久化] 成功记录指纹: {md5_str}")


def chinese_tokenizer(text: str) -> list[str]:
    """供 BM25 检索使用的中文分词器。"""
    return [w.strip() for w in jieba.lcut(text) if w.strip()]


class KnowledgeBaseService:
    """知识库服务：Chroma 向量检索 + BM25 倒排索引混合召回，CrossEncoder 精排。"""

    def __init__(self, file_path: str = None):
        self.file_path = file_path

        persist_dir = chroma_config.get("persist_directory")
        os.makedirs(persist_dir, exist_ok=True)

        self.chroma = Chroma(
            collection_name=chroma_config.get("collection_name"),
            embedding_function=OllamaEmbeddings(
                model=chroma_config.get("embedding_model_name"),
                base_url=chroma_config.get("ollama_base_url"),
            ),
            persist_directory=persist_dir,
        )

        chunk_size = chroma_config.get("chunk_size")
        chunk_overlap = chroma_config.get("chunk_overlap")
        separators = chroma_config.get("separators")

        self.splitter = RecursiveCharacterTextSplitter(
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            separators=separators,
            length_function=len,
        )
        self.item_splitter = RecursiveCharacterTextSplitter(
            chunk_size=chunk_size,
            chunk_overlap=0,
            separators=["\n\n", r"\n(?=\d+[\.、])", "\n", "。", " "],
            length_function=len,
            is_separator_regex=True,
        )
        self.reranker = self._init_reranker()
        self._chroma_retriever = None
        self._bm25_retriever = None
        self.retriever = self._init_retriever()

    def _init_reranker(self):
        """加载 CrossEncoder 精排模型；关闭或权重缺失时降级为「不精排」。"""
        if not chroma_config.get("reranker_enabled", True):
            logger.warning("[Reranker] 已通过配置关闭（RERANKER_ENABLED=false），将跳过精排")
            return None

        reranker_path = chroma_config.get("reranker_path")
        if not reranker_path or not os.path.exists(reranker_path):
            logger.warning(
                f"[Reranker] 未找到模型目录 {reranker_path}，将跳过精排。"
                f"如需启用，请下载 bge-reranker-v2-m3 到该目录，或设置 RERANKER_PATH"
            )
            return None

        try:
            from sentence_transformers import CrossEncoder

            return CrossEncoder(
                reranker_path,
                max_length=512,
                device=chroma_config.get("device", "cpu"),
            )
        except ImportError as e:
            logger.warning(f"[Reranker] 未安装 sentence-transformers / torch，已跳过精排: {e}")
            return None
        except Exception as e:
            logger.error(f"[Reranker] 加载失败，已跳过精排: {e}")
            return None

    def _init_retriever(self):
        """构建混合检索器：Chroma 向量检索 + BM25 倒排索引融合。"""
        top_k = chroma_config.get("k", 20)
        weights = chroma_config.get("hybrid_weights", [0.6, 0.4])

        self._chroma_retriever = self.chroma.as_retriever(search_kwargs={"k": top_k})

        all_data = self.chroma.get(include=["documents", "metadatas"])
        docs = [
            Document(page_content=t, metadata=m or {})
            for t, m in zip(all_data.get("documents") or [], all_data.get("metadatas") or [])
        ]

        if not docs:
            logger.warning("[Retriever] 向量库为空，暂时只使用向量检索（请先调用 upload 入库文档）")
            self._bm25_retriever = None
            return self._chroma_retriever

        self._bm25_retriever = BM25Retriever.from_documents(docs, preprocess_func=chinese_tokenizer)
        self._bm25_retriever.k = top_k

        return EnsembleRetriever(
            retrievers=[self._chroma_retriever, self._bm25_retriever],
            weights=weights,
        )

    def _recall(self, query: str) -> list[Document]:
        """粗排召回：优先混合检索，向量服务不可用时降级到纯 BM25。"""
        try:
            return self.retriever.invoke(query)
        except Exception as e:
            logger.error(f"[Retriever] 混合检索失败（{e}），尝试降级到 BM25 检索")
            if self._bm25_retriever is None:
                return []
            return self._bm25_retriever.invoke(query)

    def rerank(self, query: str, docs: list[Document], top_n: int = None,
               score_threshold: float = 0.0) -> list[Document]:
        """使用 CrossEncoder 对召回文档打分并重新排序。"""
        if not docs:
            return []

        top_n = top_n or chroma_config.get("top_n", 5)

        if self.reranker is None:
            return docs[:top_n]

        pairs = [[query, doc.page_content] for doc in docs]

        raw_scores = self.reranker.predict(pairs)
        sigmoid_scores = 1 / (1 + np.exp(-np.array(raw_scores)))

        for doc, score in zip(docs, sigmoid_scores):
            doc.metadata["rerank_score"] = float(score)

        filtered = [doc for doc in docs if doc.metadata["rerank_score"] >= score_threshold]
        ranked = sorted(filtered, key=lambda x: x.metadata["rerank_score"], reverse=True)
        return ranked[:top_n]

    def _load_and_split(self) -> list[Document]:
        """统一加载（TXT/PDF）并根据文件类型选择分割器执行切块与元数据标记。"""
        ext = os.path.splitext(self.file_path)[1].lower()

        if ext == ".pdf":
            pages = pdf_loader(self.file_path)
            full_text = "\n".join([p.page_content for p in pages])
            raw_docs = [Document(page_content=full_text)]
            splitter = self.item_splitter
        elif ext in (".txt", ".md"):
            raw_docs = txt_loader(self.file_path)
            splitter = self.splitter
        else:
            logger.error(f"[文件加载失败] 不支持的文件类型: {ext}")
            return []

        chunks = splitter.split_documents(raw_docs)
        filename = os.path.basename(self.file_path)

        for idx, doc in enumerate(chunks):
            doc.metadata["source"] = filename
            doc.metadata["chunk_index"] = idx
        return chunks

    def upload(self) -> str:
        """带整文件级与切片级两道去重的入库。"""
        if not self.file_path or not os.path.exists(self.file_path):
            return f"【失败】文件不存在: {self.file_path}"

        file_md5 = get_file_md5_hex(self.file_path)
        if check_md5(file_md5):
            logger.info(f"[入库跳过] 整文件指纹无变化，直接跳过: {self.file_path}")
            return "【跳过】整文件指纹一致，无需重复处理"

        docs = self._load_and_split()
        if not docs:
            return "【失败】文档切块为空"

        m = re.search(r"_([\u4e00-\u9fa5]{2,4})\.\w+$", self.file_path)
        person_name = m.group(1) if m else None
        filename = os.path.basename(self.file_path)

        texts, metadatas, new_chunk_md5s = [], [], []

        for idx, doc in enumerate(docs):
            chunk = doc.page_content
            enriched = f"【{person_name}】{chunk}" if person_name else chunk
            chunk_md5 = get_string_md5(f"{filename}::{chunk}")

            if check_md5(chunk_md5):
                continue

            meta = dict(doc.metadata or {})
            meta.setdefault("source", filename)
            meta.setdefault("chunk_index", idx)
            if person_name:
                meta["person"] = person_name

            texts.append(enriched)
            metadatas.append(meta)
            new_chunk_md5s.append(chunk_md5)

        if not texts:
            save_md5(file_md5)
            return f"【跳过】{filename} 切片已全部存在于库中"

        self.chroma.add_texts(texts=texts, metadatas=metadatas)

        for c_md5 in new_chunk_md5s:
            save_md5(c_md5)
        save_md5(file_md5)

        self.retriever = self._init_retriever()

        res_msg = f"【成功】{filename} 入库完成：新增 {len(texts)} 块，跳过 {len(docs) - len(texts)} 块"
        logger.info(res_msg)
        return res_msg

    def search(self, query: str) -> list[Document]:
        """两阶段检索：混合检索粗排召回 -> CrossEncoder 精排过滤。"""
        candidate_docs = self._recall(query)
        return self.rerank(query, candidate_docs)

    def get_all_data(self, include_embeddings: bool = False):
        """获取 Chroma 集合中的全量存储数据。"""
        includes = ["documents", "metadatas"]
        if include_embeddings:
            includes.append("embeddings")
        return self.chroma.get(include=includes)
