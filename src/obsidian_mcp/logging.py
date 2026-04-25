import logging

ROOT = "obsidian_mcp"


def get_logger(name: str) -> logging.Logger:
    logging.getLogger(ROOT)  # materialize parent so child .parent resolves correctly
    return logging.getLogger(f"{ROOT}.{name}")


def configure_default_logging(level: int = logging.INFO) -> None:
    root = logging.getLogger(ROOT)
    if root.handlers:
        return
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root.addHandler(handler)
    root.setLevel(level)
    root.propagate = False
