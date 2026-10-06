.PHONY: help sync test test-integration typecheck build publish clean

# 版本号从 pyproject.toml 读，避免发布时手写错版本。
# 一律用 python 而不是 awk / grep / sed：Windows 上没有 GNU 工具链，
# 而且 cmd.exe 会把命令行里的 %1 %2 当成变量展开吃掉 —— 所以下面的内联
# python 里既不能出现 %，也不需要 $。
VERSION := $(shell uv run python -c "import re, pathlib; print(re.search(r'version = \"(.+?)\"', pathlib.Path('pyproject.toml').read_text(encoding='utf-8'), re.M).group(1))")
DIST := dist/androtools-$(VERSION)

# 说明文字里的 # 要写成 \x23：make 会把 recipe 里的 # 当成注释起点，
# 直接写 ## 会把这一行从中间截断（实测 typecheck 那条会被截掉下一个 target）。
# help 只列 target 名，不渲染 ## 后面的说明。说明保留在文件里给读 Makefile 的人看。
#
# 为什么不渲染：GNU make 在 Windows 上的输出管道会把非 ASCII 文本后面的换行吃掉 ——
# 中文说明会让相邻几行粘成一行（实测 test / test-integration / typecheck / build 会挤在
# 同一行）。命令本身是对的（`make -n` 可验证），直接跑那条 python 也对，所以这是 make
# 的输出问题，不是本文件的问题。help 是装饰性的，不值得为它绕路。
help: ## 列出所有 target（说明见本文件的 ## 注释）
	@uv run python -c "import pathlib, re; t = pathlib.Path('Makefile').read_text(encoding='utf-8'); print('make <target>'); [print('  ' + m.group(1)) for m in re.finditer(r'^([a-zA-Z_-]+):.*?\x23\x23', t, re.M)]"

sync: ## 同步
	uv sync

test: ## 跑单元测试（默认排除 integration）
	uv run pytest

test-integration: ## 跑集成测试（需要真实 Android SDK / 已连接的设备）
	uv run pytest -m integration

typecheck: ## 类型检查
	uv run mypy

build: clean ## 构建 wheel 和 sdist
	uv build

clean: ## 删掉构建产物
	uv run python -c "import shutil; shutil.rmtree('dist', ignore_errors=True)"

# 发布到 PyPI。不可撤销。
#
# 三个 Windows 上踩过的坑，都写在下面免得再犯：
#   1. `PYTHONIOENCODING=utf-8 uv run ...` 是 bash 的环境变量前缀语法。cmd 不认，
#      会去找一个叫 `PYTHONIOENCODING=utf-8` 的可执行文件，然后失败。改用
#      `python -X utf8`（UTF-8 模式），不依赖 shell 语法。
#   2. twine 的进度条含 '•'，GBK 控制台渲染时会 UnicodeEncodeError。-X utf8 解决。
#   3. recipe 里不能出现中文：编码往返会打坏它，甚至破坏字符串字面量变成 SyntaxError。
#
# 上传范围靠 `$(DIST)` 限定到当前版本。build 依赖 clean，所以 dist/ 里不会有旧产物 ——
# 这里**不需要**再加一道检查：加了也是死代码（clean 先跑，检查永远看不到残留）。
publish: build ## 发布到 PyPI（不可撤销，凭据读 ~/.pypirc）
	uv run python -X utf8 -m twine check $(DIST)*
	uv run python -X utf8 -m twine upload --non-interactive $(DIST)*