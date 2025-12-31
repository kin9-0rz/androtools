from androtools.core.device import DeviceADB


class ABCDelegate:
    def __init__(self, adb: DeviceADB) -> None:
        self.adb = adb
