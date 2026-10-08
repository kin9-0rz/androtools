# 更新日志

本文件记录面向使用者的破坏性变更。内部重构（seam 抽取、测试补充）不在此列。

## 未发布

### 新增

- **`discover(adb, consoles)`** —— 列出在线的**物理**设备，一台机器算一台。按 wlan0
  MAC 去重：MuMu 会同时占两个 adb 名字（自注册的 `emulator-555X` +
  `ensure_connected()` connect 出来的 `127.0.0.1:<port>`），实测跑一次
  `MumuPlayer.get_status()` 后 adb 上 4 个条目只对应 3 台机器。返回每台一个
  `DiscoveredDevice`（展示名 / primary serial / 全部别名 / 认出的厂商实例）。
  一台离线或问不动的设备不会被丢弃，仍在列表里。

### 新增（实例管理）

- **`EmulatorConsole.list_instances()`** —— 一次调用问出「我现在有几台机器」：
  每台的编号、名字、运行状态、Android 版本，返回 `EmulatorInstance` 快照
  （`info` / `status` / `android_version`）。不依赖 adb，也不启动任何东西。
  与只给编号的 `instances()` 分工不同：那个是反查流程的内部需要。

  快照的相等性**不看 adb serial** —— MuMu 重启一次端口就变一个，那不该让
  「同一台实例」变成两台。

- **`EmulatorConsole.create` / `clone` / `delete` / `rename`** —— 实例增删改。
  `create` / `clone` 返回新实例的编号，**拿不到就抛错，不返回猜的值**
  （返回空串会让调用方拿着它去启动错的机器）。`delete` 在 MuMu 侧会先把运行中
  的实例停干净再删 —— 实测 MuMu 拒绝对运行中的实例执行 delete（errcode -103）。
  名字**不必唯一**（实测两台重名都能建），所以别拿名字当标识，编号才是。

- **`EmulatorConsole.get_settings` / `set_settings`** —— 读写某台实例的设置。
  key 是**厂商方言**（`performance_cpu.custom`、`advancedSettings.cpuCount` …），
  这里不做统一命名；要中立命名请用领域动词。
  **写下去的值在实例下次启动时生效，本库不替你重启。** 实测两家都能在实例
  运行中写设置，所以故意**不做**「停机 → 写 → 恢复运行」的编排：白关一次别人
  的模拟器（重启 30s+、丢掉正在跑的东西）是比「值下次启动才生效」更坏的意外。
  想让它立刻生效就自己 `reboot_device(idx)`。

  注意 MuMu 报的 `key not writable` 说的是「这个 key 只读」，**与实例在不在跑
  无关** —— 同一个只读 key 在停机和运行中的实例上写都是这个错。

- **`EmulatorConsole.set_resolution` / `set_cpu` / `set_memory` / `set_root`** ——
  同一件事的中立说法，不用记厂商方言。内存单位两边都收 **MB 整数**，CPU 是核数。
  越界值抛 `ValueError` 且**不下发命令** —— 两家在这件事上都会骗你：实测 MuMu
  把超范围的宽高**静默夹到边界**（100 → 回读 380、99999 → 4096），雷电则把不
  接受的宽高**整个丢掉**（`--resolution 100,100,10` 最终只落了 dpi），两次都是
  rc 0、stdout 干净。所以范围从厂商标的元数据现读（MuMu 的
  `resolution_width.min/max`、`performance_cpu.list`），不写死在代码里。

  写完之后要立刻看到效果就自己重启实例：与 `set_settings` 一样，本库不替你重启。

- **`EmulatorConsole.get_simulation` / `set_simulation`** —— 读/写实例**对外自称**的
  五个字段：`android_id` / `imei` / `mac` / `model` / `brand`（`SIMULATION_KEYS`）。
  `get_simulation` 返回的 dict 恒有这五个 key，缺的补空串；`set_simulation` 的 key
  不是这五个之一就抛 `ValueError` 且不下发命令 —— 两家对**任何** key 名都不报错
  （实测 MuMu 的 `simulation -sk bogus_key` 回 rc 0 的 `{"bogus_key": ""}`，
  雷电的 `--bogus` 直接静默忽略），不拦就是静默成功。

  值一律**原样透传**：不做格式校验，也不拿空串当「未设置」（实测雷电把
  `--imei 1`、`--mac zz`、`--androidid abc` 全部 rc 0 原样落盘）。`"auto"`
  （雷电随机生成）与 `"__null__"`（MuMu 还原）是厂商方言，中立层不翻译。

  两个不对称写在 docstring 里而不抹平：机型是**有损映射**（MuMu 有
  `phone_brand`/`phone_model`/`phone_miit` 三个，雷电只有两个，中立的 `model`
  落到 MuMu 的 `phone_miit`）；MuMu 上「从未设过」与「设成了空」是同一个信号，
  雷电则永远有一份具体值。

