from io import StringIO
from typing import Any

from ruamel.yaml import YAML


yaml = YAML()
yaml.default_flow_style = False


def split_frontmatter(markdown: str) -> tuple[dict[str, Any], str]:
    if not markdown.startswith("---\n"):
        return {}, markdown

    end = markdown.find("\n---", 4)
    if end == -1:
        return {}, markdown

    close_end = end + len("\n---")
    if close_end < len(markdown) and markdown[close_end] not in "\r\n":
        return {}, markdown

    raw_yaml = markdown[4:end].strip()
    body = markdown[close_end:].lstrip("\r\n")
    if not raw_yaml:
        return {}, body

    parsed = yaml.load(raw_yaml)
    if parsed is None:
        return {}, body
    if not isinstance(parsed, dict):
        raise ValueError("YAML frontmatter must be a mapping")
    return dict(parsed), body


def render_frontmatter(frontmatter: dict[str, Any], body: str) -> str:
    normalized_body = body.lstrip("\r\n")
    if not frontmatter:
        return normalized_body

    stream = StringIO()
    yaml.dump(frontmatter, stream)
    return f"---\n{stream.getvalue()}---\n{normalized_body}"


def patch_frontmatter(markdown: str, patch: dict[str, Any]) -> str:
    current, body = split_frontmatter(markdown)
    for key, value in patch.items():
        if value is None:
            current.pop(key, None)
        else:
            current[key] = value
    return render_frontmatter(current, body)
