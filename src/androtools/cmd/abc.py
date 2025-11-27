import os
import shutil
import subprocess
from enum import Enum

from func_timeout import FunctionTimedOut, func_timeout

from androtools import logger
from androtools.cmd.result import CmdResult


class SubSubCommand(Enum):
    """命令的子命令的子命令"""

    pass


class CMD:
    """命令定义"""

    def __init__(self, path: str) -> None:
        assert isinstance(path, str)
        self.bin_path = path if os.path.exists(path) else shutil.which(path)
        self._args: list[str] = []
        self._current_cmd_line = ""

    def reset(self):
        """参数重置"""
        self._args.clear()

    def _build_cmd_line(self, cmd: list[str]) -> list:
        """将命令和参数组合成一个命令行"""
        assert isinstance(cmd, list)
        return [self.bin_path] + cmd

    def append_args(self, args: list[str]):
        """添加参数，可以一个一个添加，也可以一次性添加。
        -a
        -b arg1
        -c arg2 arg3
        -a -b arg1 -c arg2 arg3
        """
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

    def _run(
        self,
        cmd: list[str],
        shell: bool = False,
        encoding: str | None = None,
        timeout: int | None = None,
    ) -> CmdResult:
        """运行阻塞命令，等待结果。"""
        assert isinstance(cmd, list)
        for item in cmd:
            assert isinstance(item, str)
        cmd_line = self._build_cmd_line(cmd)
        logger.debug("[CMD]" + " ".join(cmd_line))

        try:
            r = subprocess.run(
                cmd_line,
                shell=shell,  # 例如使用通配符、管道或重定向时，须使用shell
                encoding=encoding,
                errors="ignore",
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            cr = CmdResult(r.stdout, r.stderr)
            logger.debug(f"Result:\n{cr}")
            return CmdResult(r.stdout, r.stderr)
        except Exception as e:
            raise e

    def _run_daemon(self, args: list[str]):
        """运行后台命令，直接运行命令，不需要获取结果。"""
        cmd_list = self._build_cmd_line(args)
        logger.debug(" ".join(cmd_list))
        try:
            proc = subprocess.Popen(
                cmd_list,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

            out = ""
            try:
                stdout = proc.stdout
                if stdout:
                    bs = func_timeout(3, stdout.read)
                    if bs:
                        out = bs.decode("utf-8")

            except FunctionTimedOut:
                pass

            err = ""
            try:
                stderr = proc.stderr
                if stderr:
                    bs = func_timeout(3, stderr.read)
                    if bs:
                        err = bs.decode("utf-8")

            except FunctionTimedOut:
                pass

            if out:
                logger.debug(out)
            if err:
                logger.error(" ".join(cmd_list))
                logger.error(err)

        except Exception as err:
            logger.error(cmd_list)
            logger.error(err)
            raise err

    # TODO 这种方式感觉不大好，调用过于繁琐，最好能够直接使用。
    def run_subcmd(self, scmd: SubSubCommand, args: list):
        assert isinstance(scmd, SubSubCommand)
        return self._run(scmd.value + args)