上面这些新增成员都是**软成员**（带默认实现，抛 `NotImplementedError`），
所以已有的自定义 Console 不会因此无法实例化；厂商给不出的能力一律抛
`NotImplementedError`，不静默失败、不返回假值。

### 修复

- **中文实例名不再是乱码。** MuMuManager 的输出是 UTF-8，`ldconsole` 的是 GBK，
  而两者过去都按本机 locale 解码（简中 Windows 上是 cp936）—— MuMu 的中文设备名
  因此解成乱码。现在两个 adapter 各自声明自己的编码，与本机 locale 无关。
- **`LDConsole.get_pids` 按编号取 PID，不再按行号。** 原来 `list2` 的第 n 行
  被当成编号 n 的实例，所以编号有空洞时会 `IndexError`，更糟的是**安静地返回
  另一台实例的 PID**。现在找不到就返回 `Pids(-1, -1)`，与 `probe_state()` 一致。

## 2.1.0

已发布到 PyPI。

修掉 2.0.0 里两个会「给出错误答案」的缺陷，并新增反查 API。

### 新增

- **`identify(adb, consoles, serial)`** —— 反查一个 adb serial 是哪个厂商的哪个实例。
  撞号之后最需要问的一句话；它拿实例指纹核对，**不问你手上的厂商工具**，因为实测
  `ldconsole adb --index 1` 在雷电+MuMu 混跑时会返回 MuMu 那台设备且不报错。
  认不出返回 `None`（真机、指纹读不出来的厂商都属于这一类）。目前只有雷电能反查 ——
  MuMu 拿不出两侧都读得到的指纹，所以对 MuMu 设备返回 `None`。MuMu 不需要反查，
  因为它的 `adb_port` 本身权威且可连。

### 新增（interface）

- **`EmulatorConsole.fingerprint(idx)` / `instances()`** —— 新增两个方法，带默认实现
  （抛 `NotImplementedError`），所以已有的自定义 Console 不会因此无法实例化。
  拿不出指纹的 adapter 返回 `None` 即可，`identify` 会跳过它。

### 修复

- **`LDPlayer` 不再把 `serial=None` 变成一个随机设备。** 雷电的 serial 落在 `emulator-5554 + 2 * index` 号段里，而 **MuMu 占用同一个号段** —— 实测两台雷电 + 两台 MuMu 同时开着时，MuMu index 1 占住 `emulator-5556`，雷电 index 1 在 adb 里根本不出现；连雷电官方 `ldconsole adb --index 1` 都会安静地返回 MuMu 那台设备的属性。所以按 index 推出的 serial 不会「连不上」，它会**合法地连到别人的模拟器**。现在 `LDPlayer` 会用每实例唯一的 MAC 指纹（雷电实例配置里的 `propertySettings.macAddress`，guest 的 wlan0 上是同一个值）在 adb 上认自己的那一台，**核不上就报错，绝不按 index 猜端口**。显式给出 `serial=` 的行为完全不变。
- **`MumuPlayer` 在被外部启动时不再一律报 ERORR。** MuMuManager 报的 `127.0.0.1:<adb_port>` 默认并不在 `adb devices` 里 —— 必须显式 `adb connect` 之后才存在，而过去只有 `launch()` 会做这件事。从 MuMu 启动器外部启动的实例因此每条命令都 `not found`，`get_status()` 一路判到 ERORR（看着像设备坏了，其实只是没连上）。现在连接是「用之前先保证存在」。`launch()` 里那次 connect 仍在。

两处都只改「`serial=None` 时怎么办」：显式给出 `serial` 的调用方行为不变。

实测复核：MuMu 开着时雷电 index 1 认不出来（`emulator-5556` 被占），关掉 MuMu 后同一段
代码把 index 1 认成 `emulator-5556`（`V1938T`）。同一个 serial 在不同环境下主人不同，
所以核对的必须是身份而不是端口。

两台雷电 + 两台 MuMu 同时开着的实测：4 台设备从 `serial=None` 全部自动认到正确身份，
`identify()` 对两台 MuMu 返回 `None`（**不会**把它们误认成雷电）。MuMu 拿不出可反查的
指纹 —— 原因见 `MumuConsole.fingerprint` 的文档，那里记的是逐个候选验过的实测结论。

