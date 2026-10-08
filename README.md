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

### 反查：adb 上这个 serial 到底是谁的

撞号之后最需要问的一句话。`identify()` 拿指纹核对，不问厂商工具 —— 因为实测
`ldconsole adb --index 1` 在那个环境下会返回 MuMu 那台设备且不报错。

```python
from androtools.android_sdk.platform_tools import ADB
from androtools.core import LDConsole, MumuConsole, identify

adb = ADB(r"D:\ProgramFiles\LDPlayer9.0.79.2\adb.exe")
consoles = [
    ("雷电", LDConsole(r"D:\ProgramFiles\LDPlayer9.0.79.2\ldconsole.exe")),
    ("MuMu",  MumuConsole(r"D:\Program Files\Netease\MuMu\nx_main\MuMuManager.exe")),
]

identify(adb, consoles, "emulator-5556")
# InstanceIdentity(vendor='雷电', index='1', serial='emulator-5556')

identify(adb, consoles, "A87V026107005402")   # 真机
# None
```

`None` 是正常结果，不是错误 —— 真机、以及指纹读不出来的厂商（目前是 MuMu，
原因见 `MumuConsole.fingerprint` 的文档）都属于这一类。

### 列出在线设备：`discover()`

按 **wlan0 MAC 去重**，一台物理机器算一台。必须去重是因为 MuMu 会同时占两个 adb 名字
（自注册的 `emulator-555X` + `ensure_connected()` connect 出来的 `127.0.0.1:<port>`）：

```python
from androtools.android_sdk.platform_tools import ADB
from androtools.core import Device, DeviceInfo, LDConsole, MumuConsole, discover

adb = ADB(r"D:\ProgramFiles\LDPlayer9.0.79.2\adb.exe")
entries = discover(adb, [("雷电", LDConsole(...)), ("MuMu", MumuConsole(...))])
# adb 上 4 个条目 -> 3 台
#   PFFM10     bdfbafac          别名=['bdfbafac']
#   SM-A5560   127.0.0.1:16416   别名=['127.0.0.1:16416', 'emulator-5556']
#   GM1910     emulator-5554     别名=['emulator-5554']   雷电 index 0

for e in entries:
    device = Device(DeviceInfo(e.name, e.serial, adb.bin_path))   # serial 用 primary
```

每台会读一次机型（`ro.product.model`）当展示名，读不到就回退到 serial；认得出厂商和
实例就填 `identity`，认不出是 `None`（真机、或 MuMu 这类拿不到指纹的）。两个别名的
机器合并成一条，但 `serials` 里两个名字都留着，你拿哪个来都能用。

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

## 日常操作：清单 → 会话 → 配置

上面的例子都是「我已经知道要操作哪一台」。日常用起来更常见的是反过来：先问**现在有
几台、哪台在跑**，挑一台动手，再改它的配置。下面每一节的输出都是在本机跑出来的。

### 1. 列出实例

`list_instances()` 一次调用问出全部实例，**包括没启动的**，而且不依赖 adb：

```python
from androtools.core import MumuConsole

console = MumuConsole(r"D:\Program Files\Netease\MuMu\nx_main\MuMuManager.exe")

for i in console.list_instances():
    print(f"{i.index}  {i.name}  {i.status.name}  {i.serial or '-'}  android {i.android_version}")
```

```
0  A15  STOP  -                  android 15
1  A12  BOOT  127.0.0.1:16416    android 12
```

`status` 只区分 `STOP`（进程根本没起来）和 `BOOT`（起来了）—— 「能不能真的 adb 交互」
是 session 的问题。`serial` 只有运行中的实例才有：MuMu 的 adb 端口是启动时才分配的，
**不要自己按 index 算**（见上面「多开模拟器时 serial 会被认错」）。

拿到的是 `EmulatorInstance` —— 某一刻的**快照**，要新状态就再列一次。它的相等性不看
serial，所以重启一次不会让同一台机器变成两台。

### 2. 从清单到可操作的 session

`play()` 是清单的下一站，调用方不必知道厂商的 session 类叫什么：

```python
instance = console.list_instances()[1]
player = console.play(instance)      # MumuPlayer；只构造，不发任何命令
print(player.ensure_ready())         # True —— 启动并等到开机完成
player.shell.tap(100, 200)
```

`play()` 本身不做 IO：serial 的确定、`adb connect` 都属于 session 的启动流程
（`launch()` / `ensure_connected()`）。已经在跑的实例上 `ensure_ready()` 只是确认它
就绪。

### 3. 改配置

常用项有中立动词，不用记厂商方言：

```python
idx = instance.index
console.set_resolution(idx, 540, 960, 240)
console.set_cpu(idx, 2)
console.set_root(idx, True)

console.get_settings(idx)["resolution_width"]   # '540.000000'
```

内存的单位统一是 **MB 整数**（MuMu 内部用 GB、雷电用 MB，adapter 自己换算），CPU 是
核数。越界值抛 `ValueError` 且**不下发命令** —— 两家在这件事上都会静默骗你：实测
MuMu 把超范围的宽高夹到边界，雷电把不接受的宽高整个丢掉，两次都是成功退出码。

**写下去的值在实例下次启动时生效，本库不替你重启。** 实测两家都能在实例运行中写，
所以故意**不做**「停机 → 写 → 恢复运行」的编排：为了改个配置白关一次别人的模拟器
（重启 30s+、丢掉正在跑的东西）是比「下次启动才生效」更坏的意外。想立刻看到效果就
自己 `reboot_device(idx)`。

### 4. 改「对外自称的设备信息」

模拟（Simulation）的那五个字段（`android_id` / `imei` / `mac` / `model` / `brand`）：

```python
console.set_simulation(idx, "imei", "861234567890123")
console.get_simulation(idx)["imei"]     # '861234567890123'
```

key 不是那五个之一就抛 `ValueError` —— 两家对**任何** key 名都不报错，不拦就是静默
成功。值**原样透传**：厂商不校验，本库也不替它猜（厂商自己那套 `"auto"` 写法照传）。

### 5. 逃生舱

中立动词没包住的长尾（`sort`、`control tool func`、将来新增的子命令）直接透传：

```python
result = console.run("version")
print(result.output.strip())
```

```
{
  "version": "6.8.2.0"
}
```

`run()` **不做任何解析**（要 JSON 自己 parse），也**不看返回码** —— 非零不会抛，自己
看 `result.has_error()` / `result.exit_code`。输出按 adapter 声明的厂商编码解（MuMu
是 UTF-8、雷电是 GBK），与本机 locale 无关。

雷电的 `LDConsole` 有同一套方法。两个厂商的差异收在 adapter 后面：雷电读设置是直接
解析它自己的实例配置文件（它没有读的 CLI），而 MuMu 是一条命令拿到整份 JSON。

## 设计

```
Device                 真机。serial、状态判定、超时探测、应用操作
└─ EmulatorSession     加上厂商 Console 和 launch / close / ensure_ready / recover
   └─ ConsoleSession   雷电、MuMu

AndroidShell           adb 命令字典（shallow：省的是打字，不是藏复杂度）

EmulatorConsole        厂商 CLI 的方言止步于此。四个面：
                        · 会话必需   probe_state / getprop / run_app / kill_app / …
                        · 实例编目   list_instances / create / clone / delete / rename
                        · 配置读写   get_settings / set_settings / set_resolution /
                                     set_cpu / set_memory / set_root /
                                     get_simulation / set_simulation
                        · 通路       play() 造 session，run() 透传长尾
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