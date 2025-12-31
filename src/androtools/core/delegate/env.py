from dataclasses import dataclass
from androtools.core.constants import Android_API_MAP
from androtools.core.delegate import ABCDelegate


@dataclass
class DeviceEnv:
    manufacturer: str
    android_os: str
    android_api: int
    cpu_abilist: str

    def __str__(self) -> str:
        return f"手机品牌：{self.manufacturer}\n操作系统：{self.android_os}\nAPI级别：{self.android_api}\nCPU架构: {self.cpu_abilist}"


class EnvDelegate(ABCDelegate):
    """设备环境信息，如硬件、软件"""

    def getprop(self, prop: str | None = None) -> str:
        """获取模拟器属性"""
        if prop:
            result = self.adb.adb_shell(["getprop", prop])
        else:
            result = self.adb.adb_shell(["getprop"])

        return result.output

    def get_api_level(self):
        sdk = -1
        output = self.getprop("ro.build.version.sdk")
        if output == "":
            return sdk

        if isinstance(output, str):
            sdk = int(output)
        elif isinstance(output, list):
            sdk = int(output[0])

        return sdk

    def get_android_os_name(self):
        sdk = self.get_api_level()
        if result := Android_API_MAP.get(sdk):
            return result[0]
        return f"Android {self.adb.device.info.version}"

    def show(self):
        manufacturer = self.getprop("ro.product.manufacturer")
        if manufacturer == "":
            manufacturer = self.getprop("ro.product.system.manufacturer")
            # ro.product.vendor.manufacturer
            # manufacturer = self.getprop("ro.product.vendor.manufacturer")
        android_os_version = self.getprop("ro.product.build.version.release")
        android_api_level = self.getprop("ro.product.build.version.sdk")
        cpu_abilist = self.getprop("ro.product.cpu.abilist")
        print(cpu_abilist)

        de = DeviceEnv(
            manufacturer, android_os_version, int(android_api_level), cpu_abilist
        )
        print(de)
