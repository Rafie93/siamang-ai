"""The blocks of a document the owner may edit, numbered the way the web interface numbers them (``parseMarkdown``).

A block is a fenced code block whose language the document's catalog entry names (``Spec.blocks``): Mermaid in every
design document, plus static HTML in the wireframes and JSON in the design tokens. The nth block is counted over those
languages only, so a document with only Mermaid numbers its diagrams exactly as before. "Diagram" in names and routes
means any such block.
"""
from __future__ import annotations

import difflib
import json
import re
from itertools import zip_longest

MAX_DIAGRAM_CHARS = 20_000
LANGUAGES = ('mermaid', 'html', 'json')
MERMAID = ('mermaid',)
_FENCE = re.compile(r'^```\s*([A-Za-z]+)\s*$')


class InvalidDiagram(ValueError):
    """The source could not stay one fenced block of the document, or is not valid for its language."""


def _blocks(lines: list[str], languages=MERMAID) -> list[tuple[int, int, str]]:
    """The body of each block as a line range and its language. An unclosed fence runs to the end, as it renders."""
    out, i = [], 0
    while i < len(lines):
        if not lines[i].startswith('```'):
            i += 1
            continue
        match = _FENCE.match(lines[i])
        language, start = match.group(1).lower() if match else '', i + 1
        i = start
        while i < len(lines) and not lines[i].startswith('```'):
            i += 1
        if language in languages:
            out.append((start, i, language))
        i += 1
    return out


def blocks_of(content: str, languages=MERMAID) -> list[tuple[str, str]]:
    """Each block as ``(language, source)``."""
    lines = content.split('\n')
    return [(language, '\n'.join(lines[start:end])) for start, end, language in _blocks(lines, languages)]


def diagrams_of(content: str, languages=MERMAID) -> list[str]:
    return [source for _, source in blocks_of(content, languages)]


def check(language: str, source: str) -> str:
    """The source as it would be stored, or ``InvalidDiagram``."""
    body = source.replace('\r\n', '\n').replace('\r', '\n').strip('\n')
    if not body.strip() or len(body) > MAX_DIAGRAM_CHARS or any(line.startswith('```') for line in body.split('\n')):
        raise InvalidDiagram('diagram_invalid')
    if language == 'json':
        try:
            if not isinstance(json.loads(body), dict):
                raise InvalidDiagram('diagram_invalid')
        except ValueError as exc:
            raise InvalidDiagram('diagram_invalid') from exc
    return body


def replace_diagram(content: str, index: int, source: str, languages=MERMAID) -> str:
    """The document with one block replaced and every other line untouched. ``IndexError`` when it has no such block."""
    lines = content.split('\n')
    blocks = _blocks(lines, languages)
    if not 0 <= index < len(blocks):
        check(languages[0], source)  # an invalid edit is refused as such, whatever its number
        raise IndexError(index)
    start, end, language = blocks[index]
    return '\n'.join(lines[:start] + check(language, source).split('\n') + lines[end:])


def language_of(source: str, languages=MERMAID) -> str:
    """Which of ``languages`` a block written without its fence is in: HTML starts with a tag, JSON with a brace."""
    text = source.lstrip()
    for language, looks in (('html', text.startswith('<')), ('json', text.startswith('{'))):
        if looks and language in languages:
            return language
    return 'mermaid' if 'mermaid' in languages else languages[0]


def carry_block(content: str, index: int, source: str, languages=MERMAID) -> str:
    """A freshly written document with the owner's block in it: in its old place when a block of the same language
    is there, otherwise added at the end, because it still belongs in the document."""
    language = language_of(source, languages)
    found = blocks_of(content, languages)
    if index < len(found) and found[index][0] == language:
        try:
            return replace_diagram(content, index, source, languages)
        except InvalidDiagram:
            pass
    return f'{content}\n\n{fenced(source, language)}'.lstrip()


def line_changes(was: str, now: str) -> dict[str, int]:
    diff = list(difflib.ndiff(was.split('\n') if was else [], now.split('\n') if now else []))
    return dict(added=sum(d.startswith('+ ') for d in diff), removed=sum(d.startswith('- ') for d in diff))


def changed_diagrams(before: str, after: str, languages=MERMAID) -> list[dict[str, int]]:
    """Which blocks differ, and by how many lines. An edit replaces a block in place, so their numbers stay aligned."""
    pairs = zip_longest(diagrams_of(before, languages), diagrams_of(after, languages), fillvalue='')
    return [dict(diagram=index, **line_changes(was, now)) for index, (was, now) in enumerate(pairs) if was != now]


def fenced(source: str, language: str = 'mermaid') -> str:
    return f'```{language}\n{source}\n```'
