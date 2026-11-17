"""The Mermaid diagrams inside a document, numbered the way the web interface numbers them (``parseMarkdown``)."""
from __future__ import annotations

import difflib
import re
from itertools import zip_longest

MAX_DIAGRAM_CHARS = 20_000
_MERMAID_FENCE = re.compile(r'^```\s*mermaid\s*$', re.IGNORECASE)


class InvalidDiagram(ValueError):
    """The source could not stay one fenced block of the document."""


def _blocks(lines: list[str]) -> list[tuple[int, int]]:
    """The body of each Mermaid fence as a line range. An unclosed fence runs to the end, as it renders."""
    out, i = [], 0
    while i < len(lines):
        if not lines[i].startswith('```'):
            i += 1
            continue
        mermaid, start = _MERMAID_FENCE.match(lines[i]), i + 1
        i = start
        while i < len(lines) and not lines[i].startswith('```'):
            i += 1
        if mermaid:
            out.append((start, i))
        i += 1
    return out


def diagrams_of(content: str) -> list[str]:
    lines = content.split('\n')
    return ['\n'.join(lines[start:end]) for start, end in _blocks(lines)]


def replace_diagram(content: str, index: int, source: str) -> str:
    """The document with one diagram replaced and every other line untouched. ``IndexError`` when it has no such diagram."""
    body = source.replace('\r\n', '\n').replace('\r', '\n').strip('\n')
    if not body.strip() or len(body) > MAX_DIAGRAM_CHARS or any(line.startswith('```') for line in body.split('\n')):
        raise InvalidDiagram('diagram_invalid')
    lines = content.split('\n')
    blocks = _blocks(lines)
    if not 0 <= index < len(blocks):
        raise IndexError(index)
    start, end = blocks[index]
    return '\n'.join(lines[:start] + body.split('\n') + lines[end:])


def line_changes(was: str, now: str) -> dict[str, int]:
    diff = list(difflib.ndiff(was.split('\n') if was else [], now.split('\n') if now else []))
    return dict(added=sum(d.startswith('+ ') for d in diff), removed=sum(d.startswith('- ') for d in diff))


def changed_diagrams(before: str, after: str) -> list[dict[str, int]]:
    """Which diagrams differ, and by how many lines. An edit replaces a diagram in place, so their numbers stay aligned."""
    pairs = zip_longest(diagrams_of(before), diagrams_of(after), fillvalue='')
    return [dict(diagram=index, **line_changes(was, now)) for index, (was, now) in enumerate(pairs) if was != now]


def fenced(source: str) -> str:
    return f'```mermaid\n{source}\n```'
