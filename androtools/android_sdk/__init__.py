import shutil
import subprocess

from androtools.android_sdk.build_tools import AAPT, AAPT2, ApkSigner, DexDump

__all__ = [
    "ADB",
    "DeviceType",
    "AAPT",
    "AAPT2",
    "ApkSigner",
    "DexDump",
]


class CMD:
    def __init__(self, path) -> None:
        self.bin_path = shutil.which(path)

    def _build_cmds(self, cmd: str | list, is_shell: bool = False):
        if isinstance(cmd, str):
            cmd = [cmd]
        if isinstance(cmd, list):
            return [self.bin_path] + cmd
        else:
            raise ValueError(f"unknown cmd: {cmd}")

    def _run(self, args):
        """运行阻塞命令"""
        r = subprocess.run(
            args,
            shell=True,  # 例如使用通配符、管道或重定向时，必须使用shell
            encoding="utf-8",
            capture_output=True,
            text=True,
        )
        return r.stdout, r.stderr

    def _run_async(self, args):
        """运行非堵塞命令"""
        # TODO 运行后台命令，后面把它杀掉？
        # (self, cmd: str | list, is_shell: bool = False):
        cmd_list = self._build_cmds(args)
        try:
            adb_proc = subprocess.Popen(
                cmd_list,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                shell=False,
            )
            output, error = adb_proc.communicate()
            output = output.decode("utf-8")
            error = error.decode("utf-8")

            if adb_proc.returncode == 1:
                logging.error(cmd_list)
                return output, error

            if len(output) == 0:
                output = None
            else:
                output = [x.strip() for x in output.split("\n") if len(x.strip()) > 0]

        except Exception as err:
            logging.error(cmd_list)
            logging.error(err)
            raise err

        return output, error
