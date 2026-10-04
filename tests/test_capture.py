"""抓包工具的 characterization tests。

Iptables 的命令拼装值得锁：它经过 ADB.build_su_cmd 加 su 前缀，而历史上
INPUT 那条规则被重复加了两次 su，命令根本执行不了。没有真机也能验证 ——
FakeADB 未声明的命令会直接报错，所以这份测试必须把 Iptables 会下发的每一条
命令都写出来。
"""

import os

from androtools.cmd.result import CmdResult
from helpers import make_emulator_info
from androtools.core.shell import AndroidShell
from androtools.testing import FakeADB
from androtools.utils import Iptables, Tcpdump

SERIAL = "127.0.0.1:5555"
USER_ID = "10123"
PCAP_ON_DEVICE = "/data/local/tmp/net.pcap"

DUMP = ("dumpsys", "package", "com.example")
SAVE = ("su", "0", "iptables-save", ">", "/data/local/tmp/iptables.rules")
RESTORE = ("su", "0", "iptables-restore", "<", "/data/local/tmp/iptables.rules")
FLUSH = ("su", "0", "iptables", "-F")

MARK_OUTPUT = (
    "su",
    "0",
    "iptables",
    "-A",
    "OUTPUT",
    "-m",
    "owner",
    "--uid-owner",
    USER_ID,
    "-j",
    "CONNMARK",
    "--set-mark",
    USER_ID,
)
LOG_INPUT = (
    "su",
    "0",
    "iptables",
    "-A",
    "INPUT",
    "-m",
    "connmark",
    "--mark",
    USER_ID,
    "-j",
    "NFLOG",
    "--nflog-group",
    USER_ID,
)
LOG_OUTPUT = (
    "su",
    "0",
    "iptables",
    "-A",
    "OUTPUT",
    "-m",
    "connmark",
    "--mark",
    USER_ID,
    "-j",
    "NFLOG",
    "--nflog-group",
    USER_ID,
)
PULL = ("pull", PCAP_ON_DEVICE, os.path.join("out", "net.pcap"))

ALL_COMMANDS = [DUMP, MARK_OUTPUT, LOG_INPUT, LOG_OUTPUT, SAVE, RESTORE, FLUSH, PULL]


def make_shell(
    dump_output: str = f"    userId={USER_ID}\n",
) -> tuple[AndroidShell, FakeADB]:
    responses: dict[tuple[str, ...], CmdResult] = {
        cmd: CmdResult("", "") for cmd in ALL_COMMANDS
    }
    responses[DUMP] = CmdResult(dump_output, "")
    responses[PULL] = CmdResult("1 file pulled", "")
    adb = FakeADB(responses=responses)
    info = make_emulator_info()
    return AndroidShell(adb, info), adb


def test_add_reads_the_user_id_from_dumpsys():
    shell, _ = make_shell()

    assert Iptables(shell).add("com.example") == USER_ID


def test_add_never_double_prefixes_su():
    """回归测试：INPUT 那条规则曾经多带了一个 su 0，命令根本跑不起来。"""
    shell, adb = make_shell()
    iptables = Iptables(shell)

    iptables.add("com.example")

    privileged = [c for c in adb.commands("run_shell_cmd") if "su" in c]
    assert privileged
    for cmd in privileged:
        assert cmd[:2] == ("su", "0"), cmd
        assert cmd.count("su") == 1, cmd


def test_add_marks_the_app_uid_and_logs_only_its_traffic():
    shell, adb = make_shell()
    iptables = Iptables(shell)

    iptables.add("com.example")

    assert adb.commands("run_shell_cmd") == [
        DUMP,
        MARK_OUTPUT,
        LOG_INPUT,
        LOG_OUTPUT,
        SAVE,
        RESTORE,
    ]


def test_clear_flushes_the_ruleset_and_saves_it():
    shell, adb = make_shell()

    Iptables(shell).clear()

    assert adb.commands("run_shell_cmd") == [FLUSH, SAVE, RESTORE]


def test_tcpdump_pulls_the_capture_where_it_was_asked():
    shell, adb = make_shell()

    Tcpdump(shell, "com.example").pull_pcap_file("out")

    assert adb.commands("run_cmd") == [PULL]