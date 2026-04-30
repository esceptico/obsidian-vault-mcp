from pathlib import Path
from typing import Any

from obsidian_mcp.core.constants import MAX_FRONTMATTER_DEPTH, MAX_NOTE_BYTES
from obsidian_mcp.markdown.frontmatter import (
    frontmatter_tags,
    patch_frontmatter,
    render_frontmatter,
    split_frontmatter,
    split_frontmatter_raw,
)
from obsidian_mcp.markdown.obsidian import block_ids, inline_tags, markdown_links, wikilinks
from obsidian_mcp.vault.listing import file_metadata


def read_note(root: Path, file_path: Path) -> dict[str, Any]:
    content = file_path.read_text(encoding="utf-8")
    frontmatter, body = split_frontmatter(content)
    return {
        "path": file_path.resolve().relative_to(root).as_posix(),
        "frontmatter": frontmatter,
        "body": body,
        "content": content,
        "file": file_metadata(file_path),
        "wikilinks": [link.__dict__ for link in wikilinks(body)],
        "markdown_links": markdown_links(body),
        "tags": sorted(set(frontmatter_tags(frontmatter) + inline_tags(body))),
        "block_ids": block_ids(body),
    }


def render_new_note(content: str, frontmatter: dict[str, Any] | None) -> str:
    check_note_size(content)
    check_frontmatter_depth(frontmatter or {})
    rendered = render_frontmatter(frontmatter or {}, content)
    check_note_size(rendered)
    return rendered


def render_updated_note(
    existing: str,
    content: str | None,
    frontmatter_patch: dict[str, Any] | None,
) -> tuple[str, str]:
    if content is not None:
        check_note_size(content)
    if frontmatter_patch:
        check_frontmatter_depth(frontmatter_patch)

    _, previous_body = split_frontmatter(existing)
    next_content = existing
    if content is not None:
        current_frontmatter, _ = split_frontmatter_raw(existing)
        next_content = render_frontmatter(current_frontmatter, content)
    if frontmatter_patch:
        next_content = patch_frontmatter(next_content, frontmatter_patch)
    check_note_size(next_content)
    return next_content, previous_body


def check_note_size(content: str) -> None:
    if len(content.encode("utf-8")) > MAX_NOTE_BYTES:
        raise ValueError(f"Note content exceeds {MAX_NOTE_BYTES} bytes")


def check_frontmatter_depth(value: Any, depth: int = 0) -> None:
    if depth > MAX_FRONTMATTER_DEPTH:
        raise ValueError(f"Frontmatter exceeds max depth ({MAX_FRONTMATTER_DEPTH})")
    if isinstance(value, dict):
        for child in value.values():
            check_frontmatter_depth(child, depth + 1)
    elif isinstance(value, (list, tuple)):
        for child in value:
            check_frontmatter_depth(child, depth + 1)
