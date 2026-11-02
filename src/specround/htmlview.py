"""HTML documents for the render mode — the page itself, with its text still counted.

An ``.html`` document is reviewed the same way a markdown one is: the round
freezes its source text, every anchor is a codepoint offset into that text, and
raw mode and the diff show it as text. Only the render mode differs. Instead of
re-rendering, it shows the author's page, so a reviewer comments on what the
audience will see.

The page keeps one property the markdown render keeps (see :mod:`markdown`):

    for every emitted run, ``source[start:start + len(text)] == text``

Each run of prose the browser would show is wrapped in
``<span class="sr-t" data-s="N" data-n="LEN">`` and nothing else in the document changes —
removing the spans gives the source back byte for byte. Text the browser parses
differently from how it reads (scripts, styles, the head, SVG/MathML, form
controls) is never wrapped, so a run never claims an offset it does not have.

The result runs in a sandboxed frame (``app.html``), which is where a document's
own ``<script>`` is kept away from the review page and its token. ``data-n`` is
the run's length, so the frame can tell a run the page's scripts have rewritten
(rendered math, say) and refuse to count offsets in it.
"""

from __future__ import annotations

from html.parser import HTMLParser
from pathlib import PurePath

__all__ = ["SUFFIXES", "is_html", "render"]

SUFFIXES = (".html", ".htm")

#: Elements whose contents are not plain prose in the rendered page. A ``span``
#: in any of them would be dropped, parsed as foreign markup, or end up in a
#: script, so their text stays unwrapped and unselectable.
_OPAQUE = frozenset({
    "head", "title", "script", "style", "template", "noscript", "textarea",
    "select", "option", "optgroup", "svg", "math", "iframe", "object", "canvas",
    "xmp", "plaintext", "noembed", "noframes",
})
#: Foreign elements honour a self-closing ``/>``; every HTML element ignores it.
_FOREIGN = frozenset({"svg", "math"})
#: A newline straight after these start tags is dropped by the HTML parser.
_LEADING_NEWLINE = frozenset({"pre", "listing", "textarea"})


def is_html(path: str | PurePath) -> bool:
    return PurePath(path).suffix.lower() in SUFFIXES


class _Runs(HTMLParser):
    def __init__(self, text: str) -> None:
        super().__init__(convert_charrefs=False)
        self.text = text
        self.lines = [0]
        for index, char in enumerate(text):
            if char == "\n":
                self.lines.append(index + 1)
        self.open: list[str] = []
        self.after: str | None = None
        self.runs: list[tuple[int, int]] = []

    def _at(self) -> int:
        line, column = self.getpos()
        return self.lines[line - 1] + column

    def handle_starttag(self, tag: str, attrs: object) -> None:
        if tag == "body":
            self.open = [t for t in self.open if t != "head"]
        if tag in _OPAQUE:
            self.open.append(tag)
        self.after = tag

    def handle_startendtag(self, tag: str, attrs: object) -> None:
        # ``<script/>`` still opens a script in a browser, so its contents are
        # not prose even though this parser closed it.
        if tag in _OPAQUE and tag not in _FOREIGN:
            self.open.append(tag)
        self.after = None

    def handle_endtag(self, tag: str) -> None:
        if tag in self.open:
            while self.open.pop() != tag:
                pass
        self.after = None

    def handle_data(self, data: str) -> None:
        after, self.after = self.after, None
        if self.open or not data.strip():
            return
        start = self._at()
        end = start + len(data)
        if self.text[start:end] != data:  # pragma: no cover - the parser's own positions
            return
        if after in _LEADING_NEWLINE and data.startswith("\n"):
            start += 1
        # A carriage return is folded away by the browser and a NUL dropped, so
        # neither may sit inside a run: everything after it would count short.
        at = start
        while at < end:
            stops = [i for i in (self.text.find("\r", at, end), self.text.find("\0", at, end)) if i >= 0]
            stop = min(stops) if stops else end
            if self.text[at:stop].strip():
                self.runs.append((at, stop))
            at = stop + 1

    def _other(self, *_: object) -> None:
        self.after = None

    handle_entityref = handle_charref = handle_comment = handle_decl = handle_pi = unknown_decl = _other


def render(text: str) -> str:
    """The document with each prose run wrapped in an offset-carrying span."""
    parser = _Runs(text)
    parser.feed(text)
    parser.close()
    out: list[str] = []
    at = 0
    for start, end in parser.runs:
        out.append(text[at:start])
        out.append(f'<span class="sr-t" data-s="{start}" data-n="{end - start}">{text[start:end]}</span>')
        at = end
    out.append(text[at:])
    return "".join(out)
