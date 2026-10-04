"""测试替身。

放在包里而不是 tests/ 里，是因为 Device 是使用者代码的 base class —— 想测自己
的子类就需要一个 adb 的假实现，不必每个人都重写一遍。

这里只有通用的部分（按命令脚本化响应、记录调用序列）。某个具体测试需要的编排，
例如「让 Device 走完启动流程」，应该留在那个测试自己的目录里。
"""

import subprocess
from collections.abc import Mapping
from dataclasses import dataclass

from androtools.cmd.result import CmdResult


@dataclass(frozen=True)
class AdbCall:
    """一次 adb 调用。"""

    method: str
    cmd: list[str]
    serial: str | None
    timeout: int | None

    def __str__(self) -> str:
        return f"{self.method}({' '.join(self.cmd)}) serial={self.serial} timeout={self.timeout}"


class FakeADB:
    """AdbRunner 的第二个 adapter。

    未声明的命令会直接抛错，而不是返回空结果 —— 这样「代码多调了一次 adb」
    会变成红灯，而不是悄悄通过。

    Args:
        responses: 命令元组到响应的映射。键是传给 AdbRunner 的命令 list
            （`run_shell_cmd` 不含 `shell` 前缀）。值是 CmdResult；用 None
            表示「这条命令只需要被记录，不需要响应」。
        errors: 命令元组到异常的映射。命中的命令抛出该异常而不是返回响应。

    Examples:
        >>> adb = FakeADB(responses={("get-state",): CmdResult("device", "")})
        >>> adb.run_cmd(["get-state"], serial="127.0.0.1:5555").output
        'device'
        >>> adb.calls
        [AdbCall(method='run_cmd', cmd=['get-state'], serial='127.0.0.1:5555', timeout=30)]
    """

    def __init__(
        self,
        responses: Mapping[tuple[str, ...], CmdResult | None] | None = None,
        errors: Mapping[tuple[str, ...], BaseException] | None = None,
        devices: list[tuple[str, str, str]] | None = None,
    ) -> None:
        self._responses = dict(responses or {})
        self._errors = dict(errors or {})
        self._devices = list(devices or [])
        self.calls: list[AdbCall] = []

    def get_devices(self) -> list[tuple[str, str, str]]:
        return list(self._devices)

    def run_cmd(
        self, cmd: list[str], serial: str | None = None, timeout: int = 30
    ) -> CmdResult:
        return self._resolve_required("run_cmd", cmd, serial, timeout)

    def run_shell_cmd(
        self, cmd: list[str], serial: str | None = None, timeout: int = 30
    ) -> CmdResult:
        return self._resolve_required("run_shell_cmd", cmd, serial, timeout)

    def run_shell_cmd_daemon(
        self, cmd: list[str], serial: str | None = None
    ) -> None:
        self._record("run_shell_cmd_daemon", cmd, serial, None)
        self._resolve(cmd)
        return None

    def commands(self, method: str | None = None) -> list[tuple[str, ...]]:
        """按调用顺序返回命令元组。method 用于只看某一类调用。"""
        return [
            tuple(c.cmd) for c in self.calls if method is None or c.method == method
        ]

    def count(self, *cmd: str) -> int:
        """某条命令被调用了几次。

        重复调用往往是 bug —— 例如 Device.get_status() 目前会连续调两次
        `get-state`，且第一次的结果被丢弃。
        """
        return self.commands().count(tuple(cmd))

    def _record(
        self, method: str, cmd: list[str], serial: str | None, timeout: int | None
    ) -> None:
        self.calls.append(AdbCall(method, list(cmd), serial, timeout))

    def _resolve(self, cmd: list[str]) -> CmdResult | None:
        key = tuple(cmd)
        if key not in self._responses and key not in self._errors:
            declared = sorted(" ".join(k) for k in (*self._responses, *self._errors))
            raise AssertionError(
                f"FakeADB 未声明命令 {' '.join(key)}。\n"
                f"已声明：{declared or '（无）'}"
            )
        if key in self._errors:
            raise self._errors[key]
        return self._responses.get(key)

    def _resolve_required(
        self, method: str, cmd: list[str], serial: str | None, timeout: int | None
    ) -> CmdResult:
        self._record(method, cmd, serial, timeout)
        result = self._resolve(cmd)
        if result is None:
            raise AssertionError(
                f"{method} 需要一个 CmdResult，但命令 {' '.join(cmd)} "
                "的 responses 声明为 None；只有 *_daemon 命令可以这样声明。"
            )
        return result