"""Fold — read the ledger, compute the present (G3).

``fold`` is the only place that answers "what is true now": which rounds are
open, which comments are still waiting on someone. Nothing else caches that
state, so there is no second copy to fall out of sync with the log.

Two properties are load bearing:

* **Deterministic.** The result is a pure function of the record sequence, in
  file order. No clock, no randomness, no filesystem. Timestamps are data, not
  ordering — ``seq`` is the order.
* **It is also the validator.** Every cross-record rule (a comment must name a
  live round, a settled comment stays settled, a closed round must have
  recorded what it left unresolved) is enforced here while folding. The writer
  folds the prospective history before appending, so the same code that reads a
  ledger is the code that refuses a bad append — one oracle, not two.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

from specround.anchors import Anchor
from specround.errors import InvariantError
from specround.events import (
    COMMENT_ADD,
    COMMENT_KINDS,
    DEFERRED,
    DISPOSITION,
    REPLY,
    ROUND_CLOSE,
    ROUND_OPEN,
    SUGGESTION_ADD,
    TERMINAL_VERDICTS,
    validate_event,
)

OPEN = "open"
CLOSED = "closed"


@dataclass
class Reply:
    """An answer attached to a comment."""

    id: str
    author: str
    ts: str
    body: str


@dataclass
class Disposition:
    """What was decided about a comment, and why."""

    id: str
    author: str
    ts: str
    verdict: str
    reason: str


@dataclass
class Comment:
    """A comment or a suggestion, with everything that happened to it."""

    id: str
    round: str
    kind: str  # "comment" | "suggestion"
    author: str
    ts: str
    body: str = ""
    patch: str | None = None
    anchor: Anchor | None = None
    replies: list[Reply] = field(default_factory=list)
    dispositions: list[Disposition] = field(default_factory=list)

    @property
    def disposition(self) -> Disposition | None:
        """The current disposition — the last one recorded."""
        return self.dispositions[-1] if self.dispositions else None

    @property
    def verdict(self) -> str | None:
        current = self.disposition
        return current.verdict if current else None

    @property
    def settled(self) -> bool:
        """True once a terminal verdict has been recorded."""
        return self.verdict in TERMINAL_VERDICTS

    @property
    def unresolved(self) -> bool:
        """Still owed an answer: never disposed, or explicitly deferred.

        ``deferred`` is the one verdict that does not settle a comment — that is
        the whole point of having it. A deferred comment keeps showing up until
        someone applies, rejects, or answers it.
        """
        return not self.settled

    @property
    def state(self) -> str:
        """``"open"`` before any disposition, otherwise the current verdict."""
        return self.verdict or OPEN


@dataclass
class Round:
    """One review pass over one document, against a frozen base snapshot."""

    id: str
    doc: str
    base: str
    author: str
    ts: str
    title: str = ""
    status: str = OPEN
    closed_by: str | None = None
    closed_ts: str | None = None
    unresolved_at_close: list[str] = field(default_factory=list)
    close_note: str = ""

    @property
    def open(self) -> bool:
        return self.status == OPEN


@dataclass
class State:
    """The folded present."""

    rounds: dict[str, Round] = field(default_factory=dict)
    comments: dict[str, Comment] = field(default_factory=dict)
    count: int = 0
    #: Every event id seen, so an id can never be reused by any record kind.
    seen_ids: set[str] = field(default_factory=set, repr=False)

    @property
    def open_rounds(self) -> list[Round]:
        return [r for r in self.rounds.values() if r.open]

    @property
    def unresolved(self) -> list[Comment]:
        """Comments still owed an answer, in the order they were made."""
        return [c for c in self.comments.values() if c.unresolved]

    def comments_in(self, round_id: str) -> list[Comment]:
        return [c for c in self.comments.values() if c.round == round_id]

    def unresolved_in(self, round_id: str) -> list[Comment]:
        return [c for c in self.comments_in(round_id) if c.unresolved]

    def round_of(self, comment_id: str) -> Round:
        return self.rounds[self.comments[comment_id].round]


def _comment_or_raise(state: State, target: str, what: str) -> Comment:
    comment = state.comments.get(target)
    if comment is None:
        if target in state.rounds:
            raise InvariantError(
                f"{what} targets {target!r}, which is a round, not a comment"
            )
        raise InvariantError(f"{what} targets unknown comment {target!r}")
    return comment


def _live_round_or_raise(state: State, round_id: str, what: str) -> Round:
    round_ = state.rounds.get(round_id)
    if round_ is None:
        raise InvariantError(f"{what} names unknown round {round_id!r}")
    if not round_.open:
        raise InvariantError(
            f"{what} names round {round_id!r}, which is closed — open a new round instead"
        )
    return round_


def apply_event(state: State, record: Mapping[str, Any]) -> State:
    """Fold one validated record into ``state``, enforcing the history rules."""
    validate_event(record)

    kind = record["type"]
    event_id = record["id"]
    seq = record["seq"]
    if seq != state.count:
        raise InvariantError(
            f"record {event_id!r} claims seq {seq} but is at position {state.count} — "
            "the ledger has been reordered or truncated"
        )
    if event_id in state.seen_ids:
        raise InvariantError(f"duplicate event id {event_id!r}")
    state.seen_ids.add(event_id)

    if kind == ROUND_OPEN:
        state.rounds[event_id] = Round(
            id=event_id,
            doc=record["doc"],
            base=record["base"],
            author=record["author"],
            ts=record["ts"],
            title=record.get("title", ""),
        )

    elif kind in COMMENT_KINDS:
        round_ = _live_round_or_raise(state, record["round"], f"{kind} {event_id!r}")
        anchor = record.get("anchor")
        state.comments[event_id] = Comment(
            id=event_id,
            round=round_.id,
            kind="comment" if kind == COMMENT_ADD else "suggestion",
            author=record["author"],
            ts=record["ts"],
            body=record.get("body", ""),
            patch=record["patch"] if kind == SUGGESTION_ADD else None,
            anchor=Anchor.from_json(anchor) if anchor is not None else None,
        )

    elif kind == REPLY:
        comment = _comment_or_raise(state, record["target"], f"reply {event_id!r}")
        comment.replies.append(
            Reply(
                id=event_id,
                author=record["author"],
                ts=record["ts"],
                body=record["body"],
            )
        )

    elif kind == DISPOSITION:
        comment = _comment_or_raise(state, record["target"], f"disposition {event_id!r}")
        current = comment.disposition
        if current is not None and current.verdict in TERMINAL_VERDICTS:
            raise InvariantError(
                f"comment {comment.id!r} is already settled as {current.verdict!r} "
                f"(by {current.id!r}); a settled comment cannot be re-disposed"
            )
        comment.dispositions.append(
            Disposition(
                id=event_id,
                author=record["author"],
                ts=record["ts"],
                verdict=record["verdict"],
                reason=record["reason"],
            )
        )

    elif kind == ROUND_CLOSE:
        round_ = _live_round_or_raise(state, record["round"], f"round.close {event_id!r}")
        outstanding = sorted(c.id for c in state.unresolved_in(round_.id))
        declared = sorted(record.get("unresolved", []))
        if declared != outstanding:
            # Closing over open comments is allowed, hiding them is not: the
            # event has to say which ones it walked away from (G3, loss 0).
            raise InvariantError(
                f"round.close {event_id!r} declares unresolved={declared} but round "
                f"{round_.id!r} has {outstanding} unresolved — the close must record them"
            )
        round_.status = CLOSED
        round_.closed_by = event_id
        round_.closed_ts = record["ts"]
        round_.unresolved_at_close = outstanding
        round_.close_note = record.get("note", "")

    else:  # pragma: no cover - validate_event already gates the type
        raise InvariantError(f"unhandled event type {kind!r}")

    state.count += 1
    return state


def fold(records: Iterable[Mapping[str, Any]]) -> State:
    """Reduce a record sequence to the current state."""
    state = State()
    for record in records:
        apply_event(state, record)
    return state
