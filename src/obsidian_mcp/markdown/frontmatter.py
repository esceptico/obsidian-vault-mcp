from collections.abc import MutableMapping
from io import StringIO
from typing import Any

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap
from ruamel.yaml.error import YAMLError


_TIMESTAMP_TAG = "tag:yaml.org,2002:timestamp"
_OPEN_FENCES = ("---\n", "---\r\n")
_CLOSING_FENCE = "---"


def _timestamp_as_string(_constructor: Any, node: Any) -> str:
    """Keep YAML timestamps JSON-serializable for MCP responses."""
    return node.value


def _make_yaml() -> YAML:
    yaml = YAML(typ="rt")
    yaml.default_flow_style = False
    yaml.preserve_quotes = True
    yaml.constructor.add_constructor(_TIMESTAMP_TAG, _timestamp_as_string)
    return yaml


_yaml = _make_yaml()


def split_frontmatter(markdown: str) -> tuple[dict[str, Any], str]:
    """Return plain JSON-ready frontmatter plus Markdown body."""
    frontmatter, body = split_frontmatter_raw(markdown)
    return _to_plain_data(frontmatter), body


def split_frontmatter_raw(markdown: str) -> tuple[MutableMapping[str, Any], str]:
    """Return round-trip frontmatter plus Markdown body."""
    block = _frontmatter_block(markdown)
    if block is None:
        return CommentedMap(), markdown

    raw_yaml, body = block
    if not raw_yaml.strip():
        return CommentedMap(), body
    return _parse_yaml(raw_yaml), body


def render_frontmatter(frontmatter: MutableMapping[str, Any] | dict[str, Any], body: str) -> str:
    normalized_body = body.lstrip("\r\n")
    if not frontmatter:
        return normalized_body
    stream = StringIO()
    _yaml.dump(frontmatter, stream)
    return f"---\n{stream.getvalue()}---\n{normalized_body}"


def patch_frontmatter(markdown: str, patch: dict[str, Any]) -> str:
    current, body = split_frontmatter_raw(markdown)
    _apply_frontmatter_updates(current, patch)
    return render_frontmatter(current, body)


def _apply_frontmatter_updates(frontmatter: MutableMapping[str, Any], updates: dict[str, Any]) -> None:
    for key, value in updates.items():
        if value is None:
            frontmatter.pop(key, None)
        else:
            frontmatter[key] = value


def frontmatter_tags(frontmatter: dict[str, Any]) -> list[str]:
    tags = frontmatter.get("tags", [])
    if isinstance(tags, str):
        return _normalize_tags(tags.replace(",", " ").split())
    if isinstance(tags, (list, tuple)):
        return _normalize_tags(tags)
    return []


def _normalize_tags(values: Any) -> list[str]:
    normalized = []
    for value in values:
        if value is None:
            continue
        tag = str(value).strip().lstrip("#")
        if tag:
            normalized.append(tag)
    return normalized


def _frontmatter_block(markdown: str) -> tuple[str, str] | None:
    opener = next((fence for fence in _OPEN_FENCES if markdown.startswith(fence)), None)
    if opener is None:
        return None

    position = len(opener)
    for line in markdown[position:].splitlines(keepends=True):
        line_start = position
        position += len(line)
        if _is_closing_fence(line):
            raw_yaml = markdown[len(opener) : line_start].rstrip("\r\n")
            return raw_yaml, markdown[position:]
    return None


def _is_closing_fence(line: str) -> bool:
    return line.rstrip("\r\n") == _CLOSING_FENCE


def _parse_yaml(raw: str) -> MutableMapping[str, Any]:
    try:
        parsed = _yaml.load(raw)
    except YAMLError as exc:
        raise ValueError(f"Invalid YAML frontmatter: {exc}") from exc
    if parsed is None:
        return CommentedMap()
    if not isinstance(parsed, MutableMapping):
        raise ValueError("YAML frontmatter must be a mapping")
    return parsed


def _to_plain_data(value: Any) -> Any:
    if isinstance(value, MutableMapping):
        return {str(key): _to_plain_data(child) for key, child in value.items()}
    if isinstance(value, list):
        return [_to_plain_data(child) for child in value]
    return value
