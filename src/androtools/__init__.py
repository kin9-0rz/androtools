import sys
from logging import DEBUG, getLevelName
from typing import TextIO

from loguru import logger as _logger

my_logger = _logger.bind(logger_name="androtools")

if 0 in my_logger._core.handlers:  # type: ignore
    my_logger.remove(0)


def log_filter(record):
    return __name__ in record["file"].path


def turn_on_logger(level: int = DEBUG, to_file: bool = False):
    """打开日志

    level 日志级别, logging.DEBUG, logging.INFO 等等。
    默认在屏幕输出，如果日志过多，可以考虑输出到文件
    """
    _level: str = getLevelName(level)
    _sink: str | TextIO = f"{__name__}.log" if to_file else sys.stdout
    my_logger.add(
        _sink,
        filter=log_filter,
        level=_level,
        backtrace=True,
        diagnose=True,
    )
