import shutil

from androtools.android_sdk import CMD
from androtools.android_sdk.abc import SubSubCommand


class AAPT:
    def __init__(self):
        self.aapt_path = shutil.which("aapt")


class Dump(SubSubCommand):
    permissions = ["dump", "permissions"]
    """aapt2 dump permissions <apk>"""
    badging = ["dump", "badging"]
    """aapt2 dump badging <apk>"""
    packagename = ["dump", "packagename"]
    strings = ["dump", "strings"]
    styleparents = ["dump", "styleparents"]
    resources = ["dump", "resources"]
    chunks = ["dump", "chunks"]
    xmlstrings = ["dump", "xmlstrings"]
    xmltrees = ["dump", "xtrees"]
    overlayable = ["dump", "overlayable"]


class AAPT2(CMD):
    def __init__(self, path=shutil.which("aapt2")) -> None:
        super().__init__(path)

    def dump(self, sub_cmd: Dump, args: list):
        cmd = sub_cmd.value + args
        return self._run(cmd)


class ApkSigner:
    def __init__(self):
        self.bin_path = shutil.which("apksigner")


class DexDump:
    def __init__(self):
        self.bin_path = shutil.which("dexdump")
