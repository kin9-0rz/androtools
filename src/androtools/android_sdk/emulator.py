import shutil

from androtools.cmd import CMD


class Emulator(CMD):
    def __init__(self, path: str | None = None):
        if path is None:
            path = shutil.which("emulator")

        assert path is not None
        super().__init__(path)

    def build_tcpdump(self):
        self.append_args(["-tcpdump"])

    def avd(self, name: str):
        self.append_args(["-avd", name])

    def noskin(self):
        self.append_args(["-noskin"])

    def noaudio(self):
        self.append_args(["-noaudio"])

    def no_window(self):
        self.append_args(["-no-window"])

    def no_boot_anim(self):
        self.append_args(["-no-boot-anim"])

    def start_avd(self, avd_name: str):
        # self._run(["@" + avd_name])
        # self._run(["-avd", avd_name])
        self.append_args(["-avd", avd_name])
        self.run_daemon()

    def list_avds(self):
        self.append_args(["-list-avds"])
        return self.run()
