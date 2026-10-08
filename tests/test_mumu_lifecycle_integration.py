"""真机验收：#3 的 create/clone/rename/delete 与 #4 的 settings 在真的 MuMu 上跑一遍。

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


def test_settings_round_trip_on_a_real_instance(mumu):
    """set_settings 写下去的值能被 get_settings 读回来，生效值也跟着变。

    这是 #4 的核心真机声明。注意 `vm_cpu` 是**生效值**（由 performance_mode
    推出来），所以只写 `.custom` 不改 mode 时它不会动 —— 两个都要写。
    """
    console, created = mumu

    idx = console.create(f"{PREFIX}-设置")
    created.append(idx)

    before = console.get_settings(idx)
    assert all(isinstance(v, str) for v in before.values()), "MuMu 的值全是字符串"
    assert "core_version" in before, "实测 setting -a 里一定有这个只读 key"

    # 挑一个与当前生效值不同的值，否则这个断言等于什么都没验
    default = before["vm_cpu"]
    target = "1" if default != "1" else "2"

    console.set_settings(idx, performance_mode="custom", **{"performance_cpu.custom": target})

    after = console.get_settings(idx)
    assert after["performance_cpu.custom"] == target
    assert after["performance_mode"] == "custom"
    assert after["vm_cpu"] == target, f"生效值没跟着走：from {default} expected {target}"


def test_settings_surface_a_read_only_key_on_a_real_instance(mumu):
    """只读 key 报 -101 `key not writable` —— 与实例在不在跑无关。

    实测：同一个只读 key 在停机的 index 0 和运行中的 index 1 上写都是 -101。
    所以这句文案说的是「这个 key 只读」，不是「实例在跑」。
    """
    console, created = mumu

    idx = console.create(f"{PREFIX}-只读")
    created.append(idx)

    with pytest.raises(RuntimeError, match="key not writable"):
        console.set_settings(idx, resolution_width="720")
