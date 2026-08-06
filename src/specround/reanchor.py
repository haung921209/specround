"""Re-anchoring — carrying a comment across a revision (G1, H4).

:mod:`specround.anchors` answers "does this anchor still describe this text".
This module answers the harder question that follows a revision: "where did it
go?" — and, when the answer is nowhere, says so out loud instead of dropping
the comment (G3, loss 0).

The ladder is Hypothesis' fuzzy anchoring with the DOM rung removed, since a
markdown file has no Range selector to try first:

1. ``position`` — the stored offsets still hold and the quote verifies there.
2. ``quote`` — the quote appears verbatim somewhere; context and the old
   position pick between occurrences.
3. ``normalized`` — the same search after a deterministic fold (smart quotes,
   dashes, runs of whitespace), which is what survives a reflow.
4. ``fuzzy`` — approximate alignment near candidate positions, gated by a
   similarity floor.

Two rules hold across every rung. **Every result is cut from the revised text**
— an anchor this module returns always verifies against the text it was found
in, because :func:`~specround.anchors.anchor_for` re-cuts the quote and the
context from that text rather than carrying the old ones forward. And **nothing
is guessed silently**: a rung that cannot clear the floor falls through to the
next, the last one falls through to an orphan with a reason, and a match that
was picked out of a tie is flagged ``ambiguous`` rather than presented as the
obvious answer.

Cost is bounded by constants, not by the document. Fuzzy alignment is
quadratic, so candidates are generated from word seeds, ranked by a linear
prefilter, and only the best :data:`ALIGN_CANDIDATES` of them are aligned
exactly, inside a window of the quote plus :data:`ALIGN_PAD` on each side. The
failure mode this avoids is on record: Hypothesis measured multi-second blocking
on short quotes in long documents. Every one of those bounds is taken **around
the old position** rather than from the top of the document: a cap that decides
which candidates exist would otherwise decide the answer, by hiding the true
span from the scoring it was supposed to win.
"""

from __future__ import annotations

import re
import unicodedata
from bisect import bisect_left
from dataclasses import dataclass
from difflib import SequenceMatcher

from specround.anchors import Anchor, anchor_for
from specround.errors import AnchorError

#: The offsets still hold: nothing above the anchor moved.
POSITION = "position"
#: The quote was found verbatim, possibly somewhere else in the document.
QUOTE = "quote"
#: Found verbatim after folding quotes, dashes, and whitespace runs.
NORMALIZED = "normalized"
#: Found by approximate alignment — the quoted text itself was edited.
FUZZY = "fuzzy"

#: Closed vocabulary, ordered from most to least certain. The ledger records
#: which rung matched, so a reader can tell a moved comment from a rewritten one.
STRATEGIES = (POSITION, QUOTE, NORMALIZED, FUZZY)

#: How similar an approximate match has to be before it counts as the same span.
MIN_SIMILARITY = 0.7
#: How much of an anchor's neighbourhood has to survive before *any* rung may
#: claim a span. A perfect quote match is not on its own evidence of the same
#: place — prose repeats, and the same sentence under a different heading is a
#: different sentence. Without this the two most trusted rungs were the only
#: ones with no veto at all, so a deleted section moved its comments onto their
#: twins and recorded the move as ``quote``, the highest confidence there is.
MIN_CONTEXT = 0.7
#: Candidate positions considered per fuzzy search.
MAX_CANDIDATES = 64
#: Of those, how many get an exact (quadratic) alignment.
ALIGN_CANDIDATES = 3
#: Slack on each side of an alignment window, in characters.
ALIGN_PAD = 64
#: Cap on a verbatim occurrence scan, so a one-character quote cannot run away.
MAX_OCCURRENCES = 1024
#: How far a fuzzy span may grow outward to stop cutting a word in half.
SNAP_CHARS = 16
#: Word seeds taken from a quote to generate candidate positions.
SEED_COUNT = 3
#: Similarity is compared at this precision, so ties are decided by the
#: tie-breakers below rather than by float noise.
_PRECISION = 6

_WORD = re.compile(r"\w+", re.UNICODE)

#: Characters folded to an ASCII equivalent before the ``normalized`` rung.
#: Applied after Unicode composition, so a decomposed source character is folded
#: by the same table its composed form would be.
_FOLD = {
    "‘": "'", "’": "'", "‚": "'", "‛": "'",
    "“": '"', "”": '"', "„": '"', "‟": '"',
    "‐": "-", "‑": "-", "‒": "-", "–": "-",
    "—": "-", "―": "-", "−": "-",
}