## 2.0.0

已发布到 PyPI（2026-10-04）。

PyPI 上最后一个已发布版本是 **1.0.10**（仓库里的 1.0.11 从未发布）。从 1.0.x 升级到 2.0.0 是一次大规模重构 —— **公开 API 已被移除或改名，升级前请读这一节。**

### 移除

| 移除的东西 | 原因 | 替代 |
| --- | --- | --- |
| `DeviceManager`、`WorkStatus`、`Device.is_busy` | 从未真正工作：`_device_map` 从不填充，`free_busy_device()` 第一行就早退，`add()` 不加入任何集合。零调用者 | 自己持有 `list[Device]`；需要并发调度时按 serial 挑 |
| `GEmu`、`GEmuInfo` | Google AVD 支持。无法实例化（缺 3 个 abstract 方法）、不调 `super().__init__()`、自带重复的状态枚举 | 没有了。如果需要，用 `Device` + Android SDK 官方工具 |
| `NoxConsole`、`NoxPlayer` | 夜神模拟器支持（不再使用） | 没有了。新增厂商见下方「加一个模拟器厂商」 |
| `FastBoot` | 9 个方法全部损坏（每个都把 `bin_path` 拼两次，实际执行 `fastboot fastboot devices`）。零调用者 | 官方 SDK 工具 |
| `Android_API_MAP` | 停在 SDK 35，对新设备会谎报。`android_version` 改为直接问设备 | `device.android_version` |
| `platform_tools.DeviceType` | 零调用者，且与 `core.DeviceType` 同名不同义（历史遗留的术语陷阱） | 无 |
| `AAPT`、`ApkSigner`、`DexDump`、`AVDInfo` | 未实现的空壳（只有 `__init__` 设一个路径） | `AAPT2`、`AVDManager` |
| `Device.install_app_by_console`、`uninstall_app_by_console`、`adb_by_console`、`adb_shell_by_console` | 零调用者 | `device.console.install_app(idx, apk)` |
| 厂商 Console 的 `quit_all_devices` | 零调用者，却因为在 interface 上而成为两个 adapter 的强制义务 | 自己遍历实例 |
| `ADB.get_devices(max_tries=...)` | 参数随方法一起去掉：重试不再是纯查询的职责 | 自己重试 |

### 改名

| 1.x | 2.0 | 说明 |
| --- | --- | --- |
| `Device`（含全部 adb 命令） | `Device`（仅真机与模拟器共有） | 语义收窄。命令字典搬到了 `AndroidShell`，见下 |
| `Device.launch_and_wait_for_device()` | `ensure_ready()` | |
| `Device.reboot()` | `recover()` | 返回 `DeviceStatus`，不再返回 `None` |
| `DeviceConsole` | `EmulatorConsole` | 用了 glossary 里 `_Avoid_` 的 `Device` 一词 |
| `DeviceConsole.get_pids()` | `EmulatorConsole.probe_state()` | interface 上暴露**状态**而不是 PID —— MuMu 的 CLI 只给一个 PID，`Pids` 退回具体 console 的内部细节 |
| `NoxPlayer.get_serial()` / `MumuPlayer.refresh_serial()` | `MumuPlayer.refresh_serial()` | 夜神删除 |

### `DeviceInfo` 的字段变了

1.x 有 9 个字段，其中 4 个从不被读取。现在按「真机 / 模拟器」拆开：

```python
# 1.x —— 所有字段必填，包括真机根本没有的 index 和 console_path
DeviceInfo(
    device_type=DeviceType.LD, index="0", serial="emulator-5554",
    name="雷电", version=9, adb_path="...", console_path="...",
    gateway="127.0.0.1", proxy_port=8080,
)

# 2.0 —— 真机
DeviceInfo(name="我的手机", serial="A87V026107005402", adb_path="...")

# 2.0 —— 模拟器（EmulatorInfo 多了 index 和 console_path）
EmulatorInfo(
    name="雷电", serial=None, adb_path="...",
    index="0", console_path=r"D:\...\ldconsole.exe",
)
```

被删掉的 `device_type` / `version` / `gateway` / `proxy_port` 都是零读取点；
`android_version` 改成向设备惰性查询，所以构造时不需要知道版本。

`DeviceInfo.__eq__` 的身份判据也变了：现在是 **serial + adb_path**
（1.x 是 index + adb_path + console_path）。

### 命令字典搬到了 `AndroidShell`

