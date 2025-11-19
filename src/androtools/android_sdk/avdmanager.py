import shutil

from androtools.cmd import CMD


class AVDInfo:
    name: str


class AVDManager(CMD):
    def __init__(self, path=shutil.which("avdmanager")) -> None:
        super().__init__(path)

    def delete_avd(self, name):
        return self._run(["delete", "avd", "--name", name])

    def list_avd(self):
        return self._run(["list", "avd"])

    def list_target(self):
        return self._run(["list", "target"])

    def list_device(self):
        return self._run(["list", "device"])


class CreateAVD(CMD):
    def __init__(self, path=shutil.which("avdmanager")) -> None:
        super().__init__(path)
        self.reset()

    def reset(self):
        self._args = ["create", "avd"]

    def force(self):
        self.append_args(["--force"])

    def name(self, name):
        self.append_args(["--name", name])

    def device(self, device):
        self.append_args(["--device", device])

    def abi(self, abi):
        self.append_args(["--abi", abi])

    def package(self, package):
        self.append_args(["--package", package])

    def path(self, path):
        self.append_args(["--path", path])

    def snapshot(self, snapshot):
        self.append_args(["--snapshot", snapshot])

    def sdcard(self, sdcard):
        self.append_args(["--sdcard", sdcard])
