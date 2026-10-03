# androtools

一个对 Android SDK 及第三方模拟器命令行工具的封装库。它的存在意义是：把「启动模拟器、等待开机、判断状态」这段最容易出错、也最难手工验证的流程收进一处，让调用方只需要面对一个稳定的概念。

## Language

### 目标与宿主

**Device**:
一个可以通过 adb 寻址并接受命令的 Android 目标。这是本库操作的**对象**本身。
_Avoid_: 模拟器（模拟器只是 Device 的一种宿主形态）、设备类型

**Emulator**:
由第三方应用提供的 Android 虚拟设备宿主程序（雷电、夜神、Android Studio AVD）。用户在宿主机上手动安装并启动它；它启动之后，在本库眼中就只是一个 Device。
_Avoid_: Device、模拟器实例

**Emulator Vendor**:
模拟器宿主程序的供应方。每个厂商决定了 Console 的命令行方言，因此也决定了「哪些操作只能走 Console、无法通过 adb 完成」。
_Avoid_: DeviceType（`core` 里的那个枚举专指厂商）

### 寻址

**Serial**:
`adb -s` 所使用的目标标识符，例如 `emulator-5554` 或 `127.0.0.1:5555`。**它在运行期才确定** —— 模拟器重启后端口可能变化，所以夜神模拟器的 Serial 是在启动过程中反查出来的，而不是预先配置的。
_Avoid_: Index、transport id

**Index**:
模拟器在厂商 Console 中的稳定编号，由用户在厂商工具里分配，跨重启不变。
_Avoid_: Serial、序号

**Adb Target**:
adb 选择目标的方式 —— 默认 / 按 Serial / 按 transport id。这是 adb 自身的寻址概念，与 Emulator Vendor **毫无关系**。
_Avoid_: DeviceType（`android_sdk/platform_tools.py` 里有一个同名的枚举专指这个，与「厂商」完全无关 —— 两个同名词是本库历史上最大的术语陷阱）

**Console**:
厂商随模拟器一起提供的命令行工具（`ldconsole.exe`、`NoxConsole.exe`），用于启动和关闭模拟器，以及执行无法通过 adb 完成的操作。
_Avoid_: 控制台、ADB（两者是不同的命令行，互不替代）

### 状态

**Device State**:
一个 Device 在某一时刻的生命周期状态，取值为 STOP / BOOT / DEVICE / BOOT_COMPLETED / OFFLINE / ERROR。注意 STOP 表示「尚未启动」，而 OFFLINE 与 ERROR 都表示「已启动但无法交互，通常只能重启」—— 这两者对使用者的处置动作是一样的。
_Avoid_: 状态、status（值本身叫 `DeviceStatus`）

**VM PID**:
模拟器内部负责与 adb 通信的那个进程的 PID。它与模拟器界面进程的 PID 是两个不同的进程；判断模拟器是否**完全**启动，需要两者同时存在。
_Avoid_: PID（单独说 PID 时通常指界面进程）、进程号

### 能力

**Capture**:
只抓取**单个应用**的网络流量。做法是用该应用的 user id 作为 iptables 标记，再让 tcpdump 按该标记采集，因此必须先知道应用的 user id。
_Avoid_: 抓包、抓流量（没说清抓的是谁）