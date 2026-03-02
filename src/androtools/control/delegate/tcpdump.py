import os
import time

from androtools.android_sdk.adb import ADB
from androtools.control.delegate import ABCDelegate
from androtools.device.abc import DeviceADB


class IptableDelegate:
    """设置代理"""

    """

    参考：
    https://evilpan.com/2023/01/30/android-iptables/
    https://zhuanlan.zhihu.com/p/419923518
    """

    def __init__(self, adb: DeviceADB) -> None:
        self.adb = adb

    def reload(self):
        self.adb.adb_shell(
            ADB.build_su_cmd(["iptables-save", ">", "/data/local/tmp/iptables.rules"])
        )
        self.adb.adb_shell(
            ADB.build_su_cmd(
                ["iptables-restore", "<", "/data/local/tmp/iptables.rules"]
            )
        )

    def clear(self):
        self.adb.adb_shell(ADB.build_su_cmd(["iptables", "-F"]))
        self.reload()

    def add(self, package_name: str) -> str:
        # dumpsys package $2 | grep userId | sed "s/[ \t]*userId=//g"
        r = self.adb.adb_shell(["dumpsys", "package", package_name]).output
        user_id = r.split("userId=")[1].split("\n")[0]

        # iptables -A OUTPUT -m owner --uid-owner $2 -j CONNMARK --set-mark 1
        self.adb.adb_shell(
            ADB.build_su_cmd(
                [
                    "iptables",
                    "-A",
                    "OUTPUT",
                    "-m",
                    "owner",
                    "--uid-owner",
                    user_id,
                    "-j",
                    "CONNMARK",
                    "--set-mark",
                    user_id,
                ]
            )
        )
        # iptables -A INPUT -m connmark --mark 1 -j NFLOG --nflog-group 30
        self.adb.adb_shell(
            ADB.build_su_cmd(
                [
                    "su",
                    "0",
                    "iptables",
                    "-A",
                    "INPUT",
                    "-m",
                    "connmark",
                    "--mark",
                    user_id,
                    "-j",
                    "NFLOG",
                    "--nflog-group",
                    user_id,
                ]
            )
        )
        # iptables -A OUTPUT -m connmark --mark 1 -j NFLOG --nflog-group 30
        self.adb.adb_shell(
            ADB.build_su_cmd(
                [
                    "iptables",
                    "-A",
                    "OUTPUT",
                    "-m",
                    "connmark",
                    "--mark",
                    user_id,
                    "-j",
                    "NFLOG",
                    "--nflog-group",
                    user_id,
                ]
            )
        )
        self.reload()

        return user_id


class TcpdumpDelegate(ABCDelegate):
    """对某个应用进行抓包，需要Root权限。"""

    def __init__(self, adb: DeviceADB):
        super().__init__(adb)
        self.pcap_path = "/data/local/tmp/net.pcap"

    def set_package_name(self, package_name: str):
        self.package_name = package_name
        self.iptables = IptableDelegate(self.adb)

    def start_capture(self):
        user_id = self.iptables.add(self.package_name)
        time.sleep(1)
        self.adb.adb_shell_daemon(
            ADB.build_su_cmd(
                [
                    "tcpdump",
                    "-i",
                    f"nflog:{user_id}",
                    "-U",
                    "-w",
                    self.pcap_path,
                    "&",
                ]
            )
        )

    def stop_capture(self):
        self.adb.adb_shell(ADB.build_su_cmd(["killall", "tcpdump"]))
        self.iptables.clear()

    def pull_pcap_file(self, output: str, name: str = "net.pcap"):
        self.adb.pull(self.pcap_path, os.path.join(output, name))
