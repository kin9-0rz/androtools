# 更新日志

本文件记录面向使用者的破坏性变更。内部重构（seam 抽取、测试补充）不在此列。

## 未发布

### 新增

- **`identify(adb, consoles, serial)`** —— 反查一个 adb serial 是哪个厂商的哪个实例。
  撞号之后最需要问的一句话；它拿实例指纹核对，**不问你手上的厂商工具**，因为实测
  `ldconsole adb --index 1` 在雷电+MuMu 混跑时会返回 MuMu 那台设备且不报错。
  认不出返回 `None`（真机、指纹读不出来的厂商都属于这一类）。

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