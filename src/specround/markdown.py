"""Markdown for the render mode — HTML that still knows where it came from (G6).

The render view has one requirement an ordinary markdown pipeline does not meet:
a comment made on rendered prose has to land on the same document anchor a
comment made on the raw text would. Anchors are character offsets into the
document (§5), so the renderer cannot throw source positions away. Every piece
of text that survives into the page carries the offset it was cut from, and
reading a selection back off the DOM is then arithmetic rather than a guess.

One invariant, which everything here exists to keep:

    for every emitted run, ``source[start:start + len(text)] == text``

That is why runs are split at any point where the assembled text stops being
contiguous in the document. Stripping a blockquote's ``>`` or a list marker
makes the string being scanned no longer a slice of the file; a run that
straddled the jump would be right about its first character and quietly wrong
about the rest, which is the class of failure this whole layer is built to
avoid.

Markers markdown consumes — ``**``, a heading's ``#``, a table's pipes — are not
runs and are not selectable. There is nothing in the document a reviewer means
by them, and inventing an anchor for them would put comments on syntax.

Scope is prose: the blocks these spec documents are written in. This is not a
CommonMark implementation and does not try to be. An unsupported construct
degrades to the paragraph it sits in, which still anchors correctly — the
guarantee is about offsets, not about typography.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterator, Sequence

__all__ = [
    "SAFE_SCHEMES",
    "Piece",
    "Run",
    "code_spans",
    "render",
    "runs_of",
    "safe_href",
]

#: Everything ``str.splitlines`` treats as a break, so a line's text never keeps
#: one and offsets still count every byte of it.
_BREAKS = "\r\n\v\f\x1c\x1d\x1e\x85\u2028\u2029"

_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})[ \t]*(\S*)[ \t]*$")
_HEADING = re.compile(r"^ {0,3}(#{1,6})[ \t]+(.*)$")
_RULE = re.compile(r"^ {0,3}([-*_])[ \t]*(?:\1[ \t]*){2,}$")
_QUOTE = re.compile(r"^ {0,3}>[ \t]?")
_ITEM = re.compile(r"^([ \t]*)([-*+]|\d{1,9}[.)])([ \t]+)(.*)$")
_DELIMITER = re.compile(r"^[ \t]*\|?[ \t]*:?-+:?[ \t]*(\|[ \t]*:?-+:?[ \t]*)*\|?[ \t]*$")
_AUTOLINK = re.compile(r"^<([a-zA-Z][a-zA-Z0-9+.-]*:[^<>\s]+)>")
_SCHEME = re.compile(r"^([a-zA-Z][a-zA-Z0-9+.-]*):")
#: Schemes a link in a reviewed document may carry into the page. A document is
#: input here, and the page it renders into holds the view's token — a
#: ``javascript:`` href in a spec somebody was sent would be a click away from
#: reading it. Anything relative (a sibling file, an anchor) has no scheme and is
#: always allowed.
SAFE_SCHEMES = frozenset({"http", "https", "mailto", "ftp", "ftps"})
#: Backslash may escape these, and the escaped character is a run of its own.
_PUNCT = set("\\`*_{}[]()#+-.!|~<>&\"'")
#: Characters a ``_`` may not be flanked by, so ``data_start`` stays one word.
_WORD = re.compile(r"[0-9A-Za-z_]")


def escape(text: str) -> str:
    """HTML-escape without changing the character count of the escaped text.

    Entity references are longer as markup and identical as text, so
    ``textContent`` in the browser still equals the source slice — which is the
    only property the offset contract depends on.
    """
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _attr(value: str) -> str:
    return escape(value).replace('"', "&quot;")


def safe_href(value: str) -> str:
    """``value`` if a link may point there, otherwise nothing.

    Dropping the target rather than the link keeps the label anchorable — the
    text is still a run of the document, and a comment on it still lands where it
    should. A refused link renders as text that goes nowhere, which is what it
    should have been.
    """
    match = _SCHEME.match(value.strip())
    if match is None:
        return value
    return value if match.group(1).lower() in SAFE_SCHEMES else ""


@dataclass(frozen=True)
class Run:
    """One stretch of the document that reached the page verbatim."""

    start: int
    text: str

    @property
    def end(self) -> int:
        return self.start + len(self.text)


@dataclass(frozen=True)
class Piece:
    """A line of the document, as the renderer currently sees it.

    ``text`` shrinks as the renderer descends — a blockquote's ``>`` and a list
    marker are stripped, and ``start`` moves with them. ``tail`` is the line
    break that followed the line in the file, kept because it is what joins two
    pieces back together: assembling them with a plain ``\n`` instead would put a
    character in the page the document does not contain, and on a CRLF file that
    is not a cosmetic difference but a run whose text no longer matches the
    offsets it claims.
    """

    start: int
    text: str
    tail: str = ""

    @property
    def end(self) -> int:
        return self.start + len(self.text)

    def dedent(self, width: int) -> "Piece":
        """Drop ``width`` leading characters, moving the offset with them."""
        return Piece(self.start + width, self.text[width:], self.tail)

    def cut(self, start: int, end: int) -> "Piece":
        """The slice ``[start:end)`` of this line, as a piece of its own."""
        return Piece(self.start + start, self.text[start:end])


class Chunk:
    """Text assembled from pieces of a document, remembering each character's home.

    The renderer strips block markers as it descends, so the string an inline
    scan runs over is generally not one slice of the file. This keeps the map
    from assembled offsets back to source offsets, and :meth:`runs` is where
    that map turns back into anchorable spans.
    """

    def __init__(self, pieces: Sequence[Piece]) -> None:
        parts: list[str] = []
        offsets: list[int] = []
        previous: Piece | None = None
        for piece in pieces:
            if previous is not None:
                # The break the file has after the previous line, at the offsets
                # the file has it — never a stand-in for it.
                for index, char in enumerate(previous.tail):
                    parts.append(char)
                    offsets.append(previous.end + index)
            parts.append(piece.text)
            offsets.extend(range(piece.start, piece.end))
            previous = piece
        self.text = "".join(parts)
        self._offsets = offsets

    def __len__(self) -> int:
        return len(self.text)

    def origin(self, index: int) -> int:
        """The document offset of the character at ``index``."""
        return self._offsets[index]

    def runs(self, start: int, end: int) -> list[Run]:
        """``text[start:end]``, split into maximal stretches contiguous in the source."""
        out: list[Run] = []
        index = start
        while index < end:
            head = index
            index += 1
            while index < end and self._offsets[index] == self._offsets[index - 1] + 1:
                index += 1
            out.append(Run(self._offsets[head], self.text[head:index]))
        return out


def lines_of(text: str) -> list[Piece]:
    """The document as pieces — one per line, each keeping its own break."""
    out: list[Piece] = []
    at = 0
    for raw in text.splitlines(keepends=True):
        content = raw
        while content and content[-1] in _BREAKS:
            content = content[:-1]
        out.append(Piece(at, content, raw[len(content) :]))
        at += len(raw)
    return out


def code_spans(text: str) -> list[tuple[int, int]]:
    """Half-open ranges of ``text`` that are code rather than prose.

    Fenced blocks (their fence lines included) and inline backtick spans. What
    wants this is the inline-annotation harvester: a document that explains an
    annotation syntax has to be able to *write* that syntax, and every one of
    these spec files quotes ``{--like this--}`` in backticks. Without somewhere
    to say "this is a specimen, not an instruction", the harvester mangles the
    documentation of its own feature — including this repository's.

    Deliberately narrow. Indented code blocks are not recognised, and a code span
    is matched within one line: a rule that decides whether a reviewer's marker
    counts should be one a reader can apply by eye, and "it is between backticks
    on this line" is that. Missing a construct here costs a specimen being
    harvested, which the dry run shows; over-reaching would silently swallow a
    real annotation, which nothing shows.

    Ranges are returned in ascending order and never overlap.
    """
    spans: list[tuple[int, int]] = []
    fence: str | None = None
    for piece in lines_of(text):
        marker = _FENCE.match(piece.text)
        if fence is not None:
            # Everything in the block is verbatim, the closing fence with it.
            spans.append((piece.start, piece.end))
            if marker and marker.group(1)[0] == fence[0] and len(marker.group(1)) >= len(fence):
                if not marker.group(2):  # a closing fence carries no info string
                    fence = None
            continue
        if marker:
            fence = marker.group(1)
            spans.append((piece.start, piece.end))
            continue
        spans.extend(_inline_code(piece))
    return spans


def _inline_code(piece: Piece) -> list[tuple[int, int]]:
    """Backtick spans on one line, closed by a run of the same length."""
    spans: list[tuple[int, int]] = []
    text = piece.text
    at = 0
    while at < len(text):
        if text[at] != "`":
            at += 1
            continue
        run = at
        while run < len(text) and text[run] == "`":
            run += 1
        ticks = text[at:run]
        close = _closing_run(text, ticks, run)
        if close == -1:
            # An unmatched run is not a span. Resuming after it rather than
            # inside it keeps ``a ` b `c` `` from reading as one long span.
            at = run
            continue
        spans.append((piece.start + at, piece.start + close + len(ticks)))
        at = close + len(ticks)
    return spans


def _closing_run(text: str, ticks: str, at: int) -> int:
    """Offset of the next run of exactly ``ticks``, or ``-1``."""
    while True:
        found = text.find(ticks, at)
        if found == -1:
            return -1
        after = found + len(ticks)
        if after >= len(text) or text[after] != "`":
            return found
        # Part of a longer run, which does not close this one — step past it.
        at = after
        while at < len(text) and text[at] == "`":
            at += 1


def render(text: str) -> str:
    """Render ``text`` to HTML whose text runs carry their document offsets."""
    renderer = _Renderer()
    renderer.blocks(lines_of(text))
    return "".join(renderer.out)


def runs_of(html: str) -> Iterator[Run]:
    """Read the runs back out of rendered HTML — the inverse of the contract.

    The browser does this by walking the DOM. Having it in Python too is what
    lets a test assert the invariant over a whole document instead of over the
    handful of constructs someone thought to check.
    """
    import html as html_module

    for match in re.finditer(r'<span class="t" data-s="(\d+)">(.*?)</span>', html, re.DOTALL):
        yield Run(int(match.group(1)), html_module.unescape(match.group(2)))


class _Renderer:
    """Block structure outward, inline structure inward, offsets throughout."""

    def __init__(self) -> None:
        self.out: list[str] = []

    # -- text ------------------------------------------------------------

    def text(self, chunk: Chunk, start: int, end: int) -> None:
        for run in chunk.runs(start, end):
            if not run.text:
                continue
            self.out.append(f'<span class="t" data-s="{run.start}">{escape(run.text)}</span>')

    # -- blocks ----------------------------------------------------------

    def blocks(self, lines: Sequence[Piece]) -> None:
        index = 0
        total = len(lines)
        while index < total:
            line = lines[index].text
            if not line.strip():
                index += 1
            elif _FENCE.match(line):
                index = self._fence(lines, index)
            elif _HEADING.match(line):
                index = self._heading(lines, index)
            elif _RULE.match(line):
                self.out.append("<hr>")
                index += 1
            elif _QUOTE.match(line):
                index = self._blockquote(lines, index)
            elif self._is_table(lines, index):
                index = self._table(lines, index)
            elif _ITEM.match(line):
                index = self._list(lines, index)
            else:
                index = self._paragraph(lines, index)

    def _fence(self, lines: Sequence[Piece], index: int) -> int:
        match = _FENCE.match(lines[index].text)
        assert match is not None
        marker, info = match.group(1), match.group(2)
        index += 1
        body: list[Piece] = []
        while index < len(lines):
            candidate = lines[index].text.strip()
            if candidate.startswith(marker[0] * len(marker)) and set(candidate) == {marker[0]}:
                index += 1
                break
            body.append(lines[index])
            index += 1
        language = f' data-lang="{_attr(info)}"' if info else ""
        self.out.append(f"<pre{language}><code>")
        if body:
            chunk = Chunk(body)
            self.text(chunk, 0, len(chunk))
        self.out.append("</code></pre>")
        return index

    def _heading(self, lines: Sequence[Piece], index: int) -> int:
        piece = lines[index]
        match = _HEADING.match(piece.text)
        assert match is not None
        level = len(match.group(1))
        body = match.group(2).rstrip()
        # A closing run of #'s is decoration, not content — drop it from the run
        # rather than from the offset, which stays where the text starts.
        trimmed = body.rstrip("#").rstrip() if body.endswith("#") else body
        chunk = Chunk([piece.cut(match.start(2), match.start(2) + len(trimmed))])
        self.out.append(f"<h{level}>")
        self._inline(chunk, 0, len(chunk))
        self.out.append(f"</h{level}>")
        return index + 1

    def _blockquote(self, lines: Sequence[Piece], index: int) -> int:
        inner: list[Piece] = []
        while index < len(lines):
            piece = lines[index]
            match = _QUOTE.match(piece.text)
            if match is None:
                break
            inner.append(piece.dedent(match.end()))
            index += 1
        self.out.append("<blockquote>")
        self.blocks(inner)
        self.out.append("</blockquote>")
        return index

    def _paragraph(self, lines: Sequence[Piece], index: int) -> int:
        body: list[Piece] = []
        while index < len(lines):
            if not lines[index].text.strip():
                break
            if body and self._starts_block(lines, index):
                break
            body.append(lines[index])
            index += 1
        chunk = Chunk(body)
        self.out.append("<p>")
        self._inline(chunk, 0, len(chunk))
        self.out.append("</p>")
        return index

    def _starts_block(self, lines: Sequence[Piece], index: int) -> bool:
        line = lines[index].text
        return bool(
            _FENCE.match(line)
            or _HEADING.match(line)
            or _RULE.match(line)
            or _QUOTE.match(line)
            or _ITEM.match(line)
            or self._is_table(lines, index)
        )

    # -- lists -----------------------------------------------------------

    def _list(self, lines: Sequence[Piece], index: int) -> int:
        first = _ITEM.match(lines[index].text)
        assert first is not None
        indent = len(first.group(1))
        ordered = first.group(2)[0] not in "-*+"
        self.out.append("<ol>" if ordered else "<ul>")
        while index < len(lines):
            piece = lines[index]
            match = _ITEM.match(piece.text)
            if match is None or len(match.group(1)) != indent:
                break
            width = match.end(3)
            body: list[Piece] = [piece.dedent(width)]
            index += 1
            index = self._continuation(lines, index, width, body)
            self.out.append("<li>")
            self._item(body)
            self.out.append("</li>")
        self.out.append("</ol>" if ordered else "</ul>")
        return index

    def _continuation(
        self,
        lines: Sequence[Piece],
        index: int,
        width: int,
        body: list[Piece],
    ) -> int:
        """Absorb the lines that belong to the item just opened.

        Indented lines are dedented by the marker's width so a nested list or a
        second paragraph inside the item parses as itself; the offsets move with
        the dedent, which is exactly what :class:`Chunk` is for. A blank line is
        only kept when something indented follows it, or a trailing blank would
        turn every tight item into a loose one.
        """
        pending = 0
        while index < len(lines):
            piece = lines[index]
            if not piece.text.strip():
                pending += 1
                index += 1
                continue
            lead = len(piece.text) - len(piece.text.lstrip())
            if lead >= width:
                for _ in range(pending):
                    # A separator, never a joiner: it carries no break, so the
                    # blank line cannot claim a character of the document.
                    body.append(Piece(piece.start, ""))
                pending = 0
                body.append(piece.dedent(width))
                index += 1
                continue
            if pending or _ITEM.match(piece.text) or self._starts_block(lines, index):
                break
            # A lazy continuation of the item's paragraph: no indent, but it is
            # prose, and markdown lets it run on.
            body.append(piece)
            index += 1
        return index

    def _item(self, body: Sequence[Piece]) -> None:
        """Render an item's content, tight when it is a single paragraph."""
        loose = any(not piece.text.strip() for piece in body[1:]) or any(
            _ITEM.match(piece.text) or _FENCE.match(piece.text) or _QUOTE.match(piece.text)
            for piece in body[1:]
        )
        if loose:
            self.blocks(body)
            return
        chunk = Chunk(list(body))
        self._inline(chunk, 0, len(chunk))

    # -- tables ----------------------------------------------------------

    def _is_table(self, lines: Sequence[Piece], index: int) -> bool:
        if "|" not in lines[index].text:
            return False
        following = index + 1
        if following >= len(lines):
            return False
        delimiter = lines[following].text
        return "-" in delimiter and bool(_DELIMITER.match(delimiter))

    def _table(self, lines: Sequence[Piece], index: int) -> int:
        header = self._cells(lines[index])
        aligns = _alignments(lines[index + 1].text, len(header))
        index += 2
        self.out.append("<table><thead><tr>")
        for position, cell in enumerate(header):
            self._cell("th", cell, aligns[position] if position < len(aligns) else "")
        self.out.append("</tr></thead><tbody>")
        while index < len(lines):
            line = lines[index].text
            if not line.strip() or "|" not in line:
                break
            self.out.append("<tr>")
            for position, cell in enumerate(self._cells(lines[index])):
                self._cell("td", cell, aligns[position] if position < len(aligns) else "")
            self.out.append("</tr>")
            index += 1
        self.out.append("</tbody></table>")
        return index

    def _cell(self, tag: str, cell: Piece, align: str) -> None:
        style = f' style="text-align:{align}"' if align else ""
        chunk = Chunk([cell])
        self.out.append(f"<{tag}{style}>")
        self._inline(chunk, 0, len(chunk))
        self.out.append(f"</{tag}>")

    def _cells(self, line: Piece) -> list[Piece]:
        """Split a table row on unescaped pipes, keeping each cell's offset."""
        text = line.text
        bounds: list[tuple[int, int]] = []
        at = 0
        current = 0
        while at < len(text):
            if text[at] == "\\" and at + 1 < len(text):
                at += 2
                continue
            if text[at] == "|":
                bounds.append((current, at))
                current = at + 1
            at += 1
        bounds.append((current, len(text)))
        cells = [_trimmed(line, low, high) for low, high in bounds]
        # ``| a | b |`` yields empty edge cells; a pipe-less row yields one cell.
        if cells and not cells[0].text:
            cells = cells[1:]
        if cells and not cells[-1].text:
            cells = cells[:-1]
        return cells

    # -- inline ----------------------------------------------------------

    def _inline(self, chunk: Chunk, low: int, high: int) -> None:
        text = chunk.text
        pending = low
        at = low
        while at < high:
            char = text[at]
            consumed = 0
            if char == "\\" and at + 1 < high and text[at + 1] in _PUNCT:
                self.text(chunk, pending, at)
                self.text(chunk, at + 1, at + 2)
                consumed = 2
            elif char == "`":
                consumed = self._code(chunk, at, high, pending)
            elif char == "!" and at + 1 < high and text[at + 1] == "[":
                consumed = self._image(chunk, at, high, pending)
            elif char == "[":
                consumed = self._link(chunk, at, high, pending)
            elif char == "<":
                consumed = self._autolink(chunk, at, high, pending)
            elif char in "*_~":
                consumed = self._emphasis(chunk, at, high, pending)
            if consumed:
                at += consumed
                pending = at
            else:
                at += 1
        self.text(chunk, pending, high)

    def _code(self, chunk: Chunk, at: int, high: int, pending: int) -> int:
        text = chunk.text
        ticks = 0
        while at + ticks < high and text[at + ticks] == "`":
            ticks += 1
        fence = "`" * ticks
        close = text.find(fence, at + ticks, high)
        while close != -1 and close + ticks < high and text[close + ticks] == "`":
            close = text.find(fence, close + ticks + 1, high)
        if close == -1:
            return 0
        self.text(chunk, pending, at)
        self.out.append("<code>")
        self.text(chunk, at + ticks, close)
        self.out.append("</code>")
        return close + ticks - at

    def _link(self, chunk: Chunk, at: int, high: int, pending: int) -> int:
        found = self._reference(chunk.text, at, high)
        if found is None:
            return 0
        label, close, href = found
        self.text(chunk, pending, at)
        self.out.append(f'<a href="{_attr(safe_href(href))}" rel="noreferrer">')
        self._inline(chunk, at + 1, label)
        self.out.append("</a>")
        return close + 1 - at

    def _image(self, chunk: Chunk, at: int, high: int, pending: int) -> int:
        """``![alt](src)`` — the one construct that draws its reference.

        The label becomes an ``alt`` attribute, which takes it out of the anchor
        space: attributes are not text runs, so nothing in the page claims to be
        that stretch of the document. That is the honest trade and not a loss —
        a reviewer with something to say about the picture says it in the raw
        mode, where ``![alt](src)`` is ordinary text and anchors like any other,
        and the two modes share one anchor space by construction (§3). The
        alternative, emitting the alt text as a run *beside* the image, would put
        a caption on the page that the document does not have.

        A source :func:`safe_href` will not carry degrades to :meth:`_link`'s
        answer — the ``!`` as literal text and the label as a link that goes
        nowhere. An ``<img>`` with an empty ``src`` is a broken picture that in
        some browsers re-requests the page it sits in; the label as text is what
        the construct should have been.
        """
        found = self._reference(chunk.text, at + 1, high)
        if found is None:
            return 0
        label, close, src = found
        target = safe_href(src)
        if not target:
            return 0
        self.text(chunk, pending, at)
        alt = chunk.text[at + 2 : label]
        self.out.append(f'<img src="{_attr(target)}" alt="{_attr(alt)}" loading="lazy">')
        return close + 1 - at

    def _reference(self, text: str, at: int, high: int) -> tuple[int, int, str] | None:
        """``[label](target)`` starting at ``at``, as ``(label end, ``)`` , target)``.

        One scan for the two constructs that use it. Two copies would be two
        definitions of what a bracketed reference is, and they drift on the first
        edge case somebody fixes in only one of them.
        """
        depth = 0
        label = -1
        for index in range(at, high):
            if text[index] == "[":
                depth += 1
            elif text[index] == "]":
                depth -= 1
                if depth == 0:
                    label = index
                    break
        if label == -1 or label + 1 >= high or text[label + 1] != "(":
            return None
        close = text.find(")", label + 2, high)
        if close == -1:
            return None
        inside = text[label + 2 : close]
        return label, close, inside.split()[0] if inside.strip() else ""

    def _autolink(self, chunk: Chunk, at: int, high: int, pending: int) -> int:
        match = _AUTOLINK.match(chunk.text, at, high)
        if match is None:
            return 0
        self.text(chunk, pending, at)
        self.out.append(f'<a href="{_attr(safe_href(match.group(1)))}" rel="noreferrer">')
        self.text(chunk, match.start(1), match.end(1))
        self.out.append("</a>")
        return match.end() - at

    def _emphasis(self, chunk: Chunk, at: int, high: int, pending: int) -> int:
        text = chunk.text
        char = text[at]
        length = 2 if at + 1 < high and text[at + 1] == char else 1
        if char == "~" and length == 1:
            return 0
        marker = char * length
        if char == "_" and at > 0 and _WORD.match(text[at - 1]):
            # ``data_start`` is one word, not an emphasis that never closes.
            return 0
        if at + length >= high or text[at + length].isspace():
            return 0
        search = at + length
        while True:
            close = text.find(marker, search, high)
            if close == -1:
                return 0
            if text[close - 1].isspace():
                search = close + 1
                continue
            if char == "_" and close + length < high and _WORD.match(text[close + length]):
                search = close + 1
                continue
            break
        tag = {("*", 1): "em", ("_", 1): "em", ("*", 2): "strong", ("_", 2): "strong"}.get(
            (char, length), "del"
        )
        self.text(chunk, pending, at)
        self.out.append(f"<{tag}>")
        self._inline(chunk, at + length, close)
        self.out.append(f"</{tag}>")
        return close + length - at


def _trimmed(line: Piece, low: int, high: int) -> Piece:
    """A cell of a table row, without the whitespace padding around it."""
    text = line.text
    while low < high and text[low].isspace():
        low += 1
    while high > low and text[high - 1].isspace():
        high -= 1
    return line.cut(low, high)


def _alignments(delimiter: str, columns: int) -> list[str]:
    out: list[str] = []
    for cell in delimiter.strip().strip("|").split("|"):
        spec = cell.strip()
        if spec.startswith(":") and spec.endswith(":"):
            out.append("center")
        elif spec.endswith(":"):
            out.append("right")
        elif spec.startswith(":"):
            out.append("left")
        else:
            out.append("")
    return out[:columns] if len(out) > columns else out
