import shutil

from androtools import logger
from androtools.cmd import CMD


class FastBoot(CMD):
    def __init__(self, path=shutil.which("fastboot")) -> None:
        super().__init__(path)

    def help(self):
        # NOTE -h 命令不支持 shell
        assert self.bin_path is not None
        result = self._run([self.bin_path, "-h"])
        print(result.output)

    def devices(self, flag=False):
        """List devices in bootloader"""
        _cmd = [self.bin_path, "devices"]
        if flag:
            _cmd.append("-l")
        result = self._run(_cmd)
        logger.debug(result)

    def getvar(self, key="all"):
        """获取设备和分区信息"""
        _cmd = [self.bin_path, "getvar", key]
        result = self._run(_cmd)
        logger.debug(result)

    def reboot(self, bootloader=False):
        _cmd = [self.bin_path, "reboot"]
        if bootloader:
            _cmd.append("bootloader")
        result = self._run(_cmd)
        logger.debug(result)

    def boot(self):
        pass

    # locking/unlocking
    # sub command
    def lock(self):
        _cmd = [
            self.bin_path,
            "flashing",
        ]
        result = self._run(_cmd)
        logger.debug(result)

    def unlock(self):
        pass

    # Flashing ...
    def update(self, zip_path):
        """Flash all partitions from an update.zip package."""
        _cmd = [self.bin_path, "update", zip_path]
        result = self._run(_cmd)
        logger.debug(result)

    def flash(self, partition, filename):
        """
        Flash given partition, using the image from
        $ANDROID_PRODUCT_OUT if no filename is given.
        """
        _cmd = [self.bin_path, "flash", partition, filename]
        result = self._run(_cmd)
        logger.debug(result)

    def flashall(self):
        """
        Flash all partitions from $ANDROID_PRODUCT_OUT.
        On A/B devices, flashed slot is set as active.
        Secondary images may be flashed to inactive slot.
        """
        _cmd = [self.bin_path, "flashall"]
        result = self._run(_cmd)
        logger.debug(result)
