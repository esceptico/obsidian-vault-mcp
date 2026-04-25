import re
from dataclasses import dataclass
from typing import Callable


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


def rewrite_wikilink_targets(
    markdown: str,
    old_names: set[str],
    replacement: str | Callable[[str], str],
) -> str:
    """Replace wikilink targets in `old_names` with `replacement`.

    `replacement` is either a fixed string or a callable that receives the
    matched original target and returns the substitution. The callable form
    lets the caller emit a folder-qualified replacement when the source link
    was qualified (so collisions with same-stem notes elsewhere are avoided).
    """
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
