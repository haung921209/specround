"""The round diff — the revision beside the base the round froze (G2).

This is not a git diff and never asks git anything. A round's base is a snapshot
the tool took (§4 ``round.open``), so "what changed" means "what changed since
this round was frozen" — a question that has an answer for an untracked file, a
file outside any repository, and a file with uncommitted edits, which is the
whole point of freezing a base instead of naming a commit (G5, G10).

Rows carry offsets on both sides, because the two sides answer to different
texts. A line the base has can be anchored directly; a line only the revision
has has no offset in the base at all, and turning it into an anchor is the
re-anchor ladder's job (:meth:`~specround.store.ReviewStore.carry_span_into_round`).
Keeping both offsets here is what lets the view hand either case to the right
converter instead of guessing which text a selection came from.
"""

from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher, unified_diff

from specround.markdown import lines_of

__all__ = [
    "ADDED",
    "REMOVED",
    "SAME",
    "Diff",
    "Row",
    "changed_span",
    "diff",
    "unified_patch",
]

#: The line is in both texts.
SAME = "same"
#: The base has it and the revision does not.
REMOVED = "removed"
#: The revision has it and the base does not.
ADDED = "added"


@dataclass(frozen=True)
class Row:
    """One line of the round diff, with wherever it lives in each text.

    ``base_start`` and ``live_start`` are document offsets of the line's first
    character, and either may be ``None`` — a removed line has no place in the
    revision, an added line has none in the base. A row always has at least one.
    """

    op: str
    text: str
    base_line: int | None = None
    live_line: int | None = None
    base_start: int | None = None
    live_start: int | None = None

    @property
    def changed(self) -> bool:
        return self.op != SAME


@dataclass(frozen=True)
class Diff:
    """The base against the revision, line by line."""

    rows: list[Row]
    #: The two texts are byte-identical — the document has not been touched
    #: since the round froze it.
    identical: bool

    @property
    def added(self) -> int:
        return sum(1 for row in self.rows if row.op == ADDED)

    @property
    def removed(self) -> int:
        return sum(1 for row in self.rows if row.op == REMOVED)

    @property
    def lines_changed(self) -> bool:
        return any(row.changed for row in self.rows)

    @property
    def only_terminator(self) -> bool:
        """The texts differ, but no line does — a trailing break came or went.

        Worth its own answer rather than reporting "no changes" over a diff of a
        document that did change. Snapshots keep the bytes they were given,
        including the final newline (§5), so this is a real difference that a
        line-oriented view cannot show.
        """
        return not self.identical and not self.lines_changed


def diff(base: str, live: str) -> Diff:
    """Compare the frozen base against the document as it is now."""
    base_lines = lines_of(base)
    live_lines = lines_of(live)
    matcher = SequenceMatcher(
        None, [p.text for p in base_lines], [p.text for p in live_lines], autojunk=False
    )
    rows: list[Row] = []
    for op, base_low, base_high, live_low, live_high in matcher.get_opcodes():
        if op == "equal":
            for step in range(base_high - base_low):
                left = base_lines[base_low + step]
                right = live_lines[live_low + step]
                rows.append(
                    Row(
                        op=SAME,
                        text=left.text,
                        base_line=base_low + step + 1,
                        live_line=live_low + step + 1,
                        base_start=left.start,
                        live_start=right.start,
                    )
                )
            continue
        # A replacement reads as the removal and the addition it is: the two
        # sides are different text, and the view lets a reviewer comment on
        # either one.
        for index in range(base_low, base_high):
            piece = base_lines[index]
            rows.append(
                Row(op=REMOVED, text=piece.text, base_line=index + 1, base_start=piece.start)
            )
        for index in range(live_low, live_high):
            piece = live_lines[index]
            rows.append(
                Row(op=ADDED, text=piece.text, live_line=index + 1, live_start=piece.start)
            )
    return Diff(rows=rows, identical=base == live)


def unified_patch(base: str, proposed: str, *, label: str = "document", context: int = 3) -> str:
    """A unified diff of an edit, as the body of a ``suggestion.add``.

    The patch is the substance of a suggestion (§4), so it is stored as text and
    never applied here — applying one is a disposition somebody records, and
    whether a patch may still be applied after its anchor moved is H8, still
    open.
    """
    return "".join(
        unified_diff(
            base.splitlines(keepends=True),
            proposed.splitlines(keepends=True),
            fromfile=f"a/{label}",
            tofile=f"b/{label}",
            n=context,
        )
    )


def changed_span(base: str, proposed: str, *, snap: bool = True) -> tuple[int, int] | None:
    """The span of ``base`` an edit touched, or ``None`` when nothing changed.

    Computed by trimming the common prefix and suffix rather than by aligning
    the two texts: the answer is the same, it is linear instead of quadratic on
    a whole document, and it is exact at both ends.

    ``snap`` widens a non-empty span to whole lines, because a patch is
    line-oriented and an anchor cutting a word in half reads as an error in the
    view. An **empty** span is left alone — that is an insertion point between
    two lines (§5, ``exact`` may be empty), and widening it would turn "add a
    line here" into "rewrite the line above", which is a different claim.
    """
    if base == proposed:
        return None
    head = 0
    limit = min(len(base), len(proposed))
    while head < limit and base[head] == proposed[head]:
        head += 1
    tail = 0
    while (
        tail < len(base) - head
        and tail < len(proposed) - head
        and base[len(base) - 1 - tail] == proposed[len(proposed) - 1 - tail]
    ):
        tail += 1
    start, end = head, len(base) - tail
    if snap and end > start:
        start = base.rfind("\n", 0, start) + 1
        break_at = base.find("\n", end)
        end = len(base) if break_at == -1 else break_at
    return start, end
