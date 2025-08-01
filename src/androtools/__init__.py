import sys
from logging import DEBUG, getLevelName
from typing import TextIO

from loguru import logger as _logger

logger = _logger


# NOTE: 删除默认日志器，必须的
if 0 in logger._core.handlers:  # type: ignore
    logger.remove(0)


def turn_on_logger(level: int = DEBUG, to_file: bool = False):
    """打开日志

    level 日志级别, logging.DEBUG, logging.INFO 等等。
    默认在屏幕输出，如果日志过多，可以考虑输出到文件
    """

    def log_filter(record):
        return __name__ in record["file"].path

    _level: str = getLevelName(level)
    _sink: str | TextIO = f"{__name__}.log" if to_file else sys.stdout
    logger.add(
        _sink,
        filter=log_filter,
        level=_level,
        backtrace=True,
        diagnose=True,
    )
