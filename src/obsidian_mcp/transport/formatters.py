from typing import Any

from mcp.types import CallToolResult, TextContent

from obsidian_mcp.core.types import ListSortBy, SearchMode, SortOrder


def text_result(markdown: str, structured: dict[str, Any]) -> CallToolResult:
    return CallToolResult(
        content=[TextContent(type="text", text=markdown)],
        structuredContent=structured,
    )


def format_list(
    path: str,
    entries: list[dict[str, Any]],
    sort_by: ListSortBy,
    sort_order: SortOrder,
) -> str:
    label = path or "vault root"
    if not entries:
        return f"No files or directories found in `{_inline_code(label)}`."

    lines = [
        f"Found {len(entries)} entries in `{_inline_code(label)}`.",
        f"Sorted by `{sort_by.value}` {sort_order.value}.",
        "",
        "| Path | Kind | Size | Modified |",
        "| --- | --- | ---: | --- |",
    ]
    for entry in entries:
        lines.append(
            "| "
            f"`{_inline_code(str(entry['path']))}` | "
            f"{entry['kind']} | "
            f"{_format_bytes(entry.get('size'))} | "
            f"{entry.get('modified_at') or ''} |"
        )
    return "\n".join(lines)


def format_read(result: dict[str, Any]) -> str:
    lines = [f"# `{_inline_code(str(result['path']))}`"]
    file_meta = result.get("file") or {}
    metadata = []
    if file_meta.get("modified_at"):
        metadata.append(f"Modified: {file_meta['modified_at']}")
    if file_meta.get("created_at"):
        metadata.append(f"Created: {file_meta['created_at']}")
    if file_meta.get("size") is not None:
        metadata.append(f"Size: {_format_bytes(file_meta['size'])}")
    if result.get("tags"):
        metadata.append("Tags: " + ", ".join(f"`{tag}`" for tag in result["tags"]))
    if result.get("wikilinks"):
        metadata.append(f"Wikilinks: {len(result['wikilinks'])}")
    if result.get("markdown_links"):
        metadata.append(f"Markdown links: {len(result['markdown_links'])}")
    if metadata:
        lines.extend(["", "\n".join(metadata)])
    lines.extend(["", result.get("content") or ""])
    return "\n".join(lines).rstrip()


def format_search(query: str, mode: SearchMode, result: dict[str, Any]) -> str:
    hits = result.get("hits") or []
    warnings = result.get("warnings") or []
    if not hits:
        lines = [f"No matches found for `{_inline_code(query)}` using `{mode.value}` search."]
    else:
        lines = [f"Found {len(hits)} matches for `{_inline_code(query)}` using `{mode.value}` search."]

    for warning in warnings:
        lines.append(f"Warning: {warning}")

    for index, hit in enumerate(hits, start=1):
        title = str(hit.get("title") or hit.get("path") or "Untitled")
        path = str(hit.get("path") or "")
        score = hit.get("score")
        source = hit.get("source")
        score_text = f", score: {score}" if score is not None else ""
        source_text = f", source: {source}" if source else ""
        lines.extend(
            [
                "",
                f"## {index}. {title}",
                f"Path: `{_inline_code(path)}`{score_text}{source_text}",
            ]
        )
        snippet = _blockquote(str(hit.get("snippet") or "").strip())
        if snippet:
            lines.extend(["", snippet])
    return "\n".join(lines)


def format_create_note(result: dict[str, Any]) -> str:
    return f"Wrote note `{_inline_code(str(result['path']))}`."


def format_update_note(result: dict[str, Any]) -> str:
    changed = "updated" if result.get("changed") else "already up to date"
    return f"Note `{_inline_code(str(result['path']))}` is {changed}."


def format_move_path(result: dict[str, Any]) -> str:
    rewritten = result.get("rewritten_files", 0)
    suffix = f" Rewrote wikilinks in {rewritten} files." if rewritten else ""
    return (
        f"Moved `{_inline_code(str(result['source']))}` -> "
        f"`{_inline_code(str(result['destination']))}`.{suffix}"
    )


def format_delete_path(result: dict[str, Any]) -> str:
    path = f"`{_inline_code(str(result['path']))}`"
    if result.get("trashed_to"):
        return f"Moved {path} to trash at `{_inline_code(str(result['trashed_to']))}`."
    if result.get("deleted"):
        return f"Deleted {path} permanently."
    return f"Deleted {path}."


def format_backlinks(result: dict[str, Any]) -> str:
    backlinks = result.get("backlinks") or []
    path = str(result.get("path") or "")
    if not backlinks:
        return f"No backlinks found for `{_inline_code(path)}`."
    lines = [f"Found {len(backlinks)} backlinks to `{_inline_code(path)}`."]
    for backlink in backlinks:
        links = ", ".join(f"`{_inline_code(str(link))}`" for link in backlink.get("links") or [])
        suffix = f" via {links}" if links else ""
        lines.append(f"- `{_inline_code(str(backlink['path']))}`{suffix}")
    return "\n".join(lines)


def format_reindex(result: dict[str, Any]) -> str:
    return (
        "Reindexed vault: "
        f"{result.get('added', 0)} added, "
        f"{result.get('modified', 0)} modified, "
        f"{result.get('removed', 0)} removed, "
        f"{result.get('unchanged', 0)} unchanged, "
        f"{result.get('embedded', 0)} embedded."
    )


def _format_bytes(value: Any) -> str:
    if not isinstance(value, int):
        return ""
    if value < 1024:
        return f"{value} B"
    if value < 1024 * 1024:
        return f"{value / 1024:.1f} KiB"
    return f"{value / (1024 * 1024):.1f} MiB"


def _inline_code(value: str) -> str:
    return value.replace("`", "\\`")


def _blockquote(text: str) -> str:
    if not text:
        return ""
    return "\n".join(f"> {line}" if line else ">" for line in text.splitlines())
