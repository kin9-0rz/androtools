# 雷电模拟器
import json
import shutil
import time
from pathlib import Path
from typing import Any, Callable

from androtools import logger
from androtools.android_sdk.platform_tools import AdbRunner
from androtools.core.device import (
    EmulatorConsole,
    DeviceStatus,
    EmulatorInfo,
    EmulatorInstance,
    Pids,
    bundled_adb_path,
)
from androtools.core.identity import IDENTITY_CMD, normalize_mac, parse_mac
from androtools.core.session import ConsoleSession


def _numeric_index(idx: str) -> int:
    """list2 的 index 是数字字符串。万一不是，排到最后而不是抛异常。"""
    return int(idx) if idx.isdigit() else 1 << 30


#: 雷电 `modify` 的参数全表：参数名 → 合法取值（实测自 `ldconsole help` 与真机）。
#:
#: 这张表是**必需**的：实测 `modify --bogus 1` 是 rc 0、stdout 空、什么都不改
#: —— 未知参数被**静默忽略**。不自己校验 key，调用方拼错一个名字就会得到一次
#: 「调用成功、配置没变」的假写入。
#:
#: 值域**不在这里拦**：实测 `--cpu 5` → rc 4294966291（-5 的 unsigned）+ stdout
#: `parameter error!`，`--memory 100` 同理 —— 厂商自己会拒，而且拒得可辨认
#: （见 `_write_settings` 里的退出码检查）。
#:
#: 不存在 `--dpi` / `--linenum` / `--serialno` / `--nfc` / `--brand`；
#: `--resolution` 一次写三个字段（`advancedSettings.resolution` 的 width/height
#: 加 `advancedSettings.resolutionDpi`）。
_MODIFY_VALUES: dict[str, str] = {
    "resolution": "<宽,高,dpi>，如 900,1600,400",
    "cpu": "1|2|3|4",
    "memory": "256|512|768|1024|1536|2048|4096|8192（MB）",
    "manufacturer": "<厂商名，如 asus>",
    "model": "<机型，如 ASUS_Z00DUO>",
    "pnumber": "<手机号>",
    "imei": "<auto|15 位>",
    "imsi": "<auto|15 位>",
    "simserial": "<auto|20 位>",
    "androidid": "<auto|16 位十六进制>",
    "mac": "<auto|12 位十六进制，不带冒号>",
    "autorotate": "1|0",
    "lockwindow": "1|0",
    "root": "1|0",
}


def _flatten(payload: dict[str, Any], prefix: str = "") -> dict[str, Any]:
    """把配置里的嵌套 dict 展平成点号 key。

    雷电的配置是「顶层 key 本身就是点号名字」（`advancedSettings.cpuCount`），
    但值里还有嵌套：`advancedSettings.resolution` 是 `{width, height}`，
    `hotkeySettings.*` 是 `{modifiers, key}`。展成
    `advancedSettings.resolution.width` 才能保证返回 dict[str, str]。
    """
    flat: dict[str, Any] = {}
    for key, value in payload.items():
        name = f"{prefix}.{key}" if prefix else str(key)
        if isinstance(value, dict):
            flat.update(_flatten(value, name))
        else:
            flat[name] = value
    return flat


def _as_text(value: Any) -> str:
    """配置值一律转成字符串（get_settings 的契约是 dict[str, str]）。

    bool 要排 int 前面判：Python 里 True 也是 int，不先拦就会变成 "1"。
    用 "true"/"false" 与 MuMu 那边的口径一致。
    """
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


