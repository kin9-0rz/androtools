import shutil

from androtools.android_sdk import CMD, SubSubCommand


class AAPT:
    def __init__(self):
        self.aapt_path = shutil.which("aapt")


class AAPT2(CMD):
    class Dump(SubSubCommand):
        permissions = ["dump", "permissions"]
        badging = ["dump", "badging"]
        packagename = ["dump", "packagename"]
        strings = ["dump", "strings"]
        styleparents = ["dump", "styleparents"]
        resources = ["dump", "resources"]
        chunks = ["dump", "chunks"]
        xmlstrings = "dump", "xmlstrings"
        xmltrees = ["dump", "xtrees"]
        overlayable = ["dump", "overlayable"]

    def __init__(self, path=shutil.which("aapt2")) -> None:
        super().__init__(path)


class ApkSigner:
    def __init__(self):
        self.bin_path = shutil.which("apksigner")


class DexDump:
    def __init__(self):
        self.bin_path = shutil.which("dexdump")