@dataclass(frozen=True)
class Rebind:
    """Where an anchor landed in a revised document, or why it did not.

    ``anchor`` is ``None`` exactly when the comment is orphaned; ``reason`` is
    filled in that case and empty otherwise.
    """

    anchor: Anchor | None
    strategy: str | None = None
    ambiguous: bool = False
    reason: str = ""

    @property
    def found(self) -> bool:
        return self.anchor is not None

    @property
    def orphaned(self) -> bool:
        return self.anchor is None


def reanchor(anchor: Anchor, text: str, *, min_similarity: float = MIN_SIMILARITY) -> Rebind:
    """Find ``anchor`` again in ``text``, or report it orphaned.

    Deterministic: the same anchor and the same text always produce the same
    :class:`Rebind`. Nothing here reads a clock, a filesystem, or a random
    source, and every ordering used to break a tie is total.
    """
    if not isinstance(anchor, Anchor):
        raise AnchorError("reanchor expects an Anchor")
    if not isinstance(text, str):
        raise AnchorError("text must be a string")
    if not 0.0 <= min_similarity <= 1.0:
        raise AnchorError("min_similarity must be between 0 and 1")

    if anchor.matches(text):
        return Rebind(anchor, POSITION)
    if not anchor.exact:
        return _reanchor_insertion(anchor, text, min_similarity)
    return _reanchor_quote(anchor, text, min_similarity)


# -- the quote ladder ----------------------------------------------------


def _reanchor_quote(anchor: Anchor, text: str, min_similarity: float) -> Rebind:
    verbatim = False
    for strategy, spans in (
        (QUOTE, _verbatim_spans(text, anchor.exact, anchor.start)),
        (NORMALIZED, _normalized_spans(text, anchor.exact, anchor.start)),
    ):
        verbatim = verbatim or bool(spans)
        picked = _pick(spans, anchor, text, min_context=MIN_CONTEXT)
        if picked is not None:
            start, end, ambiguous = picked
            return Rebind(anchor_for(text, start, end), strategy, ambiguous)

    picked = _fuzzy(text, anchor, anchor.exact, min_similarity)
    if picked is None:
        return Rebind(None, reason=_orphan_reason(anchor.exact, verbatim, min_similarity))
    start, end, ambiguous = picked
    return Rebind(anchor_for(text, start, end), FUZZY, ambiguous)


def _orphan_reason(quote: str, verbatim: bool, min_similarity: float) -> str:
    """Why nothing was good enough — the two failures read very differently.

    "The sentence is gone" sends a reviewer looking for what replaced it. "The
    sentence is still there but not where this comment was" sends them looking
    for the section that was deleted around it, which is the case this reason
    exists to name: the comment did not lose its text, it lost its place.
    """
    if verbatim:
        return (
            f"quote {_clip(quote)} still occurs in the revised text, but no occurrence "
            f"keeps {MIN_CONTEXT:.2f} of the context this comment was anchored in — "
            "the passage it was made on is gone"
        )
    return (
        f"quote {_clip(quote)} is not in the revised text, and no span reaches "
        f"{min_similarity:.2f} similarity"
    )


def _reanchor_insertion(anchor: Anchor, text: str, min_similarity: float) -> Rebind:
    """Re-anchor a zero-length anchor — an insertion point between two spans.

    There is no quote to match, so the context *is* the quote: the point sits
    where the prefix ends and the suffix begins. An insertion point with no
    context either survives rung 1 or cannot be looked for at all, which is a
    property of the anchor, not a failure of the search.
    """
    joined = anchor.prefix + anchor.suffix
    if not joined:
        return Rebind(
            None,
            reason=(
                "insertion point carries neither quote nor context, and offset "
                f"{anchor.start} is past the end of the revised text"
            ),
        )

    split = len(anchor.prefix)
    near = max(0, anchor.start - split)
    for strategy, spans in (
        (QUOTE, _verbatim_spans(text, joined, near)),
        (NORMALIZED, _normalized_spans(text, joined, near)),
    ):
        points = [(start + split, start + split) for start, _ in spans]
        picked = _pick(points, anchor, text, min_context=MIN_CONTEXT)
        if picked is not None:
            start, _, ambiguous = picked
            return Rebind(anchor_for(text, start, start), strategy, ambiguous)

    picked = _fuzzy(text, anchor, joined, min_similarity)
    if picked is None:
        return Rebind(
            None,
            reason=(
                f"the context around the insertion point ({_clip(joined)}) is not in the "
                f"revised text at {min_similarity:.2f} similarity"
            ),
        )
    start, end, ambiguous = picked
    # Land inside the matched context, in proportion to where the point sat.
    offset = round((end - start) * split / len(joined)) if joined else 0
    point = min(max(start + offset, start), end)
    return Rebind(anchor_for(text, point, point), FUZZY, ambiguous)


