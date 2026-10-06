# androtools

[![PyPI](https://img.shields.io/pypi/v/androtools?style=flat-square)](https://pypi.org/project/androtools/) ![PyPI - Status](https://img.shields.io/pypi/status/androtools?style=flat-square) ![PyPI - Python Version](https://img.shields.io/pypi/pyversions/androtools?style=flat-square) ![PyPI - Downloads](https://img.shields.io/pypi/dw/androtools?style=flat-square) ![PyPI - License](https://img.shields.io/pypi/l/androtools?style=flat-square)

对 Android SDK、第三方模拟器命令行工具、以及通过 adb 连接的 Android 设备的封装。

核心价值是把「判断状态、等它就绪、卡死了怎么办」这段最容易出错、也最难手工验证的流程收进一处。
支持**真机**、**雷电模拟器**、**MuMu 模拟器**。

## 安装

```bash
pip install androtools
```

## 快速开始

### 真机

真机不需要任何子类 —— 它就是一个 adb 可寻址的目标。

```python
from androtools.android_sdk.platform_tools import ADB
from androtools.core import Device, DeviceInfo

adb = ADB()
serial, status, _ = adb.get_devices()[0]

device = Device(DeviceInfo(name="我的手机", serial=serial, adb_path=adb.bin_path))
print(device.get_status())        # DeviceStatus.BOOT_COMPLETED
print(device.android_version)     # 问设备本身要，不是查本地表
print(device.read_prop("ro.product.model"))

device.shell.list_packages()      # 所有应用
device.shell.list_packages(flag=0)  # 只看第三方应用
device.shell.pull("/sdcard/DCIM", r"D:\backup")
```

同时插着多台设备时，`serial` **必须**指定 —— 否则 `AndroidShell` 会抛
`AmbiguousDeviceError`，而不是让 adb 乱挑。

### 模拟器

模拟器比真机多两样东西：厂商 Console，以及「启动 / 关闭」。

```python
from androtools.core import EmulatorInfo, LDPlayer

player = LDPlayer(
    EmulatorInfo(
        name="雷电",
        serial=None,                    # 库会按 MAC 指纹认出来，见下
        adb_path=r"D:\ProgramFiles\LDPlayer9.0.79.2\adb.exe",
        index="0",                      # 厂商 Console 里的编号
        console_path=r"D:\ProgramFiles\LDPlayer9.0.79.2\ldconsole.exe",
    )
)

player.launch()                # 启动模拟器并等它就绪
print(player.get_status())
player.shell.tap(100, 200)
player.run_app("com.android.settings")
player.close()
```

`LDPlayer` 会自己按 `console_path` 找 `ldconsole.exe`，MuMu 同理：

```python
from androtools.core import MumuPlayer

mumu = MumuPlayer(EmulatorInfo(
    name="MuMu",
    serial=None,
    adb_path=r"D:\Program Files\Netease\MuMu\nx_main\adb.exe",
    index="1",
    console_path=r"D:\Program Files\Netease\MuMu\nx_main\MuMuManager.exe",
))
mumu.launch()   # 启动 → 反查 serial → adb connect
```

MuMu 启动后 adb 端口会变，`launch()` 会从 Console 查出新的 `serial` 并 `adb connect`。
`adb connect` 不只在 `launch()` 里做 —— 从 MuMu 启动器外部启动、没经过 `launch()`
的实例，`127.0.0.1:<adb_port>` 默认并不在 `adb devices` 里，库会在用之前补上连接。

## 多开模拟器时 serial 会被认错

雷电和 MuMu 的 serial 都落在 `emulator-5554` 这一段号段里，**两家会撞**。实测两台雷电
+ 两台 MuMu 同时开着时：

| serial | 实际是谁 |
| --- | --- |
| `emulator-5554` | 雷电 index 0 |
| `emulator-5556` | MuMu index 1 —— 正好是雷电 index 1 该去的位置 |
| `127.0.0.1:16384` / `127.0.0.1:16416` | MuMu（tcp 地址，MuMu 的权威身份） |
| —— | 雷电 index 1 在 adb 里**根本不出现** |

推出来的 serial 不会「连不上」，它会**合法地连到别人的模拟器**上 —— 连雷电官方的
`ldconsole adb --index 1` 都会安静地返回 MuMu 那台设备的属性（实测返回 `SM-A5560`）。

把 MuMu 关掉再跑同样的两台雷电，`emulator-5556` 就变回雷电 index 1（`V1938T`）。**同一段
代码、同一个 serial，换个环境就换了个主人** —— 这就是为什么不能按 index 算端口。

所以 `LDPlayer` 在 `serial=None` 时不按 index 算端口，而是用每实例唯一的 **MAC 指纹**
（雷电实例配置里的 `propertySettings.macAddress`，guest 的 wlan0 上是同一个值）在 adb
上认自己的那一台，**核不上就报错，绝不猜**。多开雷电和 MuMu 混用时，如果某个实例的
serial 被对方占了，你会看到明确的报错而不是错误设备的数据。

想手动指定也完全可以，两个厂商都一样：

```python
EmulatorInfo(..., serial="emulator-5554", ...)   # 显式指定，跳过反查
```

### 抓包

按应用抓 —— 用应用的 user id 做 iptables 标记。

```python
from androtools.utils import Tcpdump

tcpdump = Tcpdump(device.shell, "com.example.app")
tcpdump.start_capture()
...
tcpdump.stop_capture()
tcpdump.pull_pcap_file(r"D:\capture")
```

## 设计

```
Device                 真机。serial、状态判定、超时探测、应用操作
└─ EmulatorSession     加上厂商 Console 和 launch / close / ensure_ready / recover
   └─ ConsoleSession   雷电、MuMu

AndroidShell           adb 命令字典（shallow：省的是打字，不是藏复杂度）
EmulatorConsole        厂商 CLI 的方言止步于此（probe_state / getprop / run_app / …）
```

两个 seam 都可以替换，所以生命周期逻辑**不需要真机就能测**：

```python
from androtools.testing import FakeADB

adb = FakeADB(responses={("get-state",): CmdResult("device", "")}, devices=[...])
device = Device(info, adb=adb, sleeper=lambda _s: None)
```

`FakeADB` 未声明的命令会直接抛错，所以「代码多调了一次 adb」会变成红灯而不是悄悄通过。

## 开发

```bash
make sync            # uv sync
make test            # 单元测试（默认排除 integration）
make typecheck       # mypy
make test-integration   # 需要真实 Android SDK 和设备
```

CI 在 `.github/workflows/ci.yml`，跑 Python 3.10（声明的最低版本）和 3.13。

## 术语

领域词汇见 [CONTEXT.md](https://github.com/kin9-0rz/androtools/blob/master/CONTEXT.md) —— 尤其「Device 与 Emulator 差在哪」「Serial 为什么是运行期才确定的」这两条。

## 破坏性变更

1.x → 2.0 的迁移说明见 [CHANGELOG.md](https://github.com/kin9-0rz/androtools/blob/master/CHANGELOG.md)。