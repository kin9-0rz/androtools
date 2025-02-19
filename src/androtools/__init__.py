import sys
from logging import DEBUG, INFO, getLevelName
from typing import TextIO

from loguru import logger

DEBUG_MODE = False

_level: str = getLevelName(DEBUG) if DEBUG_MODE else getLevelName(INFO)
_sink: str | TextIO = sys.stdout if DEBUG_MODE else f"{__name__}.log"

if 0 in logger._core.handlers:  # type: ignore
    logger.remove(0)


def log_filter(record):
    return __name__ in record["file"].path


if DEBUG_MODE:
    logger.add(
        _sink,
        filter=log_filter,
        level=_level,
        backtrace=True,
        diagnose=True,
    )