# -- candidate spans -----------------------------------------------------


def _occurrences(
    text: str, needle: str, *, near: int = 0, limit: int = MAX_OCCURRENCES
) -> list[int]:
    """Start offsets of ``needle`` in ``text`` — the ``limit`` closest to ``near``.

    The cap is what stops a one-character quote from running away in a long
    document, and it has to stay. What matters is *where* it cuts. Cutting from
    the top means a phrase that repeats past the cap never offers the occurrence
    the anchor actually came from, so the best of a set that does not contain the
    right answer wins, on the ``quote`` rung, with no ambiguity flag — the
    position hint only ever got to break ties among survivors it had no say in
    choosing. Scanning outward from that hint keeps the same bound and puts the
    span it points at first in line.
    """
    if not needle or limit <= 0:
        return []
    forward: list[int] = []
    start = text.find(needle, max(0, near))
    while start != -1 and len(forward) < limit:
        forward.append(start)
        start = text.find(needle, start + 1)
    backward: list[int] = []
    edge = max(0, near)
    while len(backward) < limit:
        # The window ends one character into the needle so an occurrence that
        # overlaps ``edge`` is still reachable — "aa" inside "aaaa" is two spans.
        found = text.rfind(needle, 0, edge + len(needle) - 1)
        if found == -1:
            break
        backward.append(found)
        edge = found
    nearest = sorted(forward + backward, key=lambda at: (abs(at - near), at))[:limit]
    return sorted(nearest)


def _verbatim_spans(text: str, quote: str, near: int = 0) -> list[tuple[int, int]]:
    return [(start, start + len(quote)) for start in _occurrences(text, quote, near=near)]


def _normalize(text: str) -> tuple[str, list[int], list[int]]:
    """Fold ``text`` and return it with a map back to the original offsets.

    Each folded character records the half-open source range it came from, so a
    match found in folded space names an exact span of the original — the caller
    never has to guess where a collapsed run of whitespace began or ended. The
    map is what lets the fold be many-to-one: a run of whitespace and a base
    character with its combining marks both collapse, and both still name the
    exact source span they came from.

    Unicode composition is folded here rather than left to rung 4 because it is
    the same kind of change as the rest of this fold — the glyphs a reader sees
    are identical. A macOS filesystem or an editor can renormalise a file with
    nobody editing it, and calling that ``fuzzy`` would tell a reviewer the
    quoted text had been rewritten when not one character of it changed.
    """
    out: list[str] = []
    starts: list[int] = []
    ends: list[int] = []
    index = 0
    length = len(text)
    while index < length:
        if text[index].isspace():
            run = index
            while run < length and text[run].isspace():
                run += 1
            out.append(" ")
            starts.append(index)
            ends.append(run)
            index = run
            continue
        # A base character owns the combining marks that follow it: composing
        # them separately would leave a mark stranded with no base to sit on.
        # Every combining mark is above U+007F, so the ASCII test is a cheap
        # prefilter that cannot skip one — and it keeps this loop off the
        # unicodedata calls entirely for the documents that have no marks.
        run = index + 1
        while run < length and not text[run].isascii() and unicodedata.combining(text[run]):
            run += 1
        head = text[index]
        chunk = head if run == index + 1 and head.isascii() else unicodedata.normalize(
            "NFC", text[index:run]
        )
        for char in chunk:
            out.append(_FOLD.get(char, char))
            starts.append(index)
            ends.append(run)
        index = run
    return "".join(out), starts, ends


