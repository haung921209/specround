"""Inline annotations — CriticMarkup markers, read off the document (G6).

The other two input surfaces need a program running: the web view serves a page,
and the CLI takes a quote on a command line. This one needs nothing. A reviewer
opens the file in whatever editor they already have, types the annotation where
it belongs, and saves. :func:`parse` reads the markers back out, and
:meth:`~specround.store.ReviewStore.harvest_document` turns them into ledger
events and leaves the document clean.

Four of CriticMarkup's five forms are read, which is the whole loop:

===================== ==================================================
``{>>why<<}``          a comment at that point
``{++added++}``        a suggestion that inserts text there
``{--removed--}``      a suggestion that removes the marked text
``{~~old~>new~~}``     a suggestion that replaces the marked text
===================== ==================================================

**The clean text is the document, and a marker is a proposal about it.** That
one sentence settles what every form contributes. ``{--x--}`` says "the document
says ``x`` and I propose removing it", so ``x`` stays in the harvested text and
the *suggestion* carries the removal; ``{++x++}`` says "``x`` is not here yet",
so it does not appear in the harvested text at all. Anything else would make
harvesting silently *apply* the proposals it was asked to record — and whether a
patch may be applied is a disposition somebody records (§4), not a side effect of
reading the file.

That is also why the anchors come out where they do. A form that quotes existing
text (``--``, ``~~``) anchors on exactly the span the reviewer marked; a form
that adds text (``++``) and a bare comment anchor on the **zero-length point**
where the marker sat, which is what :mod:`specround.anchors` calls an insertion
point and what :mod:`specround.reanchor` already knows how to carry. Nothing
here widens a span to the line or guesses at "the sentence this comment is
about": a comment's 32 characters of prefix are the text it follows, and that is
a fact rather than an interpretation.

Two classes of malformed input, kept apart because a reviewer does different
things about them:

* **A complete marker whose content cannot become an event** — an empty comment,
  a substitution with no ``~>``. :func:`parse` raises :class:`MarkupError` and
  names every one of them. The reviewer types the missing text or deletes the
  marker; harvesting half a document is not on offer.
* **Something that may not be an annotation at all** — an opener with no closer,
  or the ``{==highlight==}`` form this subset does not read. Reported in
  :attr:`Harvest.skipped` and **left in the document verbatim**. Refusing here
  would make a spec that merely mentions ``{--`` unharvestable, and dropping it
  silently is the loss G3 exists to prevent. Reported and left is neither.

Deliberately not read, and left as holes: nesting one marker inside another,
``{==highlight==}``, and pairing a comment with the edit it sits beside (a
``{--x--}{>>why<<}`` pair becomes two events whose offsets show the adjacency —
associating them would be the tool guessing at intent).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from specround.errors import SpecroundError

__all__ = [
    "COMMENT",
    "DELETE",
    "HIGHLIGHT",
    "INSERT",
    "KINDS",
    "SUBSTITUTE",
    "UNSUPPORTED",
    "UNTERMINATED",
    "Annotation",
    "Harvest",
    "MarkupError",
    "Skipped",
    "parse",
]

#: ``{>>…<<}`` — prose about the document at that point.
COMMENT = "comment"
#: ``{++…++}`` — text the reviewer proposes adding.
INSERT = "insert"
#: ``{--…--}`` — text the reviewer proposes removing.
DELETE = "delete"
#: ``{~~…~>…~~}`` — text the reviewer proposes replacing.
SUBSTITUTE = "substitute"
#: ``{==…==}`` — recognised so the hole stays visible, never harvested.
HIGHLIGHT = "highlight"

#: The four forms this module reads. Ordered as the docstring lists them.
KINDS = (COMMENT, INSERT, DELETE, SUBSTITUTE)

#: Why a marker was left in the document rather than harvested.
UNTERMINATED = "unterminated"
UNSUPPORTED = "unsupported"

#: What separates the two halves of a substitution.
_ARROW = "~>"

#: Every opener is this wide, which is what makes :attr:`Annotation.source_span`
#: arithmetic. Asserted against the table below rather than trusted.
_OPENER_CHARS = 3

#: opener → (closer, kind). Every opener differs at its second character, so no
#: two can begin at the same offset and the scan needs no precedence rule.
_FORMS: dict[str, tuple[str, str]] = {
    "{>>": ("<<}", COMMENT),
    "{++": ("++}", INSERT),
    "{--": ("--}", DELETE),
    "{~~": ("~~}", SUBSTITUTE),
    "{==": ("==}", HIGHLIGHT),
}

assert all(len(opener) == _OPENER_CHARS for opener in _FORMS), "openers must be uniform"

#: How much of a skipped marker a report shows.
_CLIP = 40


class MarkupError(SpecroundError):
    """A marker is complete, and its content cannot become an event.

    Distinct from a skipped marker on purpose. This one is unambiguously an
    annotation — the reviewer typed both ends of it — so declining it quietly
    would drop something somebody meant to say. The fix is in the document, and
    the message names every offset that needs it.
    """


@dataclass(frozen=True)
class Annotation:
    """One marker, placed in the text that remains after the markers go.

    ``start``/``end`` are offsets into :attr:`Harvest.clean`, and they are a
    span for the forms that quote existing text and a point (``start == end``)
    for the forms that do not. ``source_start``/``source_end`` name the marker
    in the original text, which is what a preview shows and what the rewrite
    removes.
    """

    kind: str
    #: Offsets into the clean text — the anchor span.
    start: int
    end: int
    #: The comment body. Non-empty exactly when ``kind`` is :data:`COMMENT`.
    body: str = ""
    #: Text the proposal takes out — the span ``start``/``end`` covers.
    removed: str = ""
    #: Text the proposal puts in.
    added: str = ""
    #: Where the marker itself was, in the text :func:`parse` was given.
    source_start: int = 0
    source_end: int = 0
    #: 1-based line of the marker in that same text — what a preview points at.
    #: It goes stale the moment the markers are removed, which is why it names
    #: the annotated file and not the document a reader will open afterwards.
    source_line: int = 0

    @property
    def suggestion(self) -> bool:
        """True when this becomes a ``suggestion.add`` rather than a comment."""
        return self.kind != COMMENT

    @property
    def anchored(self) -> bool:
        """True when the marker named existing text rather than a point."""
        return self.end > self.start

    @property
    def source_span(self) -> tuple[int, int]:
        """The same span, in the text :func:`parse` was handed.

        A form that quotes existing text carries those characters inside its own
        marker, three columns right of the opener — every opener is three
        characters, so this is arithmetic and not a search. A point form sits
        where the marker begins, which is where the point lands once the marker
        is gone.

        This is what lets a document that was annotated *before* its round was
        opened be harvested exactly: the round's base still holds the markers, so
        the span the anchor needs is right there, and no matcher has to guess at
        it.
        """
        if not self.anchored:
            return self.source_start, self.source_start
        inner = self.source_start + _OPENER_CHARS
        return inner, inner + len(self.removed)

    def proposed(self, clean: str) -> str:
        """``clean`` with this one proposal carried out — the patch's other side.

        Only ever handed to :func:`~specround.diffs.unified_patch`. The document
        itself is never written this way: applying a suggestion is a disposition
        (§4, H8), and a harvest records proposals rather than deciding them.
        """
        if self.kind == COMMENT:
            raise MarkupError("a comment proposes no edit")
        return clean[: self.start] + self.added + clean[self.end :]


@dataclass(frozen=True)
class Skipped:
    """A marker left in the document, and why it was not harvested."""

    reason: str
    opener: str
    #: Offset in the text :func:`parse` was given.
    start: int
    #: 1-based line, for a reviewer who has to go and look.
    line: int
    text: str


@dataclass(frozen=True)
class Harvest:
    """What one document holds: the text without the markers, and the markers."""

    #: The document with every harvested marker removed. This is the text the
    #: anchors count in, and the text the rewrite puts on disk — the two have to
    #: be the same string or every offset is off by the markers ahead of it.
    clean: str
    annotations: list[Annotation] = field(default_factory=list)
    skipped: list[Skipped] = field(default_factory=list)

    @property
    def found(self) -> bool:
        """True when there is anything to harvest."""
        return bool(self.annotations)


def parse(text: str) -> Harvest:
    """Read the markers out of ``text``, returning them and the text without them.

    Pure and deterministic: no clock, no filesystem, and a single left-to-right
    scan, so the same document always yields the same spans. Markers do not
    nest — each opener closes at the first closer of its own form — which keeps
    that scan unambiguous and leaves nesting as a hole rather than a guess.
    """
    if not isinstance(text, str):
        raise MarkupError("text must be a string")

    pieces: list[str] = []
    annotations: list[Annotation] = []
    skipped: list[Skipped] = []
    problems: list[str] = []
    # ``kept`` tracks the length of the clean text built so far, which is where
    # the next annotation's offsets count from. ``cursor`` is how far the
    # original text has been copied, and ``at`` is where the scan looks next —
    # the last two separate because a skipped marker advances the scan without
    # being copied yet, so it stays verbatim in the output.
    kept = 0
    cursor = 0
    at = 0

    while True:
        found = _next_marker(text, at)
        if found is None:
            break
        start, opener = found
        closer, kind = _FORMS[opener]
        payload_end = text.find(closer, start + len(opener))
        if payload_end == -1:
            skipped.append(_skip(text, start, opener, UNTERMINATED))
            at = start + len(opener)
            continue
        if kind == HIGHLIGHT:
            skipped.append(_skip(text, start, opener, UNSUPPORTED))
            # Past the opener rather than past the whole form, for the same
            # reason a skipped opener does not stop the scan: a stray ``{==`` in
            # prose would otherwise swallow every real marker up to the next
            # ``==}``, and losing those is worse than reading one out of a
            # highlight that this subset was not going to keep anyway.
            at = start + len(opener)
            continue

        payload = text[start + len(opener) : payload_end]
        end = payload_end + len(closer)
        pieces.append(text[cursor:start])
        kept += start - cursor
        cursor = end
        at = end

        problem, annotation = _read(kind, payload, kept, start, end, _line(text, start))
        if problem is not None:
            problems.append(f"offset {start} (line {_line(text, start)}): {problem}")
            continue
        assert annotation is not None  # one or the other, never both
        annotations.append(annotation)
        if annotation.removed:
            pieces.append(annotation.removed)
            kept += len(annotation.removed)

    pieces.append(text[cursor:])
    clean = "".join(pieces)

    if problems:
        raise MarkupError(
            f"{len(problems)} marker(s) cannot be harvested as written:\n  "
            + "\n  ".join(problems)
        )
    for annotation in annotations:
        # The offsets are bookkeeping, and bookkeeping that has drifted places
        # anchors on the wrong text without anything failing. Cheap to check.
        if clean[annotation.start : annotation.end] != annotation.removed:
            raise MarkupError(  # pragma: no cover - guards the loop above
                f"internal: {annotation.kind} span [{annotation.start}:{annotation.end}] "
                f"does not hold the text it names"
            )
    return Harvest(clean=clean, annotations=annotations, skipped=skipped)


def _read(
    kind: str, payload: str, kept: int, start: int, end: int, line: int
) -> tuple[str | None, Annotation | None]:
    """Turn one marker's payload into an annotation, or say what is wrong with it."""
    common = {
        "kind": kind,
        "source_start": start,
        "source_end": end,
        "source_line": line,
    }
    if kind == COMMENT:
        # Bodies are prose and the CLI strips them too; the payloads below are
        # document content, where a space is a character like any other.
        body = payload.strip()
        if not body:
            return "a comment marker with nothing in it", None
        return None, Annotation(start=kept, end=kept, body=body, **common)
    if kind == INSERT:
        if not payload:
            return "an insertion marker with nothing to insert", None
        return None, Annotation(start=kept, end=kept, added=payload, **common)
    if kind == DELETE:
        if not payload:
            return "a deletion marker with nothing to delete", None
        return None, Annotation(
            start=kept, end=kept + len(payload), removed=payload, **common
        )
    if _ARROW not in payload:
        return (
            f"a substitution needs {_ARROW!r} between the old text and the new "
            f"(got {_clip(payload)})",
            None,
        )
    removed, _, added = payload.partition(_ARROW)
    if not removed and not added:
        return "a substitution with nothing on either side", None
    return None, Annotation(
        start=kept, end=kept + len(removed), removed=removed, added=added, **common
    )


def _next_marker(text: str, at: int) -> tuple[int, str] | None:
    """The earliest opener at or after ``at``, or ``None``."""
    best: tuple[int, str] | None = None
    for opener in _FORMS:
        found = text.find(opener, at)
        if found != -1 and (best is None or found < best[0]):
            best = (found, opener)
    return best


def _skip(text: str, start: int, opener: str, reason: str) -> Skipped:
    break_at = text.find("\n", start)
    tail = text[start:] if break_at == -1 else text[start:break_at]
    return Skipped(
        reason=reason,
        opener=opener,
        start=start,
        line=_line(text, start),
        text=_clip(tail, quote=False),
    )


def _line(text: str, at: int) -> int:
    return text.count("\n", 0, at) + 1


def _clip(text: str, limit: int = _CLIP, *, quote: bool = True) -> str:
    flat = text if len(text) <= limit else text[:limit] + "…"
    return repr(flat) if quote else flat
