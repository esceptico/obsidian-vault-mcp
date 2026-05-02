from collections.abc import Callable
from functools import partial
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.types import CallToolResult, ToolAnnotations

from obsidian_mcp.core.constants import (
    DEFAULT_LIST_LIMIT,
    DEFAULT_READ_LIMIT,
    DEFAULT_SEARCH_LIMIT,
    MAX_LIST_LIMIT,
    MAX_READ_LIMIT,
    MAX_SEARCH_LIMIT,
)
from obsidian_mcp.core.types import DeleteStrategy, ListSortBy, SearchMode, SortOrder
from obsidian_mcp.transport.formatters import (
    format_backlinks,
    format_create_note,
    format_delete_path,
    format_list,
    format_move_path,
    format_read,
    format_reindex,
    format_search,
    format_update_note,
    text_result,
)
from obsidian_mcp.transport.pagination import page_items, validate_page
from obsidian_mcp.vault.service import Vault

READ_ONLY_TOOL = ToolAnnotations(
    readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False
)
REINDEX_TOOL = ToolAnnotations(
    readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False
)
CREATE_NOTE_TOOL = ToolAnnotations(
    readOnlyHint=False, destructiveHint=True, idempotentHint=False, openWorldHint=False
)
UPDATE_NOTE_TOOL = ToolAnnotations(
    readOnlyHint=False, destructiveHint=True, idempotentHint=True, openWorldHint=False
)
MOVE_PATH_TOOL = ToolAnnotations(
    readOnlyHint=False, destructiveHint=True, idempotentHint=False, openWorldHint=False
)
DELETE_PATH_TOOL = ToolAnnotations(
    readOnlyHint=False, destructiveHint=True, idempotentHint=False, openWorldHint=False
)


ToolFunction = Callable[..., CallToolResult]


def vault_list(
    vault: Vault,
    path: str = "",
    sort_by: ListSortBy = ListSortBy.NAME,
    sort_order: SortOrder = SortOrder.ASC,
    limit: int = DEFAULT_LIST_LIMIT,
    offset: int = 0,
) -> CallToolResult:
    """List files and directories with size/created_at/modified_at metadata.

    Omit path for the vault root. Sort with sort_by=name|modified_at|created_at|size
    and sort_order=asc|desc. Page with limit and offset.
    Returns Markdown text plus structuredContent entries.
    """
    validate_page(limit, offset, MAX_LIST_LIMIT)
    all_entries = vault.list(path, sort_by, sort_order)
    page = page_items(all_entries, limit, offset)
    structured = {
        "path": path,
        "sort_by": ListSortBy(sort_by).value,
        "sort_order": SortOrder(sort_order).value,
        "limit": page.limit,
        "offset": page.offset,
        "total": page.total,
        "has_more": page.has_more,
        "next_offset": page.next_offset,
        "entries": list(page.items),
        "result": list(page.items),
    }
    return text_result(
        format_list(path, page, ListSortBy(sort_by), SortOrder(sort_order)),
        structured,
    )


def vault_read(
    vault: Vault, path: str, limit: int = DEFAULT_READ_LIMIT, offset: int = 0
) -> CallToolResult:
    """Read a Markdown file.

    Page large notes with limit and offset. Offset/limit are character-based.
    Returns Markdown text plus structuredContent metadata.
    """
    validate_page(limit, offset, MAX_READ_LIMIT)
    result = vault.read(path)
    paged = _page_read_result(result, limit, offset)
    return text_result(format_read(paged), paged)


def vault_search(
    vault: Vault,
    query: str,
    limit: int = DEFAULT_SEARCH_LIMIT,
    offset: int = 0,
    mode: SearchMode = SearchMode.HYBRID,
) -> CallToolResult:
    """Search vault notes. Page with limit and offset. Returns Markdown plus structuredContent."""
    validate_page(limit, offset, MAX_SEARCH_LIMIT)
    search_mode = SearchMode(mode)
    requested = min(MAX_SEARCH_LIMIT, offset + limit + 1)
    result = vault.search(query, requested, search_mode)
    page = page_items(result.hits, limit, offset)
    warnings = list(result.warnings)
    structured = {
        "query": query,
        "limit": page.limit,
        "offset": page.offset,
        "returned": page.returned,
        "mode": search_mode.value,
        "hits": list(page.items),
        "warnings": warnings,
        "has_more": page.has_more,
        "next_offset": page.next_offset,
    }
    return text_result(format_search(query, search_mode, page, warnings), structured)


