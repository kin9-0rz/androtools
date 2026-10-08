"""反查：adb 上的一个 serial，是哪个厂商的哪个实例。

为什么要有它：雷电和 MuMu 的 serial 落在同一个号段（`emulator-5554` 起），
而两家的号会撞。实测两台雷电 + 两台 MuMu 同时开着时，`emulator-5556` 归
MuMu index 1 —— 正是雷电 index 1 该去的位置，雷电 index 1 在 adb 里根本不出现。

更麻烦的是问厂商工具问不出来：雷电官方的 `ldconsole adb --index 1 --command
"shell getprop ro.product.model"` 在那个环境下返回 `SM-A5560`，也就是 MuMu 的
设备，而且**不报任何错**。所以反查只能拿实例指纹自己核对。

这里也提供 `discover()` —— 正查（列出在线设备）。它按 **wlan0 MAC 去重**，
因为同一台机器在 adb 上可能有多个名字：MuMu 会自己注册一个 `emulator-555X`，
而 `MumuPlayer.ensure_connected()` 还会 `adb connect` 它自己那个
`127.0.0.1:<adb_port>`。实测跑一次 `MumuPlayer.get_status()` 之后，adb 上 4 个
条目只对应 3 台机器 —— 同一台的 `127.0.0.1:16416` 与 `emulator-5556` 的 MAC 都是
`0879791ACF72`。去重后 `DiscoveredDevice.serials` 会把两个名字都留着，用户拿哪个来都能用。
"""

from dataclasses import dataclass
from typing import Sequence

from androtools import logger
from androtools.android_sdk.platform_tools import AdbRunner
from androtools.core.device import EmulatorConsole

#: 问设备要指纹用的 guest 命令。取 wlan0 的 MAC —— 雷电把它注入到那里。
IDENTITY_CMD = ["ip", "addr", "show", "wlan0"]


@dataclass(frozen=True)
class InstanceIdentity:
    """认出之后的结果：一个 serial 属于哪个厂商的哪个实例。"""

    vendor: str
    """厂商名，调用方自己给的（`"雷电"`、`"MuMu"`），库不做校验。"""

    index: str
    """厂商 Console 里的实例编号。"""

    serial: str
    """当初被问的那个 adb serial。"""


def normalize_mac(value: str) -> str:
    """MAC 归一化成大写无分隔。

    雷电配置里写的是 `00DB48FD6270`，guest 里 `ip addr` 吐的是
    `00:db:48:fd:62:70` —— 同一台机器，两种写法。
    """
    return value.replace(":", "").replace("-", "").strip().upper()


def parse_mac(ip_addr_output: str) -> str | None:
    """从 `ip addr show` 的输出里取出第一个 MAC。取不到返回 None。

    token 是 `link/ether` / `link/none` 这类（不是 `link/`），后面一个才是地址。
    """
    for line in ip_addr_output.splitlines():
        parts = line.split()
        for position, token in enumerate(parts[:-1]):
            if token.startswith("link/") and parts[position + 1] not in {"", "(null)"}:
                return normalize_mac(parts[position + 1])
    return None


def identify(
    adb: AdbRunner,
    consoles: Sequence[tuple[str, EmulatorConsole]],
    serial: str,
) -> InstanceIdentity | None:
    """认出 `serial` 是哪个厂商的哪个实例。认不出返回 None。

    认不出不是错误 —— 真机、没装在列表里的模拟器、以及指纹读不出来的厂商
    （`fingerprint()` 返回 None）都属于这一类。

    指纹比对是内存操作，所以成本只在于**向目标设备问一次 MAC**。

    Args:
        adb: 用来问设备指纹的 adb runner。
        consoles: (厂商名, Console) 的列表，按顺序挨个问，第一个认出的即返回。
        serial: 要认的 adb serial。

    Returns:
        与该 serial 指纹相符的实例；对不上或问不到则为 None。
    """
    expected = _mac_of(adb, serial)
    if expected is None:
        return None

    for vendor, console in consoles:
        for index in console.instances():
            fingerprint = console.fingerprint(index)
            # 指纹为 None 的厂商要跳过，不能拿它当期望值 —— 那会让
            # 「读不出指纹」变成「匹配任何东西」。
            if fingerprint is None:
                continue
            if normalize_mac(fingerprint) == expected:
                return InstanceIdentity(vendor=vendor, index=str(index), serial=serial)

    return None


