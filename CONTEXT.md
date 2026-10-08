# androtools

一个对 Android SDK、第三方模拟器命令行工具、以及通过 adb 连接的 Android 设备的封装库。它的存在意义是：把「判断状态、等它就绪、卡死了怎么办」这段最容易出错、也最难手工验证的流程收进一处，让调用方只需要面对一个稳定的概念。

## Language

### 目标与宿主

**Device**:
一个可以通过 adb 寻址并接受命令的 Android 目标 —— 真机（插着 USB 或 WiFi adb 的手机）或者模拟器。这是本库操作的**对象**本身。两者在这一点上没有区别，本库不做区分。
_Avoid_: 模拟器（模拟器只是 Device 的一种**宿主**形态）、设备类型

**Emulator**:
由第三方应用提供的 Android 虚拟设备宿主程序（雷电、MuMu）。用户在宿主机上手动安装并启动它；它启动之后，在本库眼中就只是一个 Device —— 和真机没有区别。区别在于宿主那一侧：Emulator 有厂商 Console、能被 launch 和 close，Device 没有。
_Avoid_: Device、模拟器实例

### 寻址

**Serial**:
`adb -s` 所使用的目标标识符，例如 `emulator-5554` 或 `127.0.0.1:16416`。**有些模拟器的 Serial 是运行期才确定的** —— 端口会变，所以 MuMu 是在启动后从 Console 查出来的，而不是预先配置的。这条 Serial 必须在构造时给出，或者交给 `refresh_serial()` 之类的方法去填；留空且同时有多台设备在线时，库会明确报错而不是让 adb 自己乱挑。
_Avoid_: Index、transport id

**实例指纹**（Fingerprint）:
用来确认「眼前这台 adb 设备确实是我要的那个实例」的每实例唯一值。雷电把它写在实例配置 `vms/config/leidian{index}.config` 的 `macAddress` 里，也注入了 guest 的 wlan0 —— 两边读的是同一个 MAC。
_Avoid_: Serial（Serial 是寻址用的，指纹是**核对身份**用的；两者不能互相替代 —— 恰恰是 Serial 不可信才需要指纹）

**Serial 不可信**（Untrusted Serial）:
有些 Serial 推导得出来、但**不代表你能操作到想要的实例**。雷电的 Serial 落在 `emulator-5554 + 2 * index` 号段里，而 MuMu 占用同一个号段 —— 实测两台雷电 + 两台 MuMu 同时开着时，MuMu index 1 占住 `emulator-5556`，雷电 index 1 在 adb 里根本不出现；连雷电官方 `ldconsole adb --index 1` 都会安静地返回 MuMu 那台设备。这种 Serial 不报「连不上」，它**合法地指向别人的设备** —— 对一个要装 APK、跑测试的库来说这是最坏的失败方式。
因此遇到不可信 Serial 时必须**核对实例指纹**，核不上就如实报错，绝不按 index 猜端口。
_Avoid_: 端口冲突、serial 不对（说清是「连到了别人」还是「连不上」）

**反查**（Identify）:
给一个 adb Serial，问它是哪个厂商的哪个实例。它核对指纹，**不问厂商 Console** ——
雷电和 MuMu 撞号时，厂商自己的 CLI 会安静地返回另一家的设备。
目前只有雷电能反查（`fingerprint` 有值），MuMu 拿不出两边都读得到的指纹，返回 `None`。

**物理设备**（Physical Device）:
一台真实的机器，与它在 adb server 里有几个 Serial 名字无关。同一台机器可能有多个
adb 名字（MuMu 自注册的 `emulator-555X` + connect 出来的 `127.0.0.1:<port>`），
所以 `adb devices` 的条目数不等于设备数。`discover()` 按 wlan0 MAC 去重 —— 同一台机器
的多个名字 MAC 相同，不同机器一定不同。
_Avoid_: Adb Target（那是 adb 自己的寻址概念，与「几台机器」无关）

**Index**:
模拟器在厂商 Console 中的稳定编号，由用户在厂商工具里分配，跨重启不变。
_Avoid_: Serial、序号

**Adb Target**:
adb 选择目标的方式 —— 默认 / 按 Serial / 按 transport id。这是 adb 自身的寻址概念，与 Emulator Vendor **毫无关系**。
_Avoid_: DeviceType（`android_sdk/platform_tools.py` 里有一个同名的枚举专指这个，与「厂商」完全无关 —— 两个同名词是本库历史上最大的术语陷阱）

**Console**:
厂商随模拟器一起提供的命令行工具（`ldconsole.exe`、`MuMuManager.exe`），用于启动和关闭模拟器，以及执行无法通过 adb 完成的操作。
_Avoid_: 控制台、ADB（两者是不同的命令行，互不替代）

**Emulator Vendor**:
模拟器宿主程序的供应方。每个厂商决定了 Console 的命令行方言 —— 有的输出 CSV，有的输出 JSON —— 因此也决定了「哪些操作只能走 Console、无法通过 adb 完成」。新增一个厂商只需要写两个 adapter，不改已有代码。
_Avoid_: DeviceType（`core` 里的那个枚举专指厂商）

**启动判定**:
判断模拟器是否已经起来。厂商给出的信号强度差别很大：MuMu 直接给进程状态，而雷电不给状态、只能靠两个进程 PID 推断。因此 Console interface 上暴露的是状态而不是 PID —— PID 只是具体 Console 的内部细节。
_Avoid_: 启动状态、PID 判断（说清是哪一种）

### 状态

**Device State**:
一个 Device 在某一时刻的状态，取值为 STOP / BOOT / DEVICE / BOOT_COMPLETED / OFFLINE / ERORR。STOP 的含义随宿主而变，对模拟器是「尚未启动」，对真机是「adb 看不见它」—— 拔线、关 USB 调试都落在这里。而 OFFLINE 与 ERROR 都表示「连上了但用不了」。
_Avoid_: 状态、status（值本身叫 `DeviceStatus`）

**VM PID**:
模拟器内部负责与 adb 通信的那个进程的 PID。它与模拟器界面进程的 PID 是两个不同的进程；判断模拟器是否**完全**启动，需要两者同时存在。
_Avoid_: PID（单独说 PID 时通常指界面进程）、进程号

### 能力

**Capture**:
只抓取**单个应用**的网络流量。做法是用该应用的 user id 作为 iptables 标记，再让 tcpdump 按该标记采集，因此必须先知道应用的 user id。
_Avoid_: 抓包、抓流量（没说清抓的是谁）