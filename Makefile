.PHONY: clean clean-test clean-pyc clean-build docs help

help: ## 帮助
	@awk 'BEGIN {FS = ":.*##"; printf "\nUsage:\n  make \033[36m\033[0m\n\nTargets:\n"} /^[+a-zA-Z_-]+:.*?##/ { printf "  \033[36m%-10s\033[0m %s\n", $$1, $$2 }' $(MAKEFILE_LIST)

test: ## 跑测试
	pytest

build: ## build
	rm -rf dist
	rye build --wheel

publish: build ## 发布
	rye publish