1.x 里这些都在 `Device` 上，现在在 `device.shell` 上：

`adb` · `adb_shell` · `adb_shell_daemon` · `install_app` · `uninstall_app` · `grant_permission` · `grant_all_permissions` · `run_app` · `kill_app` · `list_packages` · `pull` · `push` · `rm` · `ls` · `mkdir` · `screencap` · `ps` · `pidof` · `kill` · `killall` · `dumpsys_window_windows` · `tap` · `swipe` · `long_press` · `input_keyevent` · `input_text` · `home` · `back` · `delete` · `getprop`

还在 `Device` 上的：`get_status` · `is_boot` · `is_boot_completed` · `is_crashed` · `is_timeout` · `read_prop` · `run_app` · `kill_app` · `android_version` · `reconnect`。

### 迁移示例

```python
# 1.x
from androtools.core import Device, DeviceInfo, DeviceType
dev = Device(DeviceInfo(DeviceType.LD, "0", "emulator-5554", "雷电", 9,
                         r"...\adb.exe", r"...\ldconsole.exe", "127.0.0.1", 8080))
dev.launch_and_wait_for_device()
dev.tap(100, 200)
apps = dev.list_packages()

# 2.0
from androtools.core import EmulatorInfo, LDPlayer
dev = LDPlayer(EmulatorInfo("雷电", "emulator-5554",
                            r"...\adb.exe", "0", r"...\ldconsole.exe"))
dev.ensure_ready()
dev.shell.tap(100, 200)
apps = dev.shell.list_packages()

# 真机是新的，不需要 Console
from androtools.core import Device, DeviceInfo
dev = Device(DeviceInfo("我的手机", "A87V026107005402", r"...\adb.exe"))
print(dev.get_status())
```

### 行为变更（不是改名，但会影响你的代码）

1. **`ADB.get_devices()` 不再重启 adb server。** 它以前看到 `127.0.0.1:` 就调
   `restart_server()` —— 一个看起来纯查询的方法在改全局状态。现在问一次、解析、返回。
   依赖那个自动重启的调用方需要自己处理「模拟器刚启动时列表为空」。
2. **`serial` 为空且多台设备在线时抛 `AmbiguousDeviceError`**，而不是让 adb 自己挑。
   1.x 会静默发出无 `-s` 的命令并得到错误答案 —— 上层可能把它误读成「设备已连接」。
3. **`get_status()` 不再无条件 `adb reconnect`。** 只在真的 `not found` 时才重连
   —— `adb reconnect` 会打断 tcp 连接的模拟器且不会自己恢复。
4. **`android_version` 返回设备自报的版本号**（如 `"17"`、`"9"`），
   不再返回 `Android_API_MAP` 里的名字（如 `"Android 11"`）。
5. **`print(device)` 不再包含 Android 版本**，改成 `名字 [serial]` —— 取版本要发一条
   adb 命令，打印对象不该有副作用。
6. **`CmdResult.output_equal()` 变成真正的相等判断**（1.x 是包含判断）。
   `"device offline"` 1.x 会被判成已连接。

### 修复的缺陷

- 雷电的 PID 列偏移一位：把屏幕宽度当成了 VBox PID，「两个进程都在」实际只检查了一个
- 夜神的 PID 装反，且 VM PID 存成 `str` —— 与 `psutil` 返回的 `int` 永远比不相等，
  `get_serial()` 的循环出不来
- `get_status()` 重复查询 `get-state`，启动失败流程发 24 次 adb 往返（现 12 次）
- `ldconsole getprop` 在实例未启动时把报错写进 stdout 并返回 exit 0，错误文案被当成属性值
- `LDPlayer(info)` 无法构造：Console 曾经是必需位置参数
- `adb devices` 的 `*` 提示行（daemon 启动信息）被当成设备行解析
- `examples/` 下三个例子里有两个无法运行

## 加一个模拟器厂商

写两个 adapter 即可，已有代码一行都不用改：

```python
from androtools.core import ConsoleSession, EmulatorConsole, EmulatorInfo

class FooConsole(EmulatorConsole):
    def probe_state(self, idx): ...        # STOP 还是 BOOT
    def launch_device(self, idx): ...
    def reboot_device(self, idx): ...
    def quit_device(self, idx): ...
    def getprop(self, idx, prop): ...
    def run_app(self, idx, package): ...
    def kill_app(self, idx, package): ...

class FooPlayer(ConsoleSession):
    def launch(self): ...
    def close(self): ...
```

厂商的命令行方言全部止步在 `FooConsole` 内部。