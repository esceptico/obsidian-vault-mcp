from collections.abc import MutableMapping
from io import StringIO
from typing import Any

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap
from ruamel.yaml.error import YAMLError


_TIMESTAMP_TAG = "tag:yaml.org,2002:timestamp"
_OPEN_FENCES = ("---\n", "---\r\n")


def _timestamp_as_string(_constructor: Any, node: Any) -> str:
    """Keep YAML timestamps JSON-serializable for MCP responses."""
    return node.value


_yaml = YAML(typ="rt")
_yaml.default_flow_style = False
_yaml.preserve_quotes = True
_yaml.constructor.add_constructor(_TIMESTAMP_TAG, _timestamp_as_string)


def split_frontmatter(markdown: str) -> tuple[dict[str, Any], str]:
    """Return plain JSON-ready frontmatter plus Markdown body."""
    frontmatter, body = split_frontmatter_raw(markdown)
    return dict(frontmatter), body


def split_frontmatter_raw(markdown: str) -> tuple[MutableMapping[str, Any], str]:
    """Return round-trip frontmatter plus Markdown body."""
    opener = next((fence for fence in _OPEN_FENCES if markdown.startswith(fence)), None)
    if opener is None:
        return CommentedMap(), markdown

    content_start = len(opener)
    close_end = _find_close(markdown, content_start)
    if close_end == -1:
        return CommentedMap(), markdown
    if close_end < len(markdown) and markdown[close_end] not in "\r\n":
        return CommentedMap(), markdown

    raw_yaml = markdown[content_start : close_end - len("---")].rstrip("\r\n")
    body = markdown[close_end:]
    if body.startswith("\r\n"):
        body = body[2:]
    elif body.startswith("\n"):
        body = body[1:]
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
    for key, value in patch.items():
        if value is None:
            current.pop(key, None)
        else:
            current[key] = value
    return render_frontmatter(current, body)


def frontmatter_tags(frontmatter: dict[str, Any]) -> list[str]:
    tags = frontmatter.get("tags", [])
    if isinstance(tags, str):
        return [tags.lstrip("#")]
    if isinstance(tags, list):
        return [str(tag).lstrip("#") for tag in tags]
    return []


def _find_close(markdown: str, start: int) -> int:
    for needle in ("\n---", "\r\n---"):
        idx = markdown.find(needle, start)
        if idx != -1:
            return idx + len(needle)
    return -1


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
