from datetime import datetime, timezone
from typing import Any

from mcp.types import CallToolResult, TextContent

from headless_obsidian_mcp.core.types import ListSortBy, SearchMode, SortOrder
from headless_obsidian_mcp.transport.pagination import Page

BYTES_PER_KIB = 1024
BYTES_PER_MIB = BYTES_PER_KIB * BYTES_PER_KIB
RELATIVE_TIMESTAMP_MAX_DAYS = 60
SECONDS_PER_MINUTE = 60
MINUTES_PER_HOUR = 60
HOURS_PER_DAY = 24
RELATIVE_TIME_UNITS = (
    ("day", SECONDS_PER_MINUTE * MINUTES_PER_HOUR * HOURS_PER_DAY),
    ("hour", SECONDS_PER_MINUTE * MINUTES_PER_HOUR),
    ("minute", SECONDS_PER_MINUTE),
)


def text_result(markdown: str, structured: dict[str, Any]) -> CallToolResult:
    return CallToolResult(
        content=[TextContent(type="text", text=markdown)],
        structuredContent=structured,
    )


def format_list(
    path: str,
    page: Page[dict[str, Any]],
    sort_by: ListSortBy,
    sort_order: SortOrder,
) -> str:
    label = path or "vault root"
    entries = page.items
    total = page.total if page.total is not None else page.returned
    if total == 0:
        return f"No files or directories found in {_code_span(label)}."
    if not entries:
        return (
            f"No entries on this page for {_code_span(label)}. "
            f"Total entries: {total}. Use a smaller offset."
        )

    end = page.offset + page.returned
    lines = [
        f"Showing entries {page.offset + 1}-{end} of {total} in {_code_span(label)}.",
        f"Sorted by `{sort_by.value}` {sort_order.value}.",
    ]
    if page.has_more:
        lines.append(
            f"More entries available. Use `offset={end}` with `limit={page.limit}`."
        )
    lines.extend(
        [
            "",
            "| Path | Kind | Size | Modified |",
            "| --- | --- | ---: | --- |",
        ]
    )
    for entry in entries:
        lines.append(
            "| "
            f"{_table_code_span(str(entry['path']))} | "
            f"{_table_cell(str(entry['kind']))} | "
            f"{_format_bytes(entry.get('size'))} | "
            f"{_format_timestamp(entry.get('modified_at'))} |"
        )
    return "\n".join(lines)


def format_read(result: dict[str, Any]) -> str:
    lines = [f"# {_code_span(str(result['path']))}"]
    metadata = _read_metadata_lines(result)
    if metadata:
        lines.extend(["", "\n".join(metadata)])
    page = result.get("page") or {}
    if page.get("total") is not None:
        lines.extend(["", _read_page_line(page)])
    lines.extend(["", result.get("content") or ""])
    return "\n".join(lines).rstrip()


def format_search(
    query: str,
    mode: SearchMode,
    page: Page[dict[str, Any]],
    warnings: list[str],
) -> str:
    hits = page.items
    if not hits:
        lines = [
            f"No matches found for {_code_span(query)} using `{mode.value}` search."
        ]
    else:
        end = page.offset + page.returned
        lines = [
            f"Showing matches {page.offset + 1}-{end} for {_code_span(query)} using `{mode.value}` search."
        ]
        if page.has_more:
            lines.append(
                f"More matches may be available. Use `offset={end}` with `limit={page.limit}`."
            )

    for warning in warnings:
        lines.append(f"Warning: {warning}")

    for index, hit in enumerate(hits, start=1):
        title = str(hit.get("title") or hit.get("path") or "Untitled")
        path = str(hit.get("path") or "")
        score = hit.get("score")
        source = hit.get("source")
        heading = str(hit.get("heading") or "")
        chunk_index = hit.get("chunk_index")
        start_char = hit.get("start_char")
        end_char = hit.get("end_char")
        score_text = f", score: {score}" if score is not None else ""
        source_text = f", source: {source}" if source else ""
        chunk_text = f", chunk: {chunk_index}" if chunk_index is not None else ""
        range_text = (
            f", chars: {start_char}-{end_char}"
            if start_char is not None and end_char is not None
            else ""
        )
        lines.extend(
            [
                "",
                f"## {index}. {title}",
                f"Path: {_code_span(path)}{score_text}{source_text}{chunk_text}{range_text}",
            ]
        )
        if heading:
            lines.append(f"Heading: {_code_span(heading)}")
        snippet = _blockquote(str(hit.get("snippet") or "").strip())
        if snippet:
            lines.extend(["", snippet])
    return "\n".join(lines)


def format_create_note(result: dict[str, Any]) -> str:
    return f"Wrote note {_code_span(str(result['path']))}."


def format_update_note(result: dict[str, Any]) -> str:
    changed = "updated" if result.get("changed") else "already up to date"
    return f"Note {_code_span(str(result['path']))} is {changed}."


