"""测试专用的 DeviceInfo 构造器。

四个测试文件都要造身份信息，集中在这里，改字段时只改一处。
"""

from androtools.core.device import DeviceInfo, EmulatorInfo


def make_device_info(**overrides) -> DeviceInfo:
    fields = dict(
        name="test-device",
        serial="127.0.0.1:5555",
        adb_path="adb",
    )
    fields.update(overrides)
    return DeviceInfo(**fields)


def make_emulator_info(**overrides) -> EmulatorInfo:
    fields = dict(
        name="test-device",
        serial="127.0.0.1:5555",
        adb_path="adb",
        index="0",
        console_path="ldconsole",
    )
    fields.update(overrides)
    return EmulatorInfo(**fields)