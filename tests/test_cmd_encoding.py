"""厂商工具的输出编码。

MuMuManager 与 ldconsole 都不按本机 locale 输出：前者是 UTF-8，后者是 GBK。
在中文 Windows 上 locale 是 cp936 —— 正好解对雷电、解错 MuMu。这不是厂商的锅，
是解码时选错了编码，所以每个 adapter 自己声明一个 `encoding`。
"""

import sys

import pytest

from androtools.cmd.abc import CMD

from androtools.core.ld import LDConsole
from androtools.core.mumu import MumuConsole

#: 一段中文，用来验证解码路径。写成字节是因为要控制子进程吐出的确切编码。
CHINESE = "测试"


def _echo(encoding: str) -> list[str]:
    """一段让子进程按指定编码把 CHINESE 写到 stdout 的 -c 脚本。"""
    return ["-c", f"import sys; sys.stdout.buffer.write({CHINESE!r}.encode({encoding!r}))"]


class _Echo(CMD):
    """只用来跑 python -c 的 CMD，方便测 _run 的解码。"""

    def __init__(self, default_encoding: str | None = None) -> None:
        super().__init__(sys.executable)
        self.encoding = default_encoding


def test_cmd_run_decodes_with_the_console_default_encoding():
    """没显式传 encoding 时，用 console 自己声明的那个。"""
    result = _Echo("utf-8")._run(_echo("utf-8"))
    assert result.output == CHINESE


def test_cmd_run_falls_back_to_the_locale_when_nothing_is_declared():
    """什么也没声明时，保持 subprocess 的默认行为（本机 locale）。"""
    import locale

    result = _Echo(None)._run(_echo(locale.getpreferredencoding(False)))
    assert result.output == CHINESE


def test_cmd_run_lets_an_explicit_encoding_win():
    """显式传的 encoding 覆盖 console 的默认值。"""
    result = _Echo("utf-8")._run(_echo("utf-8"), encoding="latin-1")
    assert result.output != CHINESE


def test_mumu_console_declares_utf8():
    """MuMuManager 实测输出 UTF-8。"""
    assert MumuConsole("MuMuManager.exe").encoding == "utf-8"


def test_ld_console_declares_gbk():
    """ldconsole 实测输出 GBK。"""
    assert LDConsole("ldconsole.exe").encoding == "gbk"


def test_the_base_console_does_not_guess_for_the_vendor():
    """接口自己不给默认编码 —— 由厂商 adapter 声明。"""
    assert CMD("does-not-exist.exe").encoding is None


@pytest.mark.parametrize("console_cls", [MumuConsole, LDConsole])
def test_consoles_do_not_depend_on_the_machine_locale(console_cls):
    """两个厂商的编码都是写死的，与本机 locale 无关。"""
    console = console_cls("does-not-exist.exe")
    assert console.encoding is not None
    assert console.bin_path is None
