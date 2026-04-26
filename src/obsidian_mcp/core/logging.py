import logging

ROOT = "obsidian_mcp"


def _root_logger() -> logging.Logger:
    """Library default: materialize the package logger at WARNING so importers
    (notebooks, tests, embedded callers) don't get INFO chatter unless they
    opt in via `configure_default_logging` or their own logging config."""
    logger = logging.getLogger(ROOT)
    if logger.level == logging.NOTSET:
        logger.setLevel(logging.WARNING)
    return logger


def get_logger(name: str) -> logging.Logger:
    _root_logger()  # materialize parent so child .parent resolves correctly
    return logging.getLogger(f"{ROOT}.{name}")


def configure_default_logging(level: int = logging.INFO) -> None:
    root = _root_logger()
    if root.handlers:
        root.setLevel(level)
        return
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root.addHandler(handler)
    root.setLevel(level)
    root.propagate = False
