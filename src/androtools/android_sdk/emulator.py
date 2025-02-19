import shutil

from androtools.android_sdk import CMD


class Emulator(CMD):
    def __init__(self, path: str | None = None):
        if path is None:
            path = shutil.which("emulator")

        assert path is not None
        super().__init__(path)

    def build_tcpdump(self):
        self.build("-tcpdump")

    def avd(self, name: str):
        self.build_args(["-avd", name])

    def noskin(self):
        self.build("-noskin")

    def noaudio(self):
        self.build("-noaudio")

    def no_window(self):
        self.build("-no-window")

    def no_boot_anim(self):
        self.build("-no-boot-anim")

    def start_avd(self, avd_name: str):
        # self._run(["@" + avd_name])
        # self._run(["-avd", avd_name])
        self.build_args(["-avd", avd_name])
        self.run_daemon()

    def list_avds(self):
        self.build("-list-avds")
        return self.run()
