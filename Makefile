.PHONY: help sync test test-integration typecheck build publish

help: ## 帮助
	@awk 'BEGIN {FS = ":.*##"; printf "\nUsage:\n  make \033[36m\033[0m\n\nTargets:\n"} /^[+a-zA-Z_-]+:.*?##/ { printf "  \033[36m%-10s\033[0m %s\n", $$1, $$2 }' $(MAKEFILE_LIST)

sync: ## 同步
	uv sync

test: ## 跑单元测试（默认排除 integration）
	uv run pytest

test-integration: ## 跑集成测试（需要真实 Android SDK / 已连接的设备）
	uv run pytest -m integration

typecheck: ## 类型检查
	uv run mypy

build: ## 构建 wheel 和 sdist
	uv run python -c "import shutil, pathlib; shutil.rmtree('dist', ignore_errors=True)"
	uv build

publish: build ## 发布到 PyPI（不可撤销，凭据读 ~/.pypirc）
	uv run twine check dist/*
	PYTHONIOENCODING=utf-8 uv run twine upload --non-interactive dist/*
