#!/usr/bin/env python
"""下载 CrossEncoder 精排模型到 models/ 目录。

用法：
    python -m scripts.fetch_reranker                     # 默认 BAAI/bge-reranker-v2-m3
    python -m scripts.fetch_reranker --model BAAI/bge-reranker-base
    HF_ENDPOINT=https://hf-mirror.com python -m scripts.fetch_reranker   # 国内镜像

说明：
    权重体积约 2.2GB（float32），下载后由 docker-compose 以只读方式挂载进容器。
    该目录已在 .gitignore 中，不会进入仓库。
"""

import argparse
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MODEL = os.environ.get("RERANKER_MODEL_ID", "BAAI/bge-reranker-v2-m3")
DEFAULT_DIR = os.environ.get("RERANKER_PATH", "models/bge-reranker-v2-m3")


def main() -> int:
    parser = argparse.ArgumentParser(description="下载 reranker 权重")
    parser.add_argument("--model", default=DEFAULT_MODEL, help="HuggingFace 模型 id")
    parser.add_argument("--output", default=DEFAULT_DIR, help="本地保存目录（相对项目根）")
    args = parser.parse_args()

    try:
        from huggingface_hub import snapshot_download
    except ImportError:
        print("❌ 缺少 huggingface_hub，请先 pip install huggingface_hub（sentence-transformers 会自带）")
        return 1

    target = Path(args.output)
    if not target.is_absolute():
        target = REPO_ROOT / target
    target.mkdir(parents=True, exist_ok=True)

    endpoint = os.environ.get("HF_ENDPOINT")
    print(f"下载 {args.model} -> {target}" + (f"（镜像: {endpoint}）" if endpoint else ""))
    try:
        snapshot_download(repo_id=args.model, local_dir=str(target))
    except Exception as exc:
        print(f"❌ 下载失败: {exc}")
        print("   国内网络可尝试：HF_ENDPOINT=https://hf-mirror.com python -m scripts.fetch_reranker")
        return 1

    print(f"✅ 完成。将 RERANKER_PATH 指向 {target} 即可启用精排")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
