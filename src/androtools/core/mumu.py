# MuMu 模拟器（网易）
import json
import shutil
import time
from typing import Any, Callable

from androtools.android_sdk.platform_tools import AdbRunner
from androtools.core.device import (
    EmulatorConsole,
    DeviceStatus,
    EmulatorInfo,
    EmulatorInstance,
    bundled_adb_path,
)
from androtools.core.session import ConsoleSession


def _normalize_android_version(value: Any) -> str | None:
    """MuMu 报的是 "12.0" / "15.0"，而 Device.android_version 读的是 "12" / "15"。

    统一成后者，否则「同一个版本」在两个地方是两个字符串。
    """
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return text[:-2] if text.endswith(".0") else text


def _index_sort_key(idx: str) -> tuple[int, int]:
    """`info -v all` 的键是字符串形式的 index，按数值排序才是实例顺序。

    非数字的键排在后面 —— MuMu 现在不给这种键，但排序键不该因此抛异常。
    """
    return (0, int(idx)) if idx.isdigit() else (1, 0)


_CPU_LIST_KEY = "performance_cpu.list"
_MEM_LIST_KEY = "performance_mem.list"
_RESOLUTION_BOUNDS = {
    "width": ("resolution_width.min", "resolution_width.max"),
    "height": ("resolution_height.min", "resolution_height.max"),
    "dpi": ("resolution_dpi.min", "resolution_dpi.max"),
}


def _list_values(text: str) -> list[str]:
    """解 `[1,2,...,16](best=4)` 这种列表。

    厂商把「可选值」和「推荐值」挤在同一个字符串里，推荐值是括号后面的一句
    `(best=4)`，不是列表的一部分，要剪掉。本库只用可选值 —— 推荐值不能替用户
    选：实测新建实例的 `performance_cpu.custom` 是 4（恰好等于 best），
    而 resolution 与 memory 的默认值就**不是**括号里那个 best（新建实例是
    tablet.1 / 4 GB，best 是 6 GB）。
    """
    body = text.partition("(")[0].strip()
    return [item.strip() for item in body.strip("[]").split(",") if item.strip()]


def _bounded(key: str, value: int, bounds: dict[str, tuple[str, str]], settings: dict[str, str]) -> int:
    """用一个只读 key 的 `.min` / `.max` 查范围 —— **不要写死数字**。

    实测本机是宽高 380..4096、dpi 10..960，但这是宿主机给的，写在代码里就会
    在别的机器上过期。读不到边界时放行（交给厂商拒，总比乱拦好）。
    """
    low_key, high_key = bounds[key]
    low, high = settings.get(low_key), settings.get(high_key)
    if low is None or high is None:
        return value
    low_value, high_value = int(float(low)), int(float(high))
    if not low_value <= value <= high_value:
        raise ValueError(
            f"{key} 要在 {low_value}..{high_value} 之间（这台机器的 {low_key} / "
            f"{high_key}），给的是 {value}。实测越界值厂商会**静默**夹到边界，"
            f"所以这里拦下来而不是发下去。"
        )
    return value


def _nearest(candidates: list[float], value: float, *, what: str) -> float:
    """从厂商给的离散档位里选最接近 `value` 的一档（并列取大）。

    并列取大 = 宁可多给不肯少给：少给内存会让虚拟机跑不动，多给只是占些内存。
    范围外抛 ValueError —— 不静默夹到边界，那等于改了用户要的数。
    """
    if not candidates:
        raise ValueError(f"厂商没有报出可用的{what}档位，无法校验 {value}。")
    low, high = min(candidates), max(candidates)
    if not low <= value <= high:
        raise ValueError(f"{what}要在 {low:g}..{high:g} 之间（厂商给的档位），给的是 {value}。")
    return min(candidates, key=lambda candidate: (abs(candidate - value), -candidate))


def _mem_list_mb(settings: dict[str, str]) -> list[float]:
    """`performance_mem.list` 的单位是 **GB**（实测）。中立层一律 MB。"""
    list_key = settings.get(_MEM_LIST_KEY)
    if not list_key:
        return []
    return [float(item) * 1024 for item in _list_values(list_key)]


