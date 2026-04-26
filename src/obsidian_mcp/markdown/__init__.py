import re
from collections.abc import Callable, MutableMapping
from dataclasses import dataclass
from io import StringIO
from typing import Any

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap
from ruamel.yaml.error import YAMLError


_TIMESTAMP_TAG = "tag:yaml.org,2002:timestamp"


def _timestamp_as_string(_constructor: Any, node: Any) -> str:
    """Keep YAML timestamps JSON-serializable for MCP responses."""
    return node.value


_yaml = YAML(typ="rt")
_yaml.default_flow_style = False
_yaml.preserve_quotes = True
_yaml.constructor.add_constructor(_TIMESTAMP_TAG, _timestamp_as_string)

_OPEN_FENCES = ("---\n", "---\r\n")

WIKILINK_RE = re.compile(r"(?P<embed>!)?\[\[(?P<inner>[^\]\n]+)\]\]")
MARKDOWN_LINK_RE = re.compile(r"(?P<embed>!)?\[[^\]\n]*\]\((?P<target>[^)\n]+)\)")
TAG_RE = re.compile(r"(?<![\w/])#([A-Za-z0-9][A-Za-z0-9_/-]*)")
BLOCK_ID_RE = re.compile(r"(?m)(?:^|\s)\^([A-Za-z0-9-]+)\s*$")


@dataclass(frozen=True)
class WikiLink:
    raw: str
    target: str
    alias: str | None
    heading: str | None
    block_id: str | None
    embedded: bool


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


def parse_wikilink_inner(inner: str) -> tuple[str, str | None, str | None, str | None]:
    target_part, alias = (inner.split("|", 1) + [None])[:2] if "|" in inner else (inner, None)
    block_id = None
    heading = None

    if "#^" in target_part:
        target_part, block_id = target_part.split("#^", 1)
    elif "#" in target_part:
        target_part, heading = target_part.split("#", 1)

    return target_part.strip(), alias.strip() if alias else None, heading, block_id


def wikilinks(markdown: str) -> list[WikiLink]:
    links: list[WikiLink] = []
    for match in WIKILINK_RE.finditer(markdown):
        target, alias, heading, block_id = parse_wikilink_inner(match.group("inner"))
        links.append(
            WikiLink(
                raw=match.group(0),
                target=target,
                alias=alias,
                heading=heading,
                block_id=block_id,
                embedded=bool(match.group("embed")),
            )
        )
    return links


def markdown_links(markdown: str) -> list[str]:
    return [match.group("target") for match in MARKDOWN_LINK_RE.finditer(markdown)]


def inline_tags(markdown: str) -> list[str]:
    return sorted(set(TAG_RE.findall(markdown)))


def block_ids(markdown: str) -> list[str]:
    return sorted(set(BLOCK_ID_RE.findall(markdown)))


def frontmatter_tags(frontmatter: dict[str, Any]) -> list[str]:
    tags = frontmatter.get("tags", [])
    if isinstance(tags, str):
        return [tags.lstrip("#")]
    if isinstance(tags, list):
        return [str(tag).lstrip("#") for tag in tags]
    return []


def rewrite_wikilink_targets(
    markdown: str,
    old_names: set[str],
    replacement: str | Callable[[str], str],
) -> str:
    """Replace matching Obsidian wikilink targets."""
    if isinstance(replacement, str):
        plain_new = replacement
        replacement = lambda _matched: plain_new

    def replace(match: re.Match[str]) -> str:
        target, alias, heading, block_id = parse_wikilink_inner(match.group("inner"))
        if target not in old_names:
            return match.group(0)

        suffix = ""
        if block_id:
            suffix = f"#^{block_id}"
        elif heading:
            suffix = f"#{heading}"
        alias_part = f"|{alias}" if alias else ""
        embed = "!" if match.group("embed") else ""
        return f"{embed}[[{replacement(target)}{suffix}{alias_part}]]"

    return WIKILINK_RE.sub(replace, markdown)


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
