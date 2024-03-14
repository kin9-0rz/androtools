import sys
from logging import DEBUG, INFO, getLevelName

from loguru import logger

DEBUG_MODE = False


if 0 in logger._core.handlers:  # type: ignore
    logger.remove(0)


def log_filter(record):
    return __name__ in record["file"].path


if DEBUG_MODE:
    logger.add(
        f"{__name__}.log",
        filter=log_filter,
        level=getLevelName(DEBUG),
        backtrace=True,
        diagnose=True,
    )
