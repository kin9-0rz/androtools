from androtools.device.abc import DeviceADB


class ABCDelegate:
    """
    Android 设备操作委托模块

    如：
    - APP 操作
    - Shell 命令执行
    - 环境数据获取
    - 系统代理设置
    - tcpdump 抓包
    - 模拟点击 等等

    提前将这些操作封装好，方便调用。
    操作设备的唯一方式，就是 adb，没有其他方式。
    """

    def __init__(self, adb: DeviceADB) -> None:
        self.adb = adb
