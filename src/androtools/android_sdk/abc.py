import os
import shutil
import subprocess
from enum import Enum

from loguru import logger


class SubSubCommand(Enum):
    """命令的子命令的子命令"""

    pass


class CMD:
    def __init__(self, path: str) -> None:
        assert isinstance(path, str)
        self.bin_path = path if os.path.exists(path) else shutil.which(path)
        self._args: list[str] = []

    def reset(self):
        self._args.clear()

    def _build_cmds(self, cmd: list[str]) -> list:
        assert isinstance(cmd, list)
        return [self.bin_path] + cmd

    def build(self, arg: str):
        self._args.append(arg)
        return self

    def build_args(self, args: list[str]):
        self._args += args
        return self

    def run(self, is_reset: bool = True):
        result = self._run(self._args)
        if is_reset:
            self.reset()
        return result

    def run_daemon(self, is_reset: bool = True):
        self._run_daemon(self._args)
        if is_reset:
            self.reset()

    def _run(self, cmd: list[str], shell: bool = False, encoding: str | None = None):
        """运行阻塞命令，等待结果。"""
        assert isinstance(cmd, list)
        for item in cmd:
            assert isinstance(item, str)
        args = self._build_cmds(cmd)
        logger.debug("CMD [_run] " + " ".join(args))

        r = subprocess.run(
            args,
            shell=shell,  # 例如使用通配符、管道或重定向时，须使用shell
            encoding=encoding,
            errors="ignore",
            capture_output=True,
            text=True,
        )

        return r.stdout, r.stderr

    def _run_daemon(self, args: list[str]):
        """运行后台命令，直接运行命令，不需要获取结果。"""
        cmd_list = self._build_cmds(args)
        logger.debug("CMD [_run_deamon] " + " ".join(cmd_list))
        try:
            proc = subprocess.Popen(
                cmd_list,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                shell=False,
            )

            try:
                proc.wait(10)
            except Exception:
                pass

        except Exception as err:
            logger.error(cmd_list)
            logger.error(err)
            raise err

    # TODO 这种方式感觉不大好，调用过于繁琐，最好能够直接使用。
    def run_subcmd(self, scmd: SubSubCommand, args: list):
        assert isinstance(scmd, SubSubCommand)
        return self._run(scmd.value + args)