def format_move_path(result: dict[str, Any]) -> str:
    rewritten = result.get("rewritten_files", 0)
    suffix = f" Rewrote wikilinks in {_plural(rewritten, 'file')}." if rewritten else ""
    return (
        f"Moved {_code_span(str(result['source']))} -> "
        f"{_code_span(str(result['destination']))}.{suffix}"
    )


def format_delete_path(result: dict[str, Any]) -> str:
    path = _code_span(str(result["path"]))
    if result.get("trashed_to"):
        return f"Moved {path} to trash at {_code_span(str(result['trashed_to']))}."
    if result.get("deleted"):
        return f"Deleted {path} permanently."
    return f"Deleted {path}."


def format_backlinks(result: dict[str, Any]) -> str:
    backlinks = result.get("backlinks") or []
    path = str(result.get("path") or "")
    if not backlinks:
        return f"No backlinks found for {_code_span(path)}."
    lines = [f"Found {_plural(len(backlinks), 'backlink')} to {_code_span(path)}."]
    for backlink in backlinks:
        links = ", ".join(_code_span(str(link)) for link in backlink.get("links") or [])
        suffix = f" via {links}" if links else ""
        lines.append(f"- {_code_span(str(backlink['path']))}{suffix}")
    return "\n".join(lines)


def format_reindex(result: dict[str, Any]) -> str:
    counts = [
        ("added", "added"),
        ("modified", "modified"),
        ("removed", "removed"),
        ("unchanged", "unchanged"),
        ("embedded", "embedded"),
    ]
    summary = ", ".join(f"{int(result.get(key, 0))} {label}" for key, label in counts)
    return f"Reindexed vault: {summary}."


def _read_metadata_lines(result: dict[str, Any]) -> list[str]:
    file_meta = result.get("file") or {}
    metadata = []
    if file_meta.get("modified_at"):
        metadata.append(f"Modified: {_format_timestamp(file_meta['modified_at'])}")
    if file_meta.get("created_at"):
        metadata.append(f"Created: {_format_timestamp(file_meta['created_at'])}")
    if file_meta.get("size") is not None:
        metadata.append(f"Size: {_format_bytes(file_meta['size'])}")
    tags = result.get("tags") or []
    if tags:
        metadata.append("Tags: " + ", ".join(_code_span(str(tag)) for tag in tags))
    for field, label in (
        ("wikilinks", "Wikilinks"),
        ("markdown_links", "Markdown links"),
    ):
        count = len(result.get(field) or [])
        if count:
            metadata.append(f"{label}: {count}")
    return metadata


def _read_page_line(page: dict[str, Any]) -> str:
    offset = int(page.get("offset") or 0)
    returned = int(page.get("returned") or 0)
    total = int(page.get("total") or 0)
    if total == 0:
        return "Showing 0 characters."
    end = offset + returned
    line = f"Showing characters {offset + 1}-{end} of {total}."
    if page.get("has_more"):
        line += f" More content available. Use `offset={end}` with `limit={page.get('limit')}`."
    return line


def _format_bytes(value: Any) -> str:
    if not isinstance(value, int):
        return ""
    if value < BYTES_PER_KIB:
        return f"{value} B"
    if value < BYTES_PER_MIB:
        return f"{value / BYTES_PER_KIB:.1f} KiB"
    return f"{value / BYTES_PER_MIB:.1f} MiB"


def _format_timestamp(value: Any) -> str:
    if not isinstance(value, str) or not value:
        return ""
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return value
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    parsed = parsed.astimezone(timezone.utc)
    compact = parsed.strftime("%Y-%m-%d %H:%M UTC")
    relative = _relative_timestamp(parsed)
    if relative:
        return f"{relative} ({compact})"
    return compact


def _relative_timestamp(value: datetime) -> str:
    delta = datetime.now(timezone.utc) - value
    seconds = int(delta.total_seconds())
    if seconds < 0:
        return ""
    if seconds < 60:
        return "just now"
    if (
        seconds
        >= RELATIVE_TIMESTAMP_MAX_DAYS
        * HOURS_PER_DAY
        * MINUTES_PER_HOUR
        * SECONDS_PER_MINUTE
    ):
        return ""
    for unit, unit_seconds in RELATIVE_TIME_UNITS:
        count = seconds // unit_seconds
        if count:
            return f"{_plural(count, unit)} ago"
    return ""


def _plural(value: int, unit: str) -> str:
    suffix = "" if value == 1 else "s"
    return f"{value} {unit}{suffix}"


def _code_span(value: str) -> str:
    fence = _code_fence(value)
    padding = " " if value.startswith("`") or value.endswith("`") else ""
    return f"{fence}{padding}{value}{padding}{fence}"


def _table_code_span(value: str) -> str:
    return _code_span(value.replace("|", "\\|"))


def _table_cell(value: str) -> str:
    return value.replace("|", "\\|")


def _code_fence(value: str) -> str:
    longest = 0
    current = 0
    for char in value:
        if char == "`":
            current += 1
            longest = max(longest, current)
        else:
            current = 0
    return "`" * (longest + 1)


def _blockquote(text: str) -> str:
    if not text:
        return ""
    return "\n".join(f"> {line}" if line else ">" for line in text.splitlines())
