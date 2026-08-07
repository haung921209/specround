"""Document anchors — where a comment lives (G1).

The shape is the W3C Web Annotation selector pair: a ``TextQuoteSelector``
(``exact`` plus a bounded ``prefix``/``suffix`` of surrounding context) refined
by a ``TextPositionSelector`` (``start``/``end`` character offsets). Two
selectors are carried because either one alone is fragile: offsets die on any
edit above them, quotes are ambiguous when the same phrase repeats.

The write-time invariant is that the two selectors agree with the text they
were cut from — ``text[start:end] == exact``, and the context matches at that
position. A record that violates it is unrepairable later, so it is rejected
at append time rather than stored and discovered broken.

Scope: this module verifies an anchor against a *given* text. Finding an anchor
again in a revised document (fuzzy re-anchoring, orphan handling) is H4 and is
deliberately not implemented here — nothing in this module guesses.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from specround.errors import AnchorError

#: How much surrounding context a generated anchor carries on each side.
CONTEXT_CHARS = 32

_KEYS = ("exact", "start", "end", "prefix", "suffix")


@dataclass(frozen=True)
class Anchor:
    """A quote plus a position, both describing the same span of a document."""

    exact: str
    start: int
    end: int
    prefix: str = ""
    suffix: str = ""

    def __post_init__(self) -> None:
        for name in ("exact", "prefix", "suffix"):
            if not isinstance(getattr(self, name), str):
                raise AnchorError(f"anchor.{name} must be a string")
        for name in ("start", "end"):
            value = getattr(self, name)
            # bool is an int subclass; reject it so typos surface early.
            if not isinstance(value, int) or isinstance(value, bool):
                raise AnchorError(f"anchor.{name} must be an integer")
        if self.start < 0:
            raise AnchorError("anchor.start must not be negative")
        if self.end < self.start:
            raise AnchorError("anchor.end must not precede anchor.start")
        if self.end - self.start != len(self.exact):
            raise AnchorError(
                "anchor span and quote disagree: "
                f"end-start={self.end - self.start}, len(exact)={len(self.exact)}"
            )

    def verify(self, text: str) -> None:
        """Raise ``AnchorError`` unless this anchor still describes ``text``."""
        if self.end > len(text):
            raise AnchorError(
                f"anchor ends at {self.end} but the text is {len(text)} characters long"
            )
        found = text[self.start : self.end]
        if found != self.exact:
            raise AnchorError(
                f"quote mismatch at [{self.start}:{self.end}]: "
                f"expected {self.exact!r}, found {found!r}"
            )
        if self.prefix:
            begin = max(0, self.start - len(self.prefix))
            if text[begin : self.start] != self.prefix:
                raise AnchorError(f"prefix mismatch before offset {self.start}")
        if self.suffix:
            if text[self.end : self.end + len(self.suffix)] != self.suffix:
                raise AnchorError(f"suffix mismatch after offset {self.end}")

    def matches(self, text: str) -> bool:
        """Non-raising form of :meth:`verify`."""
        try:
            self.verify(text)
        except AnchorError:
            return False
        return True

    def to_json(self) -> dict[str, Any]:
        """The wire form. Empty context is omitted so the line stays terse."""
        payload: dict[str, Any] = {
            "exact": self.exact,
            "start": self.start,
            "end": self.end,
        }
        if self.prefix:
            payload["prefix"] = self.prefix
        if self.suffix:
            payload["suffix"] = self.suffix
        return payload

    @classmethod
    def from_json(cls, payload: Mapping[str, Any]) -> "Anchor":
        if not isinstance(payload, Mapping):
            raise AnchorError("anchor must be an object")
        unknown = sorted(set(payload) - set(_KEYS))
        if unknown:
            raise AnchorError(f"unknown anchor field(s): {', '.join(unknown)}")
        for required in ("exact", "start", "end"):
            if required not in payload:
                raise AnchorError(f"anchor is missing required field {required!r}")
        return cls(
            exact=payload["exact"],
            start=payload["start"],
            end=payload["end"],
            prefix=payload.get("prefix", ""),
            suffix=payload.get("suffix", ""),
        )


def anchor_for(text: str, start: int, end: int, *, context: int = CONTEXT_CHARS) -> Anchor:
    """Cut an anchor out of ``text``, filling context from its neighbourhood."""
    if not isinstance(text, str):
        raise AnchorError("text must be a string")
    if start < 0 or end < start:
        raise AnchorError(f"invalid span [{start}:{end}]")
    if end > len(text):
        raise AnchorError(f"span [{start}:{end}] runs past the end of the text")
    if context < 0:
        raise AnchorError("context must not be negative")
    anchor = Anchor(
        exact=text[start:end],
        start=start,
        end=end,
        prefix=text[max(0, start - context) : start],
        suffix=text[end : end + context],
    )
    anchor.verify(text)  # cheap belt-and-braces: generated anchors are always valid
    return anchor


def count_occurrences(text: str, quote: str) -> int:
    """How many appearances of ``quote`` :func:`anchor_for_quote` can address.

    Stepping by one character rather than by the length of the quote, because
    that is exactly how :func:`anchor_for_quote` walks them: ``str.count`` skips
    overlaps, so ``"aa"`` in ``"aaa"`` would read as unique here and still be
    addressable as occurrence 1 there. A count that disagrees with the indexer
    is a count that waves through the one case it exists to catch — which is why
    it lives beside the indexer instead of next to each caller that asks.
    """
    if not quote:
        return 0
    total = 0
    at = text.find(quote)
    while at != -1:
        total += 1
        at = text.find(quote, at + 1)
    return total


def anchor_for_quote(text: str, quote: str, *, occurrence: int = 0, context: int = CONTEXT_CHARS) -> Anchor:
    """Anchor the ``occurrence``-th appearance of ``quote`` (0-based).

    Repeated phrases are the normal case in prose, so the caller says which one
    it means instead of the tool picking silently.
    """
    if not quote:
        raise AnchorError("quote must not be empty")
    if occurrence < 0:
        raise AnchorError("occurrence must not be negative")
    start = -1
    for _ in range(occurrence + 1):
        start = text.find(quote, start + 1)
        if start == -1:
            total = text.count(quote)
            raise AnchorError(
                f"quote {quote!r} appears {total} time(s); occurrence {occurrence} does not exist"
            )
    return anchor_for(text, start, start + len(quote), context=context)
