# MuMu 模拟器（网易）
import json
import shutil
import time
from typing import Any, Callable

from androtools.android_sdk.platform_tools import AdbRunner
from androtools.core.device import EmulatorConsole, DeviceStatus, EmulatorInfo
from androtools.core.session import ConsoleSession


class MumuConsole(EmulatorConsole):
    """MuMuManager.exe —— MuMu 的控制台。

    和雷电完全不同的方言：子命令 + `-v <vmindex>`，输出 **JSON**。
    实测（MuMu Player 6.8.2.0）：

        MuMuManager.exe info -v all
        {
          "1": { "index": "1", "name": "A12", "android_version": "12.0",
                 "pid": 10572, "player_state": "start_finished",
                 "is_android_started": true, "is_process_started": true,
                 "adb_host_ip": "127.0.0.1", "adb_port": 16416 }
        }

    三件和另外两家不一样、值得记住的事：

    1. 未启动的实例把 pid / player_state / adb_port **整个键省略**，不是给 -1。
    2. 它还给出 player_state 和 is_android_started —— 比雷电好得多，雷电
       不给状态字符串，只能靠两个进程 PID 推断。
    3. `adb` 子命令是**受限的便利封装**（go_home / key_delete / input_text），
       没有原始命令透传 —— 所以 AndroidShell 走的是 MuMu 自带的 adb.exe，
       serial 由 `serial()` 拼出来，且必须显式 connect 才存在，见
       `MumuPlayer.ensure_connected`。
    """

    def __init__(self, path=shutil.which("MuMuManager.exe")):
        super().__init__(path)

    def _call(self, *args: str) -> Any:
        """跑一个子命令并把 JSON 结果解出来。失败时抛异常而不是静默返回。"""
        result = self._run(list(args))
        try:
            payload = json.loads(result.output)
        except json.JSONDecodeError as e:
            raise RuntimeError(
                f"MuMuManager {' '.join(args)} 没有返回 JSON：{result.output!r}"
            ) from e
        if payload.get("errcode"):
            raise RuntimeError(
                f"MuMuManager {' '.join(args)} 失败：{payload.get('errmsg', payload)}"
            )
        return payload

    def instance(self, idx: int | str) -> dict[str, Any]:
        """单个实例的原始信息。未启动的实例返回的 dict 会缺很多键。"""
        return self._call("info", "-v", str(idx))

    def serial(self, idx: int | str) -> str:
        """adb 的目标地址，例如 127.0.0.1:16416。实例未启动时拼不出来。"""
        info = self.instance(idx)
        try:
            return f"{info['adb_host_ip']}:{info['adb_port']}"
        except KeyError as e:
            raise RuntimeError(
                f"MuMu 实例 {idx} 还没启动，拿不到 adb 地址"
            ) from e

    def probe_state(self, idx: int | str) -> DeviceStatus:
        """MuMu 直接给出进程状态，不需要像雷电那样推断。

        用的是 is_process_started 而不是 player_state：probe_state 只粗略回答
        「进程在不在」，而 player_state 的取值我们没有穷举过（MuMu 换版本可能
        加新值），拿它做判断反而脆。开机是否完成由 session 用 adb 继续判定。
        """
        info = self.instance(idx)
        if not info.get("is_process_started"):
            return DeviceStatus.STOP
        return DeviceStatus.BOOT

    def launch_device(self, idx: int | str):
        return self._run(["control", "-v", str(idx), "launch"])

    def reboot_device(self, idx: int | str):
        return self._run(["control", "-v", str(idx), "restart"])

    def quit_device(self, idx: int | str):
        return self._run(["control", "-v", str(idx), "shutdown"])

    def getprop(self, idx: int | str, prop: str | None) -> str:
        """`sh` 子命令成功时返回纯文本，失败时返回一段 JSON —— 两种都要处理。"""
        if prop:
            cmd = ["sh", "-v", str(idx), "-c", f"getprop {prop}"]
        else:
            cmd = ["sh", "-v", str(idx), "-c", "getprop"]
        return self._text(self._run(cmd).output, cmd)

    def _text(self, output: str, cmd: list[str]) -> str:
        """MuMu 的 sh 子命令出错时会吐 JSON，而不是纯文本。"""
        stripped = output.strip()
        if not stripped.startswith("{"):
            return output

        payload = json.loads(stripped)
        raise RuntimeError(
            f"MuMuManager {' '.join(cmd)} 失败：{payload.get('errmsg', payload)}"
        )

    def run_app(self, idx: int | str, package: str) -> bool:
        self._call("control", "-v", str(idx), "app", "launch", "--package", package)
        return True

    def kill_app(self, idx: int | str, package: str) -> None:
        self._call("control", "-v", str(idx), "app", "close", "--package", package)


class MumuPlayer(ConsoleSession):
    def __init__(
        self,
        info: EmulatorInfo,
        adb: AdbRunner | None = None,
        sleeper: Callable[[float], None] = time.sleep,
        console: MumuConsole | None = None,
    ) -> None:
        mumu = MumuConsole(info.console_path) if console is None else console
        super().__init__(info, mumu, adb=adb, sleeper=sleeper)

    #: 收窄到具体类型，这样 refresh_serial 才能用 MumuConsole.serial
    console: MumuConsole

    def launch(self) -> None:
        self.console.launch_device(self.index)
        while True:
            self._sleep(1)
            if self.is_boot():
                break
        self.refresh_serial()
        self.ensure_connected()

    def close(self) -> None:
        self.console.quit_device(self.index)
        self._sleep(5)

    def refresh_serial(self) -> None:
        """MuMu 重启后 adb 端口会变，要重新取一次 serial。"""
        self.info.serial = self.console.serial(self.index)

    def ensure_connected(self) -> str | None:
        """让 adb server 认得这个实例的地址，返回用到的 serial。

        MuMu 的两套身份只有一套是可靠的。实测（MuMu 12 与 15 各一个实例）：
        模拟器会自己往 adb server 注册一个 `emulator-555X` 名字，而
        MuMuManager 报的 `127.0.0.1:<adb_port>` **默认并不在 adb devices 里**，
        必须显式 `adb connect` 之后才存在。

        而 `emulator-555X` 那套是跟雷电抢的同一个号段：MuMu index 1 占住了
        `emulator-5556`，正好是雷电 index 1 该去的位置；MuMu index 0 该占的
        `emulator-5554` 已经被雷电 index 0 拿走，于是它只剩 tcp 地址可用。
        所以只有 `127.0.0.1:<adb_port>` 是 MuMu 的权威地址。

        连接也因此是「用之前先保证存在」，而不是只在 launch() 里做一次 ——
        从 MuMu 启动器外部启动的实例不会被我们 launch 过，adb 上那个地址不存在，
        每条命令都 not found，get_status() 一路判到 ERORR。
        """
        if self.info.serial is None:
            self.refresh_serial()

        serial = self.info.serial
        if serial is None:
            return None

        if serial not in {d[0] for d in self.adb.get_devices()}:
            # connect 的对象是 adb server，不是某台设备，所以不传 -s。
            self.adb.run_cmd(["connect", serial], None)
        return serial

    def get_status(self) -> DeviceStatus:
        """先保证地址在 adb 上真的存在，再去问状态。

        不做这一步的话，外部启动的 MuMu 会一路走到 ERORR —— 那是个合法但完全
        误导的状态，看起来像设备坏了，其实只是 adb 没连上。
        """
        self.ensure_connected()
        return super().get_status()
