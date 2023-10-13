import sys
from logging import DEBUG, INFO, getLevelName

from loguru import logger

# DEBUG_MODE = True
DEBUG_MODE = False


if 0 in logger._core.handlers:  # type: ignore
    logger.remove(0)

_level = getLevelName(INFO)
_sink = f"{__name__}.log"

if DEBUG_MODE:
    _level = getLevelName(DEBUG)
    _sink = sys.stdout


def log_filter(record):
    return __name__ in record["file"].path


logger.add(
    _sink,
    filter=log_filter,
    level=_level,
    backtrace=True,
    diagnose=True,
)


# logger.debug("Hello World")
