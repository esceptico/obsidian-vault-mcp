from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from obsidian_mcp.core.types import EntryKind, ListSortBy, SortOrder


def entry_for(path: Path, relative_path: str) -> dict[str, Any]:
    kind = EntryKind.DIRECTORY if path.is_dir() else EntryKind.FILE
    return {
        "path": relative_path,
        "kind": kind.value,
        **file_metadata(path),
    }


def file_metadata(path: Path) -> dict[str, Any]:
    stat = path.stat()
    created_at = getattr(stat, "st_birthtime", None)
    return {
        "size": stat.st_size,
        "created_at": _timestamp(created_at) if created_at is not None else None,
        "modified_at": _timestamp(stat.st_mtime),
    }


def sort_entries(
    entries: list[dict[str, Any]],
    sort_by: ListSortBy,
    sort_order: SortOrder,
) -> list[dict[str, Any]]:
    if sort_by == ListSortBy.NAME:
        reverse = sort_order == SortOrder.DESC
        directories = [entry for entry in entries if entry["kind"] == EntryKind.DIRECTORY.value]
        files = [entry for entry in entries if entry["kind"] == EntryKind.FILE.value]
        return sorted(directories, key=_path_key, reverse=reverse) + sorted(files, key=_path_key, reverse=reverse)

    reverse = sort_order == SortOrder.DESC
    with_value = [entry for entry in entries if entry[sort_by.value] is not None]
    without_value = [entry for entry in entries if entry[sort_by.value] is None]
    return sorted(with_value, key=lambda entry: (entry[sort_by.value], _path_key(entry)), reverse=reverse) + sorted(
        without_value,
        key=_path_key,
    )


def _timestamp(value: float) -> str:
    return datetime.fromtimestamp(value, timezone.utc).isoformat()


def _path_key(entry: dict[str, Any]) -> str:
    return str(entry["path"]).lower()
