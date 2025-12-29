import sys

from loguru import logger as _logger

logger = _logger


# NOTE: 删除默认日志器，必须的
if 0 in logger._core.handlers:  # type: ignore
    logger.remove(0)


def log_filter(record):
    return "androtools" in record["name"]


def setup_logging(sink: str = "androtools.log", level: str = "INFO"):
    """打开日志

    level 日志级别, logging.DEBUG, logging.INFO 等等。
    默认在屏幕输出，如果日志过多，可以考虑输出到文件
    """
    logger.add(
        sink=sink,
        filter=log_filter,
        level=level,
        backtrace=True,
        diagnose=True,
        rotation="10MB",
        retention="3 days",
        compression="zip",
        encoding="utf-8",
        enqueue=True,
    )


def enable_console_logging():
    logger.add(
        sys.stdout,
        filter=log_filter,
        level="DEBUG",
        backtrace=True,
        diagnose=True,
        enqueue=True,
    )
