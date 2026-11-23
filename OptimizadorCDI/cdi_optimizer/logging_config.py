import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path


def configure_logging(log_directory):
    logger = logging.getLogger("cdi_optimizer")
    logger.setLevel(logging.ERROR)
    logger.propagate = False

    directory = Path(log_directory)
    log_path = directory / "optimizador.log"
    for handler in logger.handlers:
        if isinstance(handler, RotatingFileHandler) and Path(handler.baseFilename) == log_path:
            return logger

    directory.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(
        log_path,
        maxBytes=2 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    handler.setFormatter(
        logging.Formatter(
            "%(asctime)s %(levelname)s %(process)d %(threadName)s "
            "%(name)s %(message)s"
        )
    )
    logger.addHandler(handler)
    return logger


def configure_logging_safely(log_directory):
    logger = logging.getLogger("cdi_optimizer")
    try:
        return configure_logging(log_directory)
    except OSError:
        logger.addHandler(logging.NullHandler())
        return logger