class LDConsole(EmulatorConsole):
    """使用 ldconsole.exe 对模拟器进行管理"""

    def __init__(self, path=shutil.which("ldconsole.exe")):
        super().__init__(path)
        # 实测（9.0.79.2）：ldconsole 的 stdout 是 GBK（错误文案写死在二进制里），
        # 与本机 locale 无关。显式声明，免得在非中文 Windows 上把 `player don't
        # exist!` 之类的错误文案解成乱码。
        self.encoding = "gbk"

    def list_devices(self) -> str:
        """列出所有模拟器信息

        0. 索引
        1. 标题
        2. 顶层窗口句柄
        3. 绑定窗口句柄
        4. 运行状态, 0-停止,1-运行,2-挂起
        5. 进程ID, 不运行则为 -1.
        6. VBox进程PID
        7. 分辨率-宽
        8. 分辨率-高
        9. dpi
        """
        return self._run(["list2"]).output

    def _rows(self) -> list[list[str]]:
        """`list2` 的 CSV，每行 10 列。空行与全空输出都过滤掉。

        列序见 `list_devices()` 的 docstring。行号**不等于** index ——
        实例被删过之后 index 会有空洞。
        """
        return [
            [cell.strip() for cell in line.split(",")]
            for line in self.list_devices().splitlines()
            if line.strip()
        ]

    def get_pids(self, idx: int | str) -> Pids:
        """按 list2 的列序取 PID：第 5 列是界面进程，第 6 列是 VBox 进程。

        按第 0 列匹配，**不能**用行号当 index：实例被删过之后 index 有空洞
        （只剩 0 和 2 时，第 3 行根本不存在），行号匹配要么 IndexError，要么
        错位后静默取到**另一个实例**的 PID。找不到 index 返回 -1，由调用方
        自己决定怎么解读 —— 对 probe_state 来说那就是 STOP。
        """
        for parts in self._rows():
            if parts[0] == str(idx):
                return Pids(int(parts[5]), int(parts[6]))
        return Pids(-1, -1)

    def instances(self) -> list[str]:
        """雷电的实例编号就在 list2 的第 0 列。"""
        return [parts[0] for parts in self._rows()]

    def list_instances(self) -> list[EmulatorInstance]:
        """每台实例一条快照，**含未启动的**。一次 `list2` 就够，不碰 adb。

        serial 恒为 None：雷电的 serial 要拿 MAC 指纹去 adb 上反查才知道
        （见 `LDPlayer.resolve_serial`），而「列有哪些实例」不该依赖 adb。
        android_version 也拿不到 —— 雷电的实例配置里不记它。
        """
        return [self._to_instance(parts) for parts in self._rows()]

    def _to_instance(self, parts: list[str]) -> EmulatorInstance:
        index, name = parts[0], parts[1]
        pids = Pids(int(parts[5]), int(parts[6]))
        return EmulatorInstance(
            info=EmulatorInfo(
                name=name,
                serial=None,
                adb_path=bundled_adb_path(self.bin_path),
                index=index,
                console_path=self.bin_path or "",
            ),
            # 与 probe_state() 同一个判据（两个 PID 都在），不用第 4 列的运行
            # 状态字符串 —— 那个会把「界面进程死了但 VBox 还活着」的半启动
            # 报成运行中。
            status=DeviceStatus.BOOT if pids.is_running() else DeviceStatus.STOP,
            android_version=None,
        )

    def probe_state(self, idx: int | str) -> DeviceStatus:
        """雷电不给状态字符串，只能看两个进程在不在。"""
        if not self.get_pids(idx).is_running():
            return DeviceStatus.STOP
        return DeviceStatus.BOOT

    def create(self, name: str | None = None) -> str:
        """新建一台实例，返回它的 index。

        雷电这边**不能靠退出码判成败**：`add` 成功时退出码就是新 index，
        而 `remove` / `rename` 失败时是负数（还按 unsigned 截断），报错也打在
        stdout 上（`player don't exist!`）、不在 stderr。所以发完命令**回读
        list2**，用「多出来的那个 index」当答案，一个厂商文案都不解析。

        `name` 为 None 时不带 `--name`，让雷电自己起默认名（实测 index 2 的
        默认名是 `雷电模拟器-2`）。**新 index 是实测出来的，不是猜的**。
        """
        before = set(self.instances())
        cmd = ["add"] if name is None else ["add", "--name", name]
        self._run(cmd)
        return self._require_new_instance(cmd, before)

    def _require_new_instance(self, cmd: list[str], before: set[str]) -> str:
        """从 list2 里找出发命令之后多出来的那台，找不到就报错。

        拿不到编号时**报错而不是返回一个猜的 index** —— 调用方拿着那个值去
        launch 就会启动错的机器，比直接失败危险得多。
        """
        new = [idx for idx in self.instances() if idx not in before]
        if not new:
            raise RuntimeError(
                f"ldconsole {' '.join(cmd)} 之后 list2 里没有出现新实例"
                f"（现有 {sorted(before, key=_numeric_index)}），拿不到新实例的编号。"
            )
        return min(new, key=_numeric_index)

    def clone(self, idx: int | str, name: str | None = None) -> str:
        """基于 `idx` 复制一台，返回新 index。

        先确认源存在再发命令：实测源不存在时 `copy` 的退出码是 -616（unsigned
        显示 4294966296），而且把**整篇 help** 打到 stdout —— 多发一条命令就等于
        把那一大篇 help 混进错误信息里。
        """
        self._require_instance(idx)
        before = set(self.instances())
        cmd = ["copy"] if name is None else ["copy", "--name", name]
        cmd += ["--from", str(idx)]
        self._run(cmd)
        return self._require_new_instance(cmd, before)

    def delete(self, idx: int | str) -> None:
        """删除一台实例，删完回读 list2 确认它真的没了。

        **不替调用方停机**：雷电对「删除运行中的实例」究竟是什么行为，我们
        没实测过（唯一在跑的是用户的实例，不能碰），所以不写没验证过的编排。
        实例还在就把话说清楚，让调用方自己决定先 close() 还是先重启模拟器。
        """
        self._require_instance(idx)
        self._run(["remove", "--index", str(idx)])
        if str(idx) in self.instances():
            raise RuntimeError(
                f"ldconsole remove --index {idx} 之后实例仍然在 list2 里。"
                "雷电可能不允许删除运行中的实例，请先 close() 再删。"
            )

    def rename(self, idx: int | str, name: str) -> None:
        """改实例标题，之后回读 list2 确认名字真的变了。

        注意 `--title` 才是新名字：help 原文是
        `rename [--name <mnq_name | mnq_idx>] --title <mnq_title>`，那里的
        `--name` 是**选择器**的别名，容易看错。

        目标名与当前名相同时直接返回：list2 看不出变化，但「本来就叫这个名」
        不是失败，发命令只会把一次正常的 no-op 报成错。
        """
        self._require_instance(idx)
        if self._name_of(idx) == name:
            return
        self._run(["rename", "--index", str(idx), "--title", name])
        current = self._name_of(idx)
        if current != name:
            raise RuntimeError(
                f"ldconsole rename --index {idx} --title {name} 之后名字还是"
                f" {current!r}。"
            )

    def _require_instance(self, idx: int | str) -> None:
        """先在 list2 里确认这个 index 存在，避免把厂商的 help 全文当错误信息。"""
        if str(idx) not in self.instances():
            raise RuntimeError(
                f"没有 index 为 {idx} 的实例（现有 "
                f"{sorted(self.instances(), key=_numeric_index)}）。"
            )

    def _name_of(self, idx: int | str) -> str | None:
        """list2 的第 1 列。找不到 index 返回 None。"""
        for parts in self._rows():
            if parts[0] == str(idx):
                return parts[1]
        return None

    def get_settings(self, idx: int | str) -> dict[str, str]:
        """读实例配置。雷电**没有读的 CLI**，只能解析它自己写的 JSON。

        文件是 `vms/config/leidian{index}.config`，**扁平的点号 key** ——
        `propertySettings.phoneIMEI` 就是字面的一个 key，不是嵌套。实测里面
        约 70 个顶层 key：

        - `propertySettings.{phoneIMEI, phoneIMSI, phoneSimSerial,
          phoneAndroidId, phoneModel, phoneManufacturer, macAddress, phoneNumber}`
        - `advancedSettings.{resolution{width,height}, resolutionDpi, cpuCount,
          memorySize}`
        - `basicSettings.{rootMode, autoRotate, lockWindow, fps, …}`
        - `networkSettings.*`、`hotkeySettings.*`（这两个的值是嵌套 dict）

        值里的嵌套 dict 展平成点号（`advancedSettings.resolution.width`），
        值一律转字符串（bool 用 `"true"`/`"false"`）—— 保持「key → 字符串」
        这条契约与 MuMu 那边一致。

        ⚠️ 这里读出的 key（配置字段名）与 `set_settings` 收的 key
        （`modify` 的参数名）**不是同一套名字**：雷电只有写有 CLI，读只能读配置
        文件，两边的方言天然不同。例如读是 `advancedSettings.cpuCount`，写是
        `cpu="2"`。这是本方法为「厂商方言」付出的代价；中立命名由领域动词提供。

        新建的实例里 `advancedSettings.*` 与 `basicSettings.*` 那几项**整个键都
        不存在**（实测 `cpuCount` / `memorySize` / `resolution` /
        `resolutionDpi` / `rootMode` 全是 None），直到 `modify` 写进去才有 ——
        默认值不在这个文件里，所以它们不会出现在返回值里。

        Raises:
            RuntimeError: 配置读不到（实例不存在，或雷电换了目录布局）。
                **不返回空 dict** —— 那会让调用方以为「这台机器没有设置」。
        """
        path = self._config_path(idx)
        if path is None or not path.is_file():
            raise RuntimeError(
                f"读不到雷电实例 {idx} 的配置"
                f"（找 {path or '（拿不到 ldconsole 路径）'} 失败）。"
            )
        try:
            payload = json.loads(path.read_text(encoding="utf-8", errors="ignore"))
        except (OSError, json.JSONDecodeError) as e:
            raise RuntimeError(f"雷电实例 {idx} 的配置读不出来：{e}") from e

        return {key: _as_text(value) for key, value in _flatten(payload).items()}

    def _write_settings(self, idx: int | str, kv: dict[str, str]) -> None:
        """`modify --index <idx> --<key> <value> ...`（一次调用可改多个）。

        参数全表与值域见 `_MODIFY_VALUES`（也是 docstring 要求的那张表）。

        三道检查，各拦一种「静默成功」：

        1. **key 在不在表里** —— 实测未知参数是 rc 0、什么都不改；
        2. **实例存不存在** —— 实测 `modify --index 99` 也是 rc 0、什么都不改；
        3. **退出码** —— 实测值域不合法时 `modify --cpu 5` 给 rc 4294966291
           （-5 的 unsigned）+ stdout `parameter error!`。

        第 3 条是这个库**唯一**能用退出码判成败的地方：`add` 成功时退出码就是
        新 index、`remove`/`rename` 失败时是负数且报错打在 stdout 上（所以那三个
        动词一律靠回读 list2）。但 `modify` 的退出码是**单向可信**的：rc != 0
        一定失败，rc == 0 不能证明成功（前两条就是反例），所以前两条必须自己拦。
        """
        unknown = sorted(key for key in kv if key not in _MODIFY_VALUES)
        if unknown:
            raise ValueError(
                f"雷电的 modify 不认识这些 key：{unknown}。支持的 key 是 "
                f"{sorted(_MODIFY_VALUES)}。注意雷电对未知参数是**静默忽略**的"
                "（实测 rc 0、stdout 空、什么都不改），所以这里必须自己拦。"
            )
        self._require_instance(idx)

        cmd = ["modify", "--index", str(idx)]
        for key, value in kv.items():
            cmd += [f"--{key}", value]
        result = self._run(cmd)
        if result.exit_code:
            raise RuntimeError(
                f"ldconsole {' '.join(cmd)} 失败（退出码 {result.exit_code}）："
                f"{result.output or result.error or '厂商没有给出原因'}"
            )

    def launch_device(self, idx: int | str):
        return self._run(["launch", "--index", str(idx)])

    def reboot_device(self, idx: int | str):
        return self._run(["reboot", "--index", str(idx)])

    def quit_device(self, idx: int | str):
        return self._run(["quit", "--index", str(idx)])

    def is_running(self, idx: int | str) -> bool:
        return self._run(["isrunning", "--index", str(idx)]).output == "running"

    def getprop(self, idx: int | str, prop: str | None = None) -> str:
        if prop:
            return self._run(["getprop", "--index", str(idx), "--key", prop]).output
        return self._run(["getprop", "--index", str(idx)]).output

    # setprop <--name mnq_name | --index mnq_idx> --key <name> --value <val>
    def setprop(self, idx: int | str, prop: str, val: str):
        return self._run(
            ["setprop", "--index", str(idx), "--key", prop, "--value", val]
        )

    # installapp <--name mnq_name | --index mnq_idx> --filename <apk_file_name>
    def install_app(self, idx: int | str, apk_path: str):
        return self._run(["installapp", "--index", str(idx), "--filename", apk_path])

    # uninstallapp <--name mnq_name | --index mnq_idx> --packagename <apk_package_name>
    def uninstall_app(self, idx: int | str, package_name: str):
        return self._run(
            ["uninstallapp", "--index", str(idx), "--packagename", package_name]
        )

    # runapp <--name mnq_name | --index mnq_idx> --packagename <apk_package_name>
    def run_app(self, idx: int | str, package: str) -> bool:
        self._run(["runapp", "--index", str(idx), "--packagename", package])
        return True

    # killapp <--name mnq_name | --index mnq_idx> --packagename <apk_package_name>
    def kill_app(self, idx: int | str, package: str) -> None:
        self._run(["killapp", "--index", str(idx), "--packagename", package])

    # locate <--name mnq_name | --index mnq_idx> --LLI <Lng,Lat>
    def locate(self, idx: int | str, lng: float, lat: float):
        return self._run(["locate", "--index", str(idx), "--LLI", f"{lng},{lat}"])

    def adb(self, idx, cmd: str | list, encoding: str | None = None):
        if isinstance(cmd, list):
            cmd = " ".join(cmd)
        return self._run(["adb", "--index", str(idx), "--command", cmd], encoding=encoding)

    def adb_shell(self, idx, cmd: str | list, encoding: str | None = None):
        if isinstance(cmd, list):
            cmd = " ".join(cmd)
        return self.adb(idx, f"shell {cmd}", encoding=encoding)

    def _config_path(self, idx: int | str) -> Path | None:
        """实例配置的路径：`vms/config/leidian{index}.config`，相对 ldconsole.exe。

        拿不到 ldconsole 路径时返回 None（自己拼一个相对路径去读别人的 CWD 更糟）。
        """
        if not self.bin_path:
            return None
        return Path(self.bin_path).parent / "vms" / "config" / f"leidian{idx}.config"

    def fingerprint(self, idx: int | str) -> str | None:
        """这个实例在雷电自己眼里的身份：它的 MAC。

        读的是雷电写的实例配置 `vms/config/leidian{index}.config` 里的
        `propertySettings.macAddress`。这不是我们的猜测 —— 雷电把这个值注入到
        guest 的 wlan0 上，实测两边一致（配置 00DB48FD6270，
        `ip addr show wlan0` 是 00:db:48:fd:62:70）。

        配置不在就返回 None。不抛异常：雷电改目录布局是我们该降级的情况，
        而调用方有明确的降级方式（不猜，如实报错）。

        注意 `leidian{index}.config` 里的 index 是雷电的实例名，和
        `list2` 的行序一致 —— 实测两台实例分别是 leidian0 / leidian1。
        """
        config = self._config_path(idx)
        if config is None or not config.is_file():
            return None
        try:
            payload = json.loads(config.read_text(encoding="utf-8", errors="ignore"))
        except (OSError, json.JSONDecodeError):
            return None

        mac = payload.get("propertySettings.macAddress")
        return normalize_mac(mac) if mac else None


