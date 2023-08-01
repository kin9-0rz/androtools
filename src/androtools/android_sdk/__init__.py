import logging
import os
import shutil
import subprocess
from enum import Enum


class SubSubCommand(Enum):
    """命令的子命令的子命令"""

    pass


class CMD:
    def __init__(self, path: str) -> None:
        assert isinstance(path, str)
        self.bin_path = path if os.path.exists(path) else shutil.which(path)

    def _build_cmds(self, cmd: list):
        return [self.bin_path] + cmd

    def _run(self, cmd: list, shell: bool = False):
        """运行阻塞命令"""
        assert isinstance(cmd, list)
        r = subprocess.run(
            self._build_cmds(cmd),
            shell=shell,  # 例如使用通配符、管道或重定向时，须使用shell
            encoding="utf-8",
            capture_output=True,
            text=True,
        )
        return r.stdout, r.stderr

    def _run_async(self, args):
        """运行非堵塞命令"""
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

    def run_subcmd(self, scmd: SubSubCommand, args: list):
        assert isinstance(scmd, SubSubCommand)
        return self._run(scmd.value + args)