def vault_create_note(
    vault: Vault,
    path: str,
    content: str,
    frontmatter: dict[str, Any] | None = None,
    overwrite: bool = False,
) -> CallToolResult:
    """Create a Markdown note. Returns a Markdown summary plus structuredContent data."""
    result = vault.create_note(path, content, frontmatter, overwrite)
    return text_result(format_create_note(result), result)


def vault_update_note(
    vault: Vault,
    path: str,
    content: str | None = None,
    frontmatter_patch: dict[str, Any] | None = None,
) -> CallToolResult:
    """Replace note body and/or patch frontmatter. Returns text plus structuredContent."""
    result = vault.update_note(path, content, frontmatter_patch)
    return text_result(format_update_note(result), result)


def vault_move_path(
    vault: Vault,
    source: str,
    destination: str,
    rewrite_links: bool = True,
    overwrite: bool = False,
) -> CallToolResult:
    """Move or rename a file/directory. Returns a text summary plus structuredContent."""
    result = vault.move_path(source, destination, rewrite_links, overwrite)
    return text_result(format_move_path(result), result)


def vault_delete_path(
    vault: Vault,
    path: str,
    recursive: bool = False,
    strategy: DeleteStrategy = DeleteStrategy.TRASH,
) -> CallToolResult:
    """Delete a file or directory. Returns a text summary plus structuredContent."""
    result = vault.delete_path(path, recursive, strategy)
    return text_result(format_delete_path(result), result)


def vault_backlinks(vault: Vault, path: str) -> CallToolResult:
    """Find notes that link to a target note. Returns Markdown plus structuredContent."""
    result = vault.backlinks(path)
    return text_result(format_backlinks(result), result)


def vault_reindex(vault: Vault) -> CallToolResult:
    """Re-scan the vault from disk and bring the index up to date.
    Returns a Markdown summary plus structuredContent diff counts."""
    result = {"ok": True, **vault.reindex()}
    return text_result(format_reindex(result), result)


def register_tools(mcp: FastMCP, vault: Vault) -> None:
    _add_tool(mcp, vault, vault_list, "vault_list", READ_ONLY_TOOL)
    _add_tool(mcp, vault, vault_read, "vault_read", READ_ONLY_TOOL)
    _add_tool(mcp, vault, vault_search, "vault_search", READ_ONLY_TOOL)
    _add_tool(mcp, vault, vault_create_note, "vault_create_note", CREATE_NOTE_TOOL)
    _add_tool(mcp, vault, vault_update_note, "vault_update_note", UPDATE_NOTE_TOOL)
    _add_tool(mcp, vault, vault_move_path, "vault_move_path", MOVE_PATH_TOOL)
    _add_tool(mcp, vault, vault_delete_path, "vault_delete_path", DELETE_PATH_TOOL)
    _add_tool(mcp, vault, vault_backlinks, "vault_backlinks", READ_ONLY_TOOL)
    _add_tool(mcp, vault, vault_reindex, "vault_reindex", REINDEX_TOOL)


def _add_tool(
    mcp: FastMCP,
    vault: Vault,
    fn: ToolFunction,
    name: str,
    annotations: ToolAnnotations,
) -> None:
    bound = partial(fn, vault)
    bound.__name__ = name
    bound.__doc__ = fn.__doc__
    mcp.add_tool(
        bound,
        name=name,
        description=fn.__doc__,
        annotations=annotations,
        structured_output=False,
    )


def _page_read_result(
    result: dict[str, Any], limit: int, offset: int
) -> dict[str, Any]:
    content = str(result.get("content") or "")
    total = len(content)
    page_content = content[offset : offset + limit]
    next_offset = (
        offset + len(page_content) if offset + len(page_content) < total else None
    )
    paged = {
        key: value for key, value in result.items() if key not in {"body", "content"}
    }
    paged["content"] = page_content
    paged["page"] = {
        "limit": limit,
        "offset": offset,
        "returned": len(page_content),
        "total": total,
        "has_more": next_offset is not None,
        "next_offset": next_offset,
    }
    return paged
