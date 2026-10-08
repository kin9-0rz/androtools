"""#3 的真机验收：create / clone / rename / delete 在真的 MuMu 上跑一遍。

默认被 `-m "not integration"` 排除，只有显式 `pytest -m integration` 才跑 ——
它会**真的在你机器上建实例，再删掉**。

安全规则（都是实测踩过的坑）：

- 只创建、只删除本测试自己建出来的 index。编号从 `create` / `clone` 的返回值
  读，**不猜**（新 index 是最小空闲号，不是「最大号 +1」）。
- 无论测试怎么失败，都在 fixture 的 teardown 里删干净 —— 实测 delete 是阻塞的
  （约 4.8s），返回时实例目录已经消失。
- 绝不碰用户已有的实例：唯一在跑的那台我们连 `list_instances()` 都不改。

MuMu 的方言细节见 `MumuConsole.create` / `clone` / `delete` / `rename` 的 docstring，
那里记的是本机实测（MuMu Player 6.8.2.0）。
"""

from pathlib import Path

import pytest

from androtools.core.mumu import MumuConsole

pytestmark = pytest.mark.integration

#: 本机安装路径（新版扁平布局，不是 "MuMu Player 12" 那个目录）
INSTALL = Path(r"D:\Program Files\Netease\MuMu\nx_main\MuMuManager.exe")

#: 测试实例的名字前缀，方便事后在 MuMu 里一眼认出来
PREFIX = "androtools-测试"


@pytest.fixture
def mumu():
    """真的 MuMuManager，加一个「无论如何都清理」的包围。"""
    if not INSTALL.is_file():
        pytest.skip(f"本机没有 MuMu（找不到 {INSTALL}）")

    console = MumuConsole(str(INSTALL))
    created: list[str] = []
    yield console, created

    leftovers = []
    for idx in created:
        try:
            console.delete(idx)
        except Exception as e:  # noqa: BLE001 - 清理失败要报出来，不能吞掉
            leftovers.append(f"{idx}: {e}")
    if leftovers:
        raise AssertionError(
            "测试实例没删干净，请手动清理：" + "; ".join(leftovers)
        )


def test_create_rename_delete_round_trip(mumu):
    """create → 出现在列表里 → rename → delete → 真的从列表里消失。"""
    console, created = mumu
    before = {i.index for i in console.list_instances()}

    idx = console.create(f"{PREFIX}-创建")
    created.append(idx)

    assert idx not in before, "新 index 不该和已有的撞号"
    listed = {i.index: i for i in console.list_instances()}
    assert idx in listed, f"create 说建在 {idx}，但 list_instances 里没有"
    assert listed[idx].name == f"{PREFIX}-创建"
    assert listed[idx].is_running is False, "新建的实例不该是运行中的"

    console.rename(idx, f"{PREFIX}-改名")
    assert {i.index: i.name for i in console.list_instances()}[idx] == f"{PREFIX}-改名"

    console.delete(idx)
    created.remove(idx)

    assert idx not in {i.index for i in console.list_instances()}


def test_clone_copies_the_source_into_a_new_index(mumu):
    """clone 出的副本是**另一台**：新编号、出现在列表里、能独立删掉。"""
    console, created = mumu

    source = console.create(f"{PREFIX}-源")
    created.append(source)
    names = {i.index: i.name for i in console.list_instances()}

    copy = console.clone(source, f"{PREFIX}-副本")
    created.append(copy)

    assert copy != source
    listed = {i.index: i for i in console.list_instances()}
    assert copy in listed, f"clone 说建在 {copy}，但 list_instances 里没有"
    assert listed[copy].name == f"{PREFIX}-副本"
    # 源还在，没被 clone 掉
    assert listed[source].name == names[source]

    console.delete(copy)
    created.remove(copy)

    remaining = {i.index for i in console.list_instances()}
    assert copy not in remaining
    assert source in remaining