class LDPlayer(ConsoleSession):
    """雷电模拟器"""

    def __init__(
        self,
        info: EmulatorInfo,
        adb: AdbRunner | None = None,
        sleeper: Callable[[float], None] = time.sleep,
        console: LDConsole | None = None,
    ) -> None:
        super().__init__(
            info,
            LDConsole(info.console_path) if console is None else console,
            adb=adb,
            sleeper=sleeper,
        )

    #: 收窄到具体类型，resolve_serial 才拿得到 fingerprint
    console: LDConsole

    def is_crashed(self) -> bool:
        """
        判断模拟器是否没响应，如果没响应，则定义为模拟器崩溃
        需要判断设备崩溃吗？
        """
        # NOTE - 注意：每个模拟器的情况不一样！

        # 启动 com.android.settings
        self.shell.adb_shell(["am", "start", "com.android.settings"])
        self._sleep(3)
        # dumpsys window windows | grep mCurrentFocus
        result = self.shell.adb_shell(
            ["dumpsys", "window", "windows", "|", "grep", "mCurrentFocus"]
        )

        if result.output_contain("com.android.settings"):
            logger.warning(f"[{self.name}] 无法启动设置，杀死系统界面，重新启动设置。")
            self.shell.kill_app("com.android.launcher3")
            self.shell.adb_shell(["am", "start", "com.android.settings"])
            out = self.shell.adb_shell(
                ["dumpsys", "window", "windows", "|", "grep", "mCurrentFocus"]
            ).output
            if "com.android.settings" not in out:
                logger.warning(f"[{self.name}] 无法启动设置，可能需要重启模拟器。")
                return True

        self.shell.home()
        result = self.shell.adb_shell(
            ["dumpsys", "window", "windows", "|", "grep", "mCurrentFocus"]
        )
        if result.output_contain("com.android.launcher3"):
            logger.warning(f"[{self.name}] 回到桌面失败，请检查模拟器是否正常启动。")
            return True
        return False

    def launch(self) -> None:
        if self.is_boot():
            return

        while True:
            self.console.launch_device(self.index)
            self._sleep(10)
            if self.is_boot():
                break

            self.close()
            self._sleep(10)

    def close(self) -> None:
        self.console.quit_device(self.index)
        self._sleep(5)

    def resolve_serial(self) -> str:
        """在 adb 现有的设备里认出属于这个 index 的那一台，写进 info.serial。

        为什么必须反查而不能按 index 算端口：雷电的 serial 落在
        `emulator-5554 + 2 * index` 这个号段里，而 **MuMu 占用同一个号段**。
        实测两台雷电 + 两台 MuMu 同时开着：

            emulator-5554  雷电 index 0
            emulator-5556  MuMu index 1     <- 正好是雷电 index 1 该去的位置
            雷电 index 1   在 adb 里根本不出现

        更糟的是雷电官方的 `ldconsole adb --index 1` 会安静地返回 MuMu 那台设备
        的属性。所以按 index 推出的 serial 不会「连不上」，它会**合法地连到别人的
        模拟器**上 —— 对一个要装 APK、跑测试的库来说，这是最坏的失败方式。

        认法是核对 MAC：雷电把每个实例的 MAC 写进自己的实例配置，也注入了 guest
        的 wlan0，两边一定对得上。认不出就报错，不退而求其次去猜。

        Raises:
            RuntimeError: 找不到指纹，或 adb 上没有任何设备与它相符。
        """
        expected = self.console.fingerprint(self.index)
        if expected is None:
            raise RuntimeError(
                f"读不到雷电实例 {self.index} 的 MAC 指纹（找 {self.index} 号实例配置"
                "失败），没有可核对的身份。请在 DeviceInfo.serial 里直接指定 adb serial。"
            )

        candidates = [serial for serial, status, _ in self.adb.get_devices()]
        for serial in candidates:
            try:
                output = self.adb.run_shell_cmd(IDENTITY_CMD, serial).output
            except Exception as e:
                # adb devices 会列出问不动的东西（离线、正在重启）。跳过即可，
                # 整条反查不该因为一台设备不配合就失败。
                logger.debug(f"[{self.name}] 问 {serial} 的 MAC 失败：{e}")
                continue

            if parse_mac(output) == expected:
                self.info.serial = serial
                return serial

        raise RuntimeError(
            f"雷电实例 {self.index} 认不出对应的 adb 设备：adb 上有 "
            f"{candidates or '（无）'}，没有一台的 MAC 是 {expected}。"
            "雷电和 MuMu 共用 emulator-555X 号段，可能被对方的实例占住了；"
            "请在 DeviceInfo.serial 里直接指定。"
        )

    def get_status(self) -> DeviceStatus:
        """问状态之前先把 serial 认出来。

        认得出就用认出的（准），认不出就留给 `_checked_serial` 的歧义检查 ——
        那里的报错本来就带着设备列表，不会比这里差。而 `get_status()` 的契约是
        返回一个状态，不该因为「认不出实例」而抛异常。
        """
        self._auto_resolve_serial()
        return super().get_status()

    def _auto_resolve_serial(self) -> None:
        """serial 没给时按 MAC 认一次。认不出就算了，不硬失败。

        没有指纹可比对时直接跳过 —— 雷电改了目录布局不该让这个类彻底不能用，
        那种情况下 adb 的多设备歧义检查仍然拦得住。
        """
        if self.info.serial is not None or self.console.fingerprint(self.index) is None:
            return
        try:
            self.resolve_serial()
        except RuntimeError as e:
            logger.debug(f"[{self.name}] 自动认 serial 失败：{e}")
