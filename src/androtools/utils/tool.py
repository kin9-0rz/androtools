import os

from androtools.core.device import Device


class Iptables:
    """

    参考：https://evilpan.com/2023/01/30/android-iptables/
    """

    def __init__(self, device: Device, package_name: str):
        self.device = device
        self.package_name = package_name

    def reload(self):
        self.device.adb_shell(["iptables-save", ">", "/data/local/tmp/iptables.rules"])
        self.device.adb_shell(
            ["iptables-restore", "<", "/data/local/tmp/iptables.rules"]
        )

    def add(self):
        # dumpsys package $2 | grep userId | sed "s/[ \t]*userId=//g"
        r, _ = self.device.adb_shell(["dumpsys", "package", self.package_name])
        # 获取包名的UID
        userid = r.split("userId=")[1].split("\n")[0]
        self.device.adb_shell(
            [
                "iptables",
                "-A",
                "OUTPUT",
                "-m",
                "owner",
                "--uid-owner",
                userid,
                "-j",
                "CONNMARK",
                "--set-mark",
                "1",
            ]
        )
        # iptables -A INPUT -m connmark --mark 1 -j NFLOG --nflog-group 30
        self.device.adb_shell(
            [
                "iptables",
                "-A",
                "INPUT",
                "-m",
                "connmark",
                "--mark",
                "1",
                "-j",
                "NFLOG",
                "--nflog-group",
                "30",
            ]
        )
        # iptables -A OUTPUT -m connmark --mark 1 -j NFLOG --nflog-group 30
        self.device.adb_shell(
            [
                "iptables",
                "-A",
                "OUTPUT",
                "-m",
                "connmark",
                "--mark",
                "1",
                "-j",
                "NFLOG",
                "--nflog-group",
                "30",
            ]
        )
        self.reload()

    def clear(self):
        self.device.adb_shell(["iptables", "-F"])
        self.reload()


class Tcpdump:
    """对某个应用进行抓包"""

    def __init__(self, device: Device, package_name: str):
        self.device = device
        self.package_name = package_name
        self.iptables = Iptables(device, package_name)

    def start_capture(self):
        self.iptables.add()
        self.device.adb_shell_daemon(
            ["tcpdump", "-i", "nflog:30", "-U", "-w", "/data/local/tmp/net.pcap&"]
        )

    def stop_capture(self):
        self.device.adb_shell(["killall", "tcpdump"])
        self.iptables.clear()

    def pull_pcap_file(self, output: str, name: str = "net.pcap"):
        self.device.pull("/data/local/tmp/net.pcap", os.path.join(output, name))
