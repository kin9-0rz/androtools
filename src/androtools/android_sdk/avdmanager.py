import shutil

from androtools.android_sdk import CMD


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
        self.build("--force")

    def name(self, name):
        self.build_args(["--name", name])

    def device(self, device):
        self.build_args(["--device", device])

    def abi(self, abi):
        self.build_args(["--abi", abi])

    def package(self, package):
        self.build_args(["--package", package])

    def path(self, path):
        self.build_args(["--path", path])

    def snapshot(self, snapshot):
        self.build_args(["--snapshot", snapshot])

    def sdcard(self, sdcard):
        self.build_args(["--sdcard", sdcard])
