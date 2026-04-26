import os
import secrets
from pathlib import Path


def clean_relative_path(path: str) -> Path:
    if not path or path == ".":
        return Path()
    candidate = Path(path)
    if candidate.is_absolute():
        raise ValueError("Vault paths must be relative")
    if any(part in {"..", ""} for part in candidate.parts):
        raise ValueError("Vault path contains unsafe segments")
    return candidate


def ensure_markdown_extension(path: str) -> str:
    return path if Path(path).suffix else f"{path}.md"


def is_relative_to(path: Path, parent: Path) -> bool:
    if parent == Path():
        return False
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def temporary_write_path(path: Path) -> Path:
    return path.with_name(f".{path.name}.{os.getpid()}.{secrets.token_hex(4)}.tmp")
