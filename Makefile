# 常用命令快捷入口
#   make install / check / fetch-model / seed / run-api / run-ui / up
.PHONY: help install check seed fetch-model run-api run-ui test up down logs clean

help:
	@echo "install  安装依赖（含本地模型依赖）"
	@echo "check    启动前环境自检"
	@echo "seed     生成演示数据（知识库 + 工作区）"
	@echo "fetch-model 下载 reranker 权重到 ./models"
	@echo "run-api  启动 FastAPI 服务 (8000)"
	@echo "run-ui   启动 Streamlit 前端 (8501)"
	@echo "test     运行测试"
	@echo "up       容器化启动全部服务"
	@echo "down     停止并移除容器"
	@echo "logs     查看 api 日志"

install:
	pip install torch==2.14.0 --index-url https://download.pytorch.org/whl/cpu
	pip install -r requirements.txt -r requirements-local.txt

check:
	python -m scripts.preflight

seed:
	python -m scripts.seed_demo

fetch-model:
	python -m scripts.fetch_reranker

run-api:
	python -m Port.server

run-ui:
	streamlit run Frontend/ui_cli.py --server.port=8501

test:
	pytest tests -s -q

up:
	docker compose up -d --build

down:
	docker compose down

logs:
	docker compose logs -f api

clean:
	find . -name "__pycache__" -type d -prune -exec rm -rf {} +
	rm -rf .pytest_cache
