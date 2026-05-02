from pathlib import Path

from obsidian_vault_mcp.vault.paths import clean_relative_path, is_relative_to

STORAGE_DIR = ".obsidian-vault-mcp"


def is_ignored_relative_path(
    path: Path, *, trash_path: str, is_directory: bool
) -> bool:
    return is_reserved_relative_path(path, trash_path=trash_path) or has_dot_directory(
        path, is_directory
    )


def is_reserved_relative_path(path: Path, *, trash_path: str) -> bool:
    return is_relative_to(path, clean_relative_path(trash_path)) or is_relative_to(
        path, Path(STORAGE_DIR)
    )


def has_dot_directory(path: Path, is_directory: bool) -> bool:
    parts = path.parts if is_directory else path.parts[:-1]
    return any(part.startswith(".") for part in parts)
