"""真机验收：#3 的 create/clone/rename/delete、#4 的 settings、#5 的中立配置动词、
#6 的 simulation、#7 的 play()/run()。

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

import json
from pathlib import Path

import pytest

from androtools.core.mumu import MumuConsole, MumuPlayer
from androtools.core.session import EmulatorSession

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


def test_set_resolution_writes_all_three_and_lands(mumu):
    """#5 的核心声明：一次 set_resolution 四个 key 落盘，只读的生效值跟着变。

    实测停机实例上写 `resolution_mode=custom` + 三个 `.custom` 会**立刻**反映到
    只读的 `resolution_width`/`_height`/`_dpi` 上，不必先启动。
    """
    console, created = mumu

    idx = console.create(f"{PREFIX}-分辨率")
    created.append(idx)

    console.set_resolution(idx, 540, 960, 240)

    after = console.get_settings(idx)
    assert after["resolution_mode"] == "custom"
    assert after["resolution_width.custom"] == "540.000000"
    assert after["resolution_height.custom"] == "960.000000"
    assert after["resolution_dpi.custom"] == "240.000000"
    assert after["resolution_width"] == "540.000000", "生效的只读值没跟着走"
    assert after["resolution_height"] == "960.000000"
    assert after["resolution_dpi"] == "240.000000"


def test_set_resolution_refuses_a_width_the_vendor_would_clamp(mumu):
    """实测超范围的宽是 rc 0 且不报错，厂商只夹到 380 —— 库得自己拦。"""
    console, created = mumu

    idx = console.create(f"{PREFIX}-夹取")
    created.append(idx)

    with pytest.raises(ValueError, match="380"):
        console.set_resolution(idx, 100, 960, 240)

    assert console.get_settings(idx)["resolution_mode"] == "tablet.1"


def test_set_memory_converts_megabytes_to_gigabytes(mumu):
    """中立层收 MB，MuMu 存 GB —— 2048 要落成 `2.000000`。"""
    console, created = mumu

    idx = console.create(f"{PREFIX}-内存")
    created.append(idx)

    console.set_memory(idx, 2048)

    after = console.get_settings(idx)
    assert after["performance_mem.custom"] == "2.000000"
    assert after["performance_mode"] == "custom"
    assert after["vm_mem"] == "2.000000", "生效值没跟着走"


def test_set_cpu_refuses_a_count_outside_the_machines_list(mumu):
    """可选核数随宿主机变，所以要现读 `performance_cpu.list` 再报错。"""
    console, created = mumu

    idx = console.create(f"{PREFIX}-核数")
    created.append(idx)

    with pytest.raises(ValueError, match="只能是"):
        console.set_cpu(idx, 32)


def test_set_root_round_trip(mumu):
    console, created = mumu

    idx = console.create(f"{PREFIX}-root")
    created.append(idx)

    assert console.get_settings(idx)["root_permission"] == "false"
    console.set_root(idx, True)
    assert console.get_settings(idx)["root_permission"] == "true"


def test_simulation_round_trip_on_a_real_instance(mumu):
    """#6 的核心真机声明：写下去 `simulation` 立刻生效，回读一致。

    与 `set_settings` 不同 —— `simulation` 是**即时**的，不是「下次启动才生效」。
    """
    console, created = mumu

    idx = console.create(f"{PREFIX}-模拟")
    created.append(idx)

    before = console.get_simulation(idx)
    assert set(before) == {"android_id", "imei", "mac", "model", "brand"}
    assert before["mac"] == "", "新建实例没伪装过 mac"
    assert before["imei"] == ""

    console.set_simulation(idx, "mac", "00DB48FD6270")
    console.set_simulation(idx, "imei", "865166023949731")

    after = console.get_simulation(idx)
    assert after["mac"] == "00DB48FD6270"
    assert after["imei"] == "865166023949731"
    assert after["android_id"] == "", "没碰过的项还是空"
    # 机型那两项来自 setting，与 simulation 无关
    assert after["brand"] == before["brand"]


def test_simulation_model_and_brand_go_through_the_setting_backend(mumu):
    """MuMu 的 `simulation` 只管 3 个 key —— 机型两项走 `setting` 的 phone_miit/phone_brand。"""
    console, created = mumu

    idx = console.create(f"{PREFIX}-机型")
    created.append(idx)

    console.set_simulation(idx, "model", "SM-A5560")
    console.set_simulation(idx, "brand", "OPPO")

    after = console.get_simulation(idx)
    assert after["model"] == "SM-A5560"
    assert after["brand"] == "OPPO"

    settings = console.get_settings(idx)
    assert settings["phone_miit"] == "SM-A5560"
    assert settings["phone_brand"] == "OPPO"


def test_simulation_can_be_restored_to_empty_with_the_vendors_own_word(mumu):
    """`__null__` 是 MuMu 自己的还原写法，原样透传，中立层不翻译。"""
    console, created = mumu

    idx = console.create(f"{PREFIX}-还原")
    created.append(idx)

    console.set_simulation(idx, "mac", "00DB48FD6270")
    assert console.get_simulation(idx)["mac"] == "00DB48FD6270"

    console.set_simulation(idx, "mac", "__null__")
    assert console.get_simulation(idx)["mac"] == ""


def test_the_vendor_answers_any_simulation_key_with_a_shrug(mumu):
    """锁厂商行为：不认识的 key 也是 rc 0 加一个空值。

    这就是 `set_simulation` 必须自己校验 key 的理由 —— 不拦的话「写了个不存在的
    项」在这里是**静默成功**。
    """
    console, created = mumu

    idx = console.create(f"{PREFIX}-乱键")
    created.append(idx)

    assert console._call("simulation", "-v", idx, "-sk", "bogus_key") == {"bogus_key": ""}


# --------------------------------------------------------------------------- #
# #7：清单 → 可操作对象，以及逃生舱
# --------------------------------------------------------------------------- #


def test_play_turns_a_listed_instance_into_a_player(mumu):
    """调用方不必知道厂商的 session 类叫什么。"""
    console, _ = mumu

    instance = console.list_instances()[0]

    player = console.play(instance)

    assert isinstance(player, EmulatorSession)
    assert isinstance(player, MumuPlayer)
    assert player.index == instance.index
    assert player.console is console


def test_run_hands_back_the_raw_text_of_a_real_command(mumu):
    """逃生舱不做任何解析 —— 拿回来的是原文，parse 留给调用方。"""
    console, _ = mumu

    result = console.run("info", "-v", "all")

    payload = json.loads(result.output)
    assert payload, "至少得有一台实例，否则这个断言没意义"
    assert all(isinstance(raw, dict) for raw in payload.values())


def test_run_can_drive_a_subcommand_no_neutral_verb_covers(mumu):
    """逃生舱的真实用途：中立动词没包住的长尾（`sort` 就是一个）。"""
    console, _ = mumu

    result = console.run("sort")

    assert result.exit_code == 0
