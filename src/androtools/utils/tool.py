import os
import time
from androtools.core.device import Device
from androtools.android_sdk.platform_tools import ADB


class Iptables:
    """

    参考：
    https://evilpan.com/2023/01/30/android-iptables/
    https://zhuanlan.zhihu.com/p/419923518
    """

    def __init__(self, device: Device, package_name: str, uid: str):
        self.device = device
        self.package_name = package_name
        self.uid = uid

    def reload(self):
        self.device.adb_shell(
            ADB.build_su_cmd(["iptables-save", ">", "/data/local/tmp/iptables.rules"])
        )
        self.device.adb_shell(
            ADB.build_su_cmd(
                ["iptables-restore", "<", "/data/local/tmp/iptables.rules"]
            )
        )

    def add(self):
        # iptables -A OUTPUT -m owner --uid-owner $2 -j CONNMARK --set-mark 1
        self.device.adb_shell(
            ADB.build_su_cmd(
                [
                    "iptables",
                    "-A",
                    "OUTPUT",
                    "-m",
                    "owner",
                    "--uid-owner",
                    self.uid,
                    "-j",
                    "CONNMARK",
                    "--set-mark",
                    self.uid,
                ]
            )
        )
        # iptables -A INPUT -m connmark --mark 1 -j NFLOG --nflog-group 30
        self.device.adb_shell(
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
                    self.uid,
                    "-j",
                    "NFLOG",
                    "--nflog-group",
                    self.uid,
                ]
            )
        )
        # iptables -A OUTPUT -m connmark --mark 1 -j NFLOG --nflog-group 30
        self.device.adb_shell(
            ADB.build_su_cmd(
                [
                    "iptables",
                    "-A",
                    "OUTPUT",
                    "-m",
                    "connmark",
                    "--mark",
                    self.uid,
                    "-j",
                    "NFLOG",
                    "--nflog-group",
                    self.uid,
                ]
            )
        )
        self.reload()

    def clear(self):
        self.device.adb_shell(ADB.build_su_cmd(["iptables", "-F"]))
        self.reload()


class Tcpdump:
    """对某个应用进行抓包"""

    def __init__(self, device: Device, package_name: str):
        self.device = device
        self.package_name = package_name
        self.pcap_path = "/data/local/tmp/net.pcap"

    def setting_iptable(self):
        # dumpsys package $2 | grep userId | sed "s/[ \t]*userId=//g"
        r, _ = self.device.adb_shell(["dumpsys", "package", self.package_name])
        self.user_id = r.split("userId=")[1].split("\n")[0]
        self.iptables = Iptables(self.device, self.package_name, self.user_id)
        self.iptables.add()
        time.sleep(3)

    def start_capture(self):
        self.setting_iptable()
        self.device.adb_shell_daemon(
            ADB.build_su_cmd(
                [
                    "tcpdump",
                    "-i",
                    f"nflog:{self.user_id}",
                    "-U",
                    "-w",
                    self.pcap_path,
                    "&",
                ]
            )
        )

    def stop_capture(self):
        self.device.adb_shell(ADB.build_su_cmd(["killall", "tcpdump"]))
        self.iptables.clear()

    def pull_pcap_file(self, output: str, name: str = "net.pcap"):
        self.device.pull(self.pcap_path, os.path.join(output, name))
