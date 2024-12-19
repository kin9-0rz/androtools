from enum import Enum
import shutil

from androtools.android_sdk import CMD


class AAPT:
    def __init__(self):
        self.aapt_path = shutil.which("aapt")


class Dump(Enum):
    permissions = ["dump", "permissions"]
    badging = ["dump", "badging"]
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
        return sub_cmd.value + args


class ApkSigner:
    def __init__(self):
        self.bin_path = shutil.which("apksigner")


class DexDump:
    def __init__(self):
        self.bin_path = shutil.which("dexdump")