def _mac_of(adb: AdbRunner, serial: str) -> str | None:
    """问一台设备它的 wlan0 MAC。问不到返回 None，不抛。

    adb devices 会列出问不动的东西（离线、正在重启）—— 那台跳过即可，
    不该因为它让整条反查失败。
    """
    try:
        output = adb.run_shell_cmd(IDENTITY_CMD, serial).output
    except Exception as e:
        logger.debug(f"[identify] 问 {serial} 的 MAC 失败：{e}")
        return None
    return parse_mac(output)


@dataclass(frozen=True)
class DiscoveredDevice:
    """在线的一台物理设备。"""

    name: str
    """读到的机型（`ro.product.model`）；读不到就用 primary serial。"""

    serial: str
    """主 serial —— `adb devices` 里列在前面的那个。构造 `Device` 用它。"""

    serials: list[str]
    """这一台机器在 adb 上的**全部**名字。

    通常只有一个。会多于一个的情况实测存在：MuMu 会自己注册一个 `emulator-555X`，
    而 `MumuPlayer.ensure_connected()` 还会 `adb connect` 它自己那个
    `127.0.0.1:<adb_port>` —— 同一台机器于是有两个名字。跑一次
    `MumuPlayer.get_status()` 之后，adb 上 4 个条目只对应 3 台机器，其中
    `127.0.0.1:16416` 与 `emulator-5556` 的 wlan0 MAC 都是 `0879791ACF72`。
    """

    identity: InstanceIdentity | None
    """认得出的话，是哪个厂商的哪个实例。认不出为 None。"""


def discover(
    adb: AdbRunner,
    consoles: Sequence[tuple[str, EmulatorConsole]] = (),
) -> list[DiscoveredDevice]:
    """列出在线的物理设备，一台机器算一台。

    按 wlan0 MAC 分组去重 —— 实测同一台机器的多个 adb 名字 MAC 相同，而不同机器
    一定不同（哪怕型号一样）。

    Args:
        adb: 用来查在线设备和问机型、指纹的 adb runner。
        consoles: (厂商名, Console) 的列表。给了才会尝试认厂商与实例；不给就只
            列出设备，`identity` 全是 None。

    Returns:
        顺序同 `adb devices` 里各组首次出现的顺序。
    """
    groups: dict[str, list[str]] = {}
    order: list[str] = []

    for serial, _status, _transport in adb.get_devices():
        mac = _mac_of(adb, serial)
        # 问不到 MAC 的设备（离线、正在重启）单独成组。它确实在线，
        # 不能因为问不动就从列表里消失 —— 那会让用户以为设备没了。
        key = mac or f"unreachable:{serial}"
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(serial)

    discovered = []
    for key in order:
        serials = groups[key]
        primary = serials[0]
        discovered.append(
            DiscoveredDevice(
                name=_model_of(adb, primary) or primary,
                serial=primary,
                serials=list(serials),
                identity=identify(adb, consoles, primary),
            )
        )
    return discovered


def _model_of(adb: AdbRunner, serial: str) -> str | None:
    """读机型。读不到返回 None —— 型号只是拿来给人看的，认不出不影响功能。"""
    try:
        return adb.run_shell_cmd(["getprop", "ro.product.model"], serial).output.strip()
    except Exception as e:
        logger.debug(f"[discover] 问 {serial} 的机型失败：{e}")
        return None