def _status_of(raw: dict[str, Any]) -> DeviceStatus:
    """MuMu 直接给出进程状态，不需要像雷电那样推断。

    用的是 is_process_started 而不是 player_state：probe_state 只粗略回答
    「进程在不在」，而 player_state 的取值我们没有穷举过（MuMu 换版本可能
    加新值），拿它做判断反而脆。开机是否完成由 session 用 adb 继续判定。
    """
    return DeviceStatus.BOOT if raw.get("is_process_started") else DeviceStatus.STOP


#: 等实例停下来的轮询间隔与上限（秒）。delete 对运行中的实例会被拒
#: （errcode -103），所以要先停干净。停不下来时不自己造错误 —— 接着发的
#: delete 会以厂商的 -103 失败，那条信息更接近事实。
_STOP_POLL_SECONDS = 1.0
_STOP_WAIT_SECONDS = 15.0


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
        # 实测（6.8.2.0）：MuMuManager 的 stdout 是 UTF-8，中文设备名按 UTF-8 输出
        # （`info -v all` 的 name 字段原始字节是 `\xe6\xb5\x8b\xe8\xaf\x95` = 测试）。
        # 跟随本机 locale 会解成乱码，见 CMD.encoding。
        self.encoding = "utf-8"

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

    def instances(self) -> list[str]:
        """MuMu 的实例编号就是 `info -v all` 返回的那个 dict 的键。"""
        return list(self._call("info", "-v", "all"))

    def list_instances(self) -> list[EmulatorInstance]:
        """每台实例一条快照，**含未启动的**。一次 `info -v all` 就够，不碰 adb。

        MuMu 报 android_version="12.0"，这里归一化成 "12"（与 Device.android_version
        口径一致）。serial 只对运行中的实例存在 —— 未启动时 MuMu 把 adb_host_ip /
        adb_port 两个键整个省略了，**不能**用 16384 + 2*index 推算：实测本机
        index 0 占 16384、index 1 是 16416（15.0 引擎另占一段）。
        """
        payload = self._call("info", "-v", "all")
        entries = sorted(payload.items(), key=lambda kv: _index_sort_key(kv[0]))
        return [self._to_instance(idx, raw) for idx, raw in entries]

    def _to_instance(self, idx: str, raw: dict[str, Any]) -> EmulatorInstance:
        host, port = raw.get("adb_host_ip"), raw.get("adb_port")
        serial = f"{host}:{port}" if host and port else None
        return EmulatorInstance(
            info=EmulatorInfo(
                name=raw.get("name", ""),
                serial=serial,
                adb_path=bundled_adb_path(self.bin_path),
                index=str(idx),
                console_path=self.bin_path or "",
            ),
            status=_status_of(raw),
            android_version=_normalize_android_version(raw.get("android_version")),
        )

    def fingerprint(self, idx: int | str) -> str | None:
        """拿不出来 —— 恒为 None，所以 `identify` 会跳过 MuMu。

        下面是实测结论，不是「还没查」（两台 MuMu 同时开着，逐个候选验过）：

        | guest 侧能读到 | 每实例唯一 | MuMu 配置里有对应值 |
        | --- | --- | --- |
        | wlan0 MAC | 是（`08:fb:cf:09:96:e2` vs `08:79:79:1a:cf:72`） | **没有** |
        | `settings get secure android_id` | 是（两台互不相同） | **没有** |
        | `ro.serialno` | —— | 空值 |
        | `service call iphonesubinfo 1` | —— | 抛异常 parcel（`fffffffc`），配置里的 `imei` 读不回来 |

        缺的是「期望值来源」这一半：MuMu 在
        `vms/MuMuPlayer-<版本>-<index>/configs/vm_config.json` 里记了每实例唯一的
        `imei` 和 `ginstance`，但 **guest 里都读不回来**；反过来 guest 里唯一的
        MAC 和 android_id，MuMu 全目录的配置里都没记。指纹必须是两侧同一个值，
        单有一侧等于没有。

        配置里确实有 `vm.phone.miit`，而它**正好等于 guest 的 `ro.product.model`**
        （index 0：PGBM10，index 1：SM-A5560）。但那是个**机型 profile**，不是实例 ——
        两个用同一套手机配置的 MuMu 实例会给出同样的值，所以它只能分厂商、分不了 index。

        MuMu 之所以不需要反查，是因为它从 Console 拿到的 `adb_port` 本身权威且可连：
        实测 `127.0.0.1:16384` / `:16416` 一直可用，而且当雷电占着 `emulator-555X`
        时，MuMu 也就只提供 tcp 地址。雷电的 serial 号段会撞车，所以雷电才需要指纹。

        哪天 MuMu 记下了 guest 的 MAC 或 android_id，这里就可以实现了。在那之前返回
        None 比返回一个只在单实例下成立的猜的值安全 —— 后者会让反查给出错误的 index。
        """
        return None

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
        """MuMu 直接给出进程状态，不需要像雷电那样推断。"""
        return _status_of(self.instance(idx))

    def create(self, name: str | None = None) -> str:
        """新建一台实例，返回它的 index。

        实测（MuMu Player 6.8.2.0，本机已装 12/15 双引擎）：

        - `create --number 1` 的返回体是**按新 index 分键**的：
          `{"2": {"errcode": 0, "errmsg": ""}}` —— 顶层**没有** errcode，
          所以新编号直接就在返回值里，不必靠前后 diff。
        - 新 index 是**最小空闲号**，不是「最大号 +1」。（当时已有 0/1 与一个
          残留的 99，给出的是 2。）
        - 不传 `--version` 时是 auto，实测本机双引擎混装下落到了 **15.0**；
          这里不选版本，让厂商按自己的默认走（要特定版本就改用厂商自己的 CLI）。
        - `--vmindex` 实测**确实有效**（`create --vmindex 99` 真的建在 99），
          但这里**故意不用**：让厂商自己分配编号比我们指定更安全。

        MuMu 的 `create` 不接受名字，所以给了 `name` 就是「建完再改名」两步。
        实测名字**不必唯一**（把一台改成与另一台同名照样 errcode 0），所以
        名字不能当标识，index 才是。
        """
        cmd = ["create", "--number", "1"]
        before = set(self.instances())
        response = self._call(*cmd)
        idx = self._new_index(response, before, cmd)
        if name is not None:
            self.rename(idx, name)
        return idx

    def _new_index(
        self, response: dict[str, Any], before: set[str], cmd: list[str]
    ) -> str:
        """新实例的编号：先从返回值里取，取不到就前后 diff `info -v all`。

        两条路都拿不到就**报错**，绝不返回一个猜的 index —— 调用方拿着它去
        launch 会启动错的机器，比直接失败危险得多。返回值里非编号的键
        （如 errcode）用 isdigit 过滤掉。
        """
        # create / clone 的顶层没有 errcode（那是「整个命令没跑起来」那一档，
        # 已经由 _call 拦下了），但每台新实例的值里还有一个 —— 实测错误就
        # 藏在那里（{"2": {"errcode": -1, "errmsg": "..."}}），必须逐键看。
        errors = [
            raw.get("errmsg") or raw
            for raw in response.values()
            if isinstance(raw, dict) and raw.get("errcode")
        ]
        if errors:
            raise RuntimeError(f"MuMuManager {' '.join(cmd)} 失败：{errors[0]}")

        created = [k for k in map(str, response) if k.isdigit() and k not in before]
        if not created:
            created = [i for i in self.instances() if i not in before]
        if not created:
            raise RuntimeError(
                f"MuMuManager {' '.join(cmd)} 之后没有出现新实例"
                f"（现有 {sorted(before, key=_index_sort_key)}），拿不到新实例的编号。"
            )
        return min(created, key=_index_sort_key)

    def clone(self, idx: int | str, name: str | None = None) -> str:
        """基于 `idx` 复制一台，返回新 index。

        实测（源 `MuMu安卓设备-2`，副本建在 index 3）：返回体与 create 同形
        `{"3": {"errcode": 0, "errmsg": ""}}`，新 index 也是最小空闲号，
        耗时约 1.5s（影子盘，不是整份拷贝）。不给名字时副本的默认名是
        `源名 + "-1"`，例如 `MuMu安卓设备-2-1`。
        """
        cmd = ["clone", "-v", str(idx), "--number", "1"]
        before = set(self.instances())
        response = self._call(*cmd)
        new = self._new_index(response, before, cmd)
        if name is not None:
            self.rename(new, name)
        return new

    def delete(self, idx: int | str) -> None:
        """删除一台实例。**运行中的先停机** —— 这一步由 adapter 编排。

        实测：MuMu 拒绝对运行中的实例执行 delete，
        `{"errcode": -103, "errmsg": "player is running, command not allowed"}`
        （进程退出码 4294967193，即 -103 的 unsigned）。所以这里先 `shutdown`、
        等它真的到 STOP 再删 —— 调用方不必知道这条规矩。

        `delete` 本身是**阻塞**的：实测成功路径耗时约 4.8s，返回时实例目录已
        消失，所以**不需要** `--no_wait`（加了反而要自己做等待）。删完回读
        确认，不留「命令说成功、其实还在」的可能。
        """
        self._shutdown_and_wait(idx)
        self._call("delete", "-v", str(idx))
        if str(idx) in self.instances():
            raise RuntimeError(f"MuMuManager delete -v {idx} 之后实例仍在 info -v all 里。")

    def _shutdown_and_wait(self, idx: int | str) -> None:
        """停机并等它到 STOP。实例已经不存在时直接返回。"""
        try:
            if _status_of(self.instance(idx)) is DeviceStatus.STOP:
                return
        except RuntimeError:
            return

        self.quit_device(idx)
        deadline = time.monotonic() + _STOP_WAIT_SECONDS
        while time.monotonic() < deadline:
            time.sleep(_STOP_POLL_SECONDS)
            try:
                if _status_of(self.instance(idx)) is DeviceStatus.STOP:
                    return
            except RuntimeError:
                return

    def rename(self, idx: int | str, name: str) -> None:
        """改实例名。**运行中也能改**（实测对运行中的实例改同名，errcode 0）。

        实测参数校验（都会带 errcode 透出）：空名字 → -22
        `Missing param <name> value error !!!`；干脆不给 `--name` → -21
        `Missing param <name> error !!!`。

        名字**不必唯一**：实测把 index 3 改成与 index 0 同名的 `A15` 照样成功，
        于是两台重名。名字只是给人看的，index 才是标识。
        """
        self._call("rename", "-v", str(idx), "--name", name)

    def get_settings(self, idx: int | str) -> dict[str, str]:
        """`setting -v <idx> -a`：一条命令拿到整份设置。

        实测（6.8.2.0，本机 index 0）：**扁平 dict，113 个 key，值全是字符串，
        没有嵌套、不用从 errcode 里绕**。其中约 69 个是只读的「当前生效值」
        （`core_version` / `vm_cpu` / `vm_mem` / `resolution_width` /
        `resolution_height` / `resolution_dpi`），可写的是对应的 `.custom`
        （期望值）；可写 key 的数量**随实例而变**（本机 index 0 量到 44 个，
        index 1 是 46 个）。

        想知道某个 key 可不可写、有哪些合法取值，用
        `setting -v <idx> -k <key> -i`（返回 desc / option_values / readable /
        writable）—— 本库没有包装它。另有 `-aw`（只要可写的）也是同一条命令，
        本层一律用 `-a`：读全量比读子集更少意外。
        """
        payload = self._call("setting", "-v", str(idx), "-a")
        return {str(key): str(value) for key, value in payload.items()}

    def _write_settings(self, idx: int | str, kv: dict[str, str]) -> None:
        """一次调用写多个 key：`setting -v <idx> -k K1 -val V1 -k K2 -val V2`。

        实测（本机）：key/value 可以重复出现，一次写完多个；返回值是**回显**刚写
        进去的那些键值（不是 `{"errcode": 0}`），所以不用自己解析 —— `_call`
        对非零 errcode 抛异常，写不进去时它给的是
        `{"errcode": -101, "errmsg": "key not writable"}`。

        **必须带 `-v <idx>`**：不带 `-v` 是改**全局设置**，不是这台实例的。那是
        另一个能力，不该从 `set_settings(idx, ...)` 里悄悄发生。

        清空某个 key 用 `-val __null__`（实测有效），本库不拦 —— 它是厂商方言的
        一部分。
        """
        cmd = ["setting", "-v", str(idx)]
        for key, value in kv.items():
            cmd += ["-k", key, "-val", value]
        self._call(*cmd)

    def set_resolution(self, idx: int | str, width: int, height: int, dpi: int) -> None:
        """四个 key 一次写完：`resolution_mode=custom` + 三个 `.custom`。

        三个数值都先用只读的 `resolution_width.min/max`（本机 380..4096）、
        `resolution_height.min/max`、`resolution_dpi.min/max`（本机 10..960）查范围
        —— 这是**必需**的：实测超范围的宽高/dpi 是 **rc 0 且不报错**，厂商只把它
        夹到边界（宽 100 → 回读 380，99999 → 4096，dpi 5000 → 960）。不管就等于
        悄悄给人另一个分辨率。

        `resolution_mode` 必须一起置 `custom`，否则写了 `.custom` 也不生效。
        """
        settings = self.get_settings(idx)
        _bounded("width", width, _RESOLUTION_BOUNDS, settings)
        _bounded("height", height, _RESOLUTION_BOUNDS, settings)
        _bounded("dpi", dpi, _RESOLUTION_BOUNDS, settings)
        self.set_settings(
            idx,
            resolution_mode="custom",
            **{
                "resolution_width.custom": str(width),
                "resolution_height.custom": str(height),
                "resolution_dpi.custom": str(dpi),
            },
        )

    def set_cpu(self, idx: int | str, cores: int) -> None:
        """先读 `performance_cpu.list` 再核 —— 可选值**随宿主机变**，不能写死。

        实测本机 `[1,2,...,16](best=4)`，但把 1..16 写在代码里，在 CPU 少的机器上
        就会把厂商支持得起的值拦成错误。越界时用厂商列出来报错，那句话才说得清
        **哪个值行**（厂商自己的 `-105 cpu setting not found in list` 说不出）。
        """
        settings = self.get_settings(idx)
        candidates = _list_values(settings.get(_CPU_LIST_KEY, ""))
        if str(cores) not in candidates:
            raise ValueError(
                f"CPU 核数只能是 {candidates} 之一（这台机器的 {_CPU_LIST_KEY}），"
                f"给的是 {cores}。"
            )
        self.set_settings(idx, performance_mode="custom", **{"performance_cpu.custom": str(cores)})

    def set_memory(self, idx: int | str, megabytes: int) -> None:
        """中立层收 MB，MuMu 存 **GB**（实测 `performance_mem.custom = "1.750000"`）。

        档位从 `performance_mem.list` 读（本机 `[0.75,1,1.5,1.75,2,3,...,16]` GB），
        换成分成 MB 档位后**就近取值**（并列取大）：落在两档之间时不报错，因为
        两家本来就只认离散档位；整个范围外才抛 ValueError。

        厂商自己也会拒（实测 `performance_mem.custom=999` →
        `-106 mem setting not found in list`），但读一次档位才说得清**哪个值行**。
        """
        settings = self.get_settings(idx)
        picked = _nearest(_mem_list_mb(settings), float(megabytes), what="内存")
        self.set_settings(
            idx,
            performance_mode="custom",
            **{"performance_mem.custom": f"{picked / 1024:.6f}"},
        )

    def set_root(self, idx: int | str, enabled: bool) -> None:
        """`root_permission` 本身就是 true/false —— 两家差异最小的一个动词。"""
        self.set_settings(idx, root_permission="true" if enabled else "false")

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
