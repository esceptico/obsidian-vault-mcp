import re
from collections.abc import Callable
from dataclasses import dataclass

WIKILINK_RE = re.compile(r"(?P<embed>!)?\[\[(?P<inner>[^\]\n]+)\]\]")
MARKDOWN_LINK_RE = re.compile(r"(?P<embed>!)?\[[^\]\n]*\]\((?P<target>[^)\n]+)\)")
TAG_RE = re.compile(r"(?<![\w/])#([A-Za-z0-9][A-Za-z0-9_/-]*)")
BLOCK_ID_RE = re.compile(r"(?m)(?:^|\s)\^([A-Za-z0-9-]+)\s*$")
FENCE_RE = re.compile(r"(?m)^(?P<fence>`{3,}|~{3,}).*$")


@dataclass(frozen=True)
class WikiLink:
    raw: str
    target: str
    alias: str | None
    heading: str | None
    block_id: str | None
    embedded: bool


def parse_wikilink_inner(inner: str) -> tuple[str, str | None, str | None, str | None]:
    target_part, alias = _split_once(inner, "|")
    block_id = None
    heading = None

    if "#^" in target_part:
        target_part, block_id = target_part.split("#^", 1)
    elif "#" in target_part:
        target_part, heading = target_part.split("#", 1)

    return target_part.strip(), alias.strip() if alias else None, heading, block_id


def wikilinks(markdown: str) -> list[WikiLink]:
    links: list[WikiLink] = []
    for text in _non_code_blocks(markdown):
        for match in WIKILINK_RE.finditer(text):
            target, alias, heading, block_id = parse_wikilink_inner(
                match.group("inner")
            )
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
    return [
        match.group("target")
        for text in _non_code_blocks(markdown)
        for match in MARKDOWN_LINK_RE.finditer(text)
    ]


def inline_tags(markdown: str) -> list[str]:
    return sorted(
        {tag for text in _non_code_blocks(markdown) for tag in TAG_RE.findall(text)}
    )


def block_ids(markdown: str) -> list[str]:
    return sorted(
        {
            block_id
            for text in _non_code_blocks(markdown)
            for block_id in BLOCK_ID_RE.findall(text)
        }
    )


def rewrite_wikilink_targets(
    markdown: str,
    old_names: set[str],
    replacement: str | Callable[[str], str],
) -> str:
    """Replace matching Obsidian wikilink targets."""
    if isinstance(replacement, str):
        replacement = _constant_replacement(replacement)
    code_ranges = _code_block_ranges(markdown)

    def replace(match: re.Match[str]) -> str:
        if _inside_ranges(match.start(), code_ranges):
            return match.group(0)
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


def _constant_replacement(value: str) -> Callable[[str], str]:
    def replace(_matched: str) -> str:
        return value

    return replace


def _split_once(value: str, separator: str) -> tuple[str, str | None]:
    if separator not in value:
        return value, None
    left, right = value.split(separator, 1)
    return left, right


def _non_code_blocks(markdown: str) -> list[str]:
    blocks: list[str] = []
    start = 0
    for code_start, code_end in _code_block_ranges(markdown):
        if start < code_start:
            blocks.append(markdown[start:code_start])
        start = code_end
    if start < len(markdown):
        blocks.append(markdown[start:])
    return blocks


def _code_block_ranges(markdown: str) -> list[tuple[int, int]]:
    ranges: list[tuple[int, int]] = []
    opened: re.Match[str] | None = None
    for match in FENCE_RE.finditer(markdown):
        if opened is None:
            opened = match
            continue
        if match.group("fence")[0] == opened.group("fence")[0]:
            ranges.append((opened.start(), match.end()))
            opened = None
    if opened is not None:
        ranges.append((opened.start(), len(markdown)))
    return ranges


def _inside_ranges(position: int, ranges: list[tuple[int, int]]) -> bool:
    return any(start <= position < end for start, end in ranges)