def _normalized_spans(text: str, quote: str, near: int = 0) -> list[tuple[int, int]]:
    folded_text, starts, ends = _normalize(text)
    folded_quote, _, _ = _normalize(quote)
    if not folded_quote:
        return []
    # ``starts`` is non-decreasing, so the hint crosses into folded space by
    # bisection — the cap has to cut around the old position here too.
    return [
        (starts[at], ends[at + len(folded_quote) - 1])
        for at in _occurrences(folded_text, folded_quote, near=bisect_left(starts, near))
    ]


def _candidates(text: str, quote: str, hint: int) -> list[int]:
    """Plausible start offsets for a fuzzy match, nearest to ``hint`` first.

    Seeds are the longest words of the quote: an edited sentence usually keeps
    most of its words, so a word that survives points at the neighbourhood.
    A quote with no word characters falls back to a stride scan, which is worse
    but still bounded.
    """
    seeds = sorted(
        ((match.group(0), match.start()) for match in _WORD.finditer(quote)),
        key=lambda pair: (-len(pair[0]), pair[1]),
    )[:SEED_COUNT]

    starts: set[int] = set()
    for word, offset in seeds:
        # A seed sits ``offset`` into the quote, so the place to look for it is
        # that far past the hint — the cap cuts around there, not around zero.
        for at in _occurrences(text, word, near=max(0, hint + offset)):
            starts.add(max(0, at - offset))
    if not starts:
        stride = max(1, len(quote) // 2)
        starts = set(range(0, max(len(text) - len(quote), 0) + 1, stride))
    ordered = sorted(starts, key=lambda start: (abs(start - hint), start))
    return ordered[:MAX_CANDIDATES]


def _min_block(quote: str) -> int:
    """Shortest matching run that may define the edge of an aligned span.

    Single shared characters are everywhere in prose, so letting one anchor an
    edge stretches the span across whole paragraphs. Requiring a short run
    instead keeps the edges on real agreement without discarding small quotes,
    which have no long runs to offer.
    """
    return max(1, min(4, len(quote) // 8))


def _align(text: str, start: int, quote: str) -> tuple[int, int] | None:
    """Snap ``quote`` onto the text around ``start``, returning the matched span.

    The window is the quote plus a constant pad rather than a fraction of the
    document: alignment is quadratic, so the window has to stop growing with the
    input or a long document turns a re-anchor into a hang.
    """
    low = max(0, start - ALIGN_PAD)
    high = min(len(text), start + len(quote) + ALIGN_PAD)
    window = text[low:high]
    smallest = _min_block(quote)
    blocks = [
        block
        for block in SequenceMatcher(None, quote, window, autojunk=False).get_matching_blocks()
        if block.size >= smallest
    ]
    if not blocks:
        return None
    return low + blocks[0].b, low + blocks[-1].b + blocks[-1].size


def _fuzzy(
    text: str, anchor: Anchor, quote: str, min_similarity: float
) -> tuple[int, int, bool] | None:
    """Best approximate span for ``quote``, or ``None`` if none is good enough."""
    if not quote:
        return None
    prefilter = sorted(
        (
            (
                -_round(_quick_ratio(quote, text[start : start + len(quote)])),
                abs(start - anchor.start),
                start,
            )
            for start in _candidates(text, quote, anchor.start)
        )
    )[:ALIGN_CANDIDATES]

    # Two spans per candidate, because the two regimes fail in opposite ways.
    # Alignment tracks a quote whose length changed but whose words survived;
    # a fixed-width window tracks one that was reworded in place, where the
    # matching runs are too short and scattered for alignment to trust. Scoring
    # both and keeping the better one costs one extra comparison per candidate.
    spans: list[tuple[int, int]] = []
    for _, _, start in prefilter:
        aligned = _align(text, start, quote)
        if aligned is not None and aligned[1] > aligned[0]:
            spans.append(aligned)
        window = (start, min(len(text), start + len(quote)))
        if window[1] > window[0]:
            spans.append(window)

    picked = _pick(
        spans,
        anchor,
        text,
        quote=quote,
        min_similarity=min_similarity,
        min_context=MIN_CONTEXT,
    )
    if picked is None:
        return None
    start, end, ambiguous = picked
    snapped = _snap(text, start, end)
    # Snapping lowers the score slightly by definition — it adds characters the
    # quote does not have. Take it anyway when it still clears the floor: an
    # anchor that reads "0 seconds" because the 6 of "60" did not match scores
    # well and shows badly, and a reviewer judges the span, not the ratio.
    if snapped != (start, end):
        if _round(_ratio(quote, text[snapped[0] : snapped[1]])) >= min_similarity:
            start, end = snapped
    return start, end, ambiguous


def _snap(text: str, start: int, end: int) -> tuple[int, int]:
    """Grow a span outward to word boundaries, by at most :data:`SNAP_CHARS`."""
    low = start
    while (
        0 < low < len(text)
        and start - low < SNAP_CHARS
        and text[low - 1].isalnum()
        and text[low].isalnum()
    ):
        low -= 1
    high = end
    while (
        high < len(text)
        and high - end < SNAP_CHARS
        and text[high].isalnum()
        and text[high - 1].isalnum()
    ):
        high += 1
    return low, high


# -- choosing between spans ----------------------------------------------


def _pick(
    spans: list[tuple[int, int]],
    anchor: Anchor,
    text: str,
    *,
    quote: str | None = None,
    min_similarity: float = 0.0,
    min_context: float = 0.0,
) -> tuple[int, int, bool] | None:
    """Rank candidate spans and return the winner, flagged if it was a tie.

    The order is total — quote similarity, then context similarity, then
    distance from the old position, then the offset itself — so the same inputs
    always yield the same span. A tie on the two similarity scores means the
    position hint alone decided it, and that is reported rather than hidden:
    a silently chosen wrong span is the failure this whole module exists to
    avoid.

    Ranking and vetoing ask different questions of the same context, so they
    read it differently. Ranking wants overall agreement and averages the two
    sides; the veto wants to know whether the span is somewhere this anchor has
    ever been, and one side surviving intact answers that — deleting the
    paragraph above a quote wipes its prefix without moving it anywhere.
    """
    if not spans:
        return None
    scored = []
    for start, end in dict.fromkeys(spans):
        quality = _round(_ratio(quote, text[start:end])) if quote is not None else 1.0
        if quote is not None and quality < min_similarity:
            continue
        # Both readings come off one measurement of the context — comparing the
        # two sides twice would double the only quadratic work on this path.
        parts = _context_parts(text, start, end, anchor)
        if _round(max(parts) if parts else 1.0) < min_context:
            continue
        agreement = sum(parts) / len(parts) if parts else 1.0
        scored.append(
            (-quality, -_round(agreement), abs(start - anchor.start), start, end)
        )
    if not scored:
        return None
    scored.sort()
    best = scored[0]
    ambiguous = len(scored) > 1 and scored[1][:2] == best[:2]
    return best[3], best[4], ambiguous


def _context_parts(text: str, start: int, end: int, anchor: Anchor) -> list[float]:
    """How well each side of a span agrees with the anchor's context.

    The two sides are returned rather than a single number because the caller
    asks two questions of them. *Which candidate is best* is the average — a
    span that agrees on both sides beats one that agrees on one. *Is any
    candidate acceptable at all* is the better side, because a revision that
    moves a quote usually keeps one side of it: deleting the paragraph above
    destroys the prefix and leaves the suffix untouched, and the reverse for a
    deletion below. A different occurrence of the same sentence agrees with
    neither side beyond the generic similarity any two pieces of prose have.

    An anchor with no context at all yields nothing here, and both readings then
    treat it as agreeing with everything — the honest answer, since it carries
    nothing to tell two identical quotes apart. The resulting tie is what raises
    the ambiguity flag.
    """
    parts: list[float] = []
    if anchor.prefix:
        parts.append(_ratio(anchor.prefix, text[max(0, start - len(anchor.prefix)) : start]))
    if anchor.suffix:
        parts.append(_ratio(anchor.suffix, text[end : end + len(anchor.suffix)]))
    return parts


def _ratio(left: str, right: str) -> float:
    if not left and not right:
        return 1.0
    return SequenceMatcher(None, left, right, autojunk=False).ratio()


def _quick_ratio(left: str, right: str) -> float:
    """Linear upper bound on :func:`_ratio`, used to rank before aligning."""
    if not left and not right:
        return 1.0
    return SequenceMatcher(None, left, right, autojunk=False).quick_ratio()


def _round(value: float) -> float:
    return round(value, _PRECISION)


def _clip(text: str, limit: int = 40) -> str:
    return repr(text if len(text) <= limit else text[:limit] + "…")
