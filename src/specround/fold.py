"""Fold — read the ledger, compute the present (G3).

``fold`` is the only place that answers "what is true now": which rounds are
open, which comments are still waiting on someone, which conversations are
over. Nothing else caches that state, so there is no second copy to fall out of
sync with the log.

Three axes run independently and are easy to confuse, so they are named apart:
*disposition* (was this decided? — ``undisposed``), *anchor* (can we still put
it on the document? — ``orphans``), and *thread* (is the conversation over? —
``resolved_threads`` / ``active_threads``). A comment can sit anywhere in that
cube.

**The disposition axis is spelled ``undisposed``, never "unresolved".** It used
to be, and one word away from the ``resolve`` verb is close enough that running
that verb and watching the number not move read as a bug rather than as the
answer to a different question. "Unresolved" now belongs to the thread axis and
to nothing else — the word and the verb agree.

Two properties are load bearing:

* **Deterministic.** The result is a pure function of the record sequence, in
  file order. No clock, no randomness, no filesystem. Timestamps are data, not
  ordering — ``seq`` is the order.
* **It is also the validator.** Every cross-record rule (a comment must name a
  live round, a settled comment stays settled, a closed round must have
  recorded what it left undisposed) is enforced here while folding. The writer
  folds the prospective history before appending, so the same code that reads a
  ledger is the code that refuses a bad append — one oracle, not two.

One spelling did not move: the ``round.close`` record's own ``unresolved``
field. That is bytes already written under ``specround.ledger/v0``, where the
field set is closed and an unknown key is refused outright (format §2), so
renaming it would not be a rename — it would make every ledger that used it
unreadable. It is read here into :attr:`Round.undisposed_at_close`, which is
what the rest of the program sees.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

from specround.anchors import Anchor
from specround.errors import InvariantError
from specround.events import (
    ANCHOR_KINDS,
    COMMENT_ADD,
    COMMENT_KINDS,
    DEFERRED,
    DISPOSITION,
    REPLY,
    ROUND_CLOSE,
    ROUND_OPEN,
    SUGGESTION_ADD,
    TERMINAL_VERDICTS,
    THREAD_KINDS,
    THREAD_RESOLVE,
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
class Resolution:
    """One assertion that a thread is over — or that it is not (G11).

    ``resolved`` is what the event said: ``True`` for ``thread.resolve``,
    ``False`` for ``thread.reopen``. ``note`` carries whichever text the event
    had — an optional note when resolving, the required reason when re-opening.
    """

    id: str
    author: str
    actor: str
    ts: str
    resolved: bool
    note: str = ""


@dataclass
class Anchoring:
    """One attempt to carry a comment's anchor onto a snapshot (G1, H4).

    ``anchor`` is ``None`` when the attempt failed — the comment is orphaned
    against that snapshot and ``reason`` says why. Failure is recorded, never
    silent: an anchor that cannot be found must still be visible to whoever
    owns the comment (G3).
    """

    id: str
    author: str
    ts: str
    base: str
    anchor: Anchor | None = None
    strategy: str | None = None
    ambiguous: bool = False
    reason: str = ""

    @property
    def orphaned(self) -> bool:
        return self.anchor is None


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
    #: Re-anchoring history, oldest first. ``anchor`` above stays as written —
    #: it is where the comment was made, against its round's base.
    anchorings: list[Anchoring] = field(default_factory=list)
    #: Resolve/reopen history for this thread, oldest first (G11).
    resolutions: list[Resolution] = field(default_factory=list)
    #: The reserved additive object, exactly as the record carried it. The fold
    #: does not look inside — preserving it is the whole contract (§2).
    ext: dict[str, Any] | None = None
    #: I12: :attr:`current_anchor` does not hold in the base this comment is
    #: painted on. **The pure fold never sets this** — deciding it means opening
    #: a snapshot, so :class:`~specround.store.ReviewStore` stamps it on the way
    #: out, the same one-layer-out arrangement I7 already uses. ``False`` from a
    #: bare fold therefore means "not checked", not "checked and clean"; every
    #: read that matters goes through a store.
    misplaced: bool = False

    @property
    def disposition(self) -> Disposition | None:
        """The current disposition — the last one recorded."""
        return self.dispositions[-1] if self.dispositions else None

    @property
    def anchoring(self) -> Anchoring | None:
        """The most recent re-anchoring attempt, if there has been one."""
        return self.anchorings[-1] if self.anchorings else None

    @property
    def orphaned(self) -> bool:
        """True when the last attempt failed to place this comment."""
        latest = self.anchoring
        return latest is not None and latest.orphaned

    @property
    def current_anchoring(self) -> Anchoring | None:
        """The attempt that put this comment where it is now, if one did.

        Not the same as :attr:`anchoring`, which is the *latest* attempt and may
        be the orphan that failed. This is the latest that succeeded — the one
        whose ``strategy`` says how the comment got here, and therefore whether
        a person should look at it (§4: ``fuzzy`` means the quoted text was
        rewritten). ``None`` means nothing has moved it and it still sits where
        it was made.
        """
        for attempt in reversed(self.anchorings):
            if attempt.anchor is not None:
                return attempt
        return None

    @property
    def current_anchor(self) -> Anchor | None:
        """Where this comment lives now — the last anchor that was bound.

        An orphan keeps its last good anchor rather than losing it. Orphaning
        is a report that a revision hid the text, not a decision to forget
        where it used to be: a later revision that restores the text can bind
        the comment again from here.
        """
        placed = self.current_anchoring
        return placed.anchor if placed is not None else self.anchor

    @property
    def bound_to(self) -> str | None:
        """The snapshot the latest attempt ran against, orphaned or not."""
        latest = self.anchoring
        return latest.base if latest else None

    @property
    def resolution(self) -> Resolution | None:
        """The assertion in force about this thread — the last one recorded."""
        return self.resolutions[-1] if self.resolutions else None

    @property
    def resolved(self) -> bool:
        """True when this conversation has been closed and not re-opened (G11).

        A third axis, independent of the other two. :attr:`undisposed` asks
        whether anyone decided what to do about the comment; :attr:`orphaned`
        asks whether the tool can still place it on the document; this asks
        whether the discussion is over. A thread can be resolved with no
        disposition (people simply agreed) and settled with the thread still
        open (the fix landed, the argument continues).

        This is the only axis the word *resolved* names. There is deliberately
        no ``unresolved`` property beside it: ``not comment.resolved`` is one
        character longer and cannot be mistaken for the disposition axis, and
        anything still reaching for the old spelling gets an ``AttributeError``
        rather than a boolean that flipped meaning underneath it.
        """
        current = self.resolution
        return current is not None and current.resolved

    @property
    def verdict(self) -> str | None:
        current = self.disposition
        return current.verdict if current else None

    @property
    def settled(self) -> bool:
        """True once a terminal verdict has been recorded."""
        return self.verdict in TERMINAL_VERDICTS

    @property
    def undisposed(self) -> bool:
        """Still owed an answer: never disposed, or explicitly deferred.

        ``deferred`` is the one verdict that does not settle a comment — that is
        the whole point of having it. A deferred comment keeps showing up until
        someone applies, rejects, or answers it.

        Nothing to do with :attr:`resolved`. This one is the disposition axis
        and it is what ``round.close`` has to account for (I6); closing a thread
        never changes it, or resolving would become a way to walk away from an
        undisposed comment quietly. That independence is the reason for the
        name: while this was called ``unresolved`` the two axes shared a word,
        and the count that correctly did not move after a ``resolve`` looked
        like the tool ignoring the command.
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
    #: The comments this round walked away from with no verdict. It reads the
    #: record's ``unresolved`` field, which keeps its v0 spelling on disk.
    undisposed_at_close: list[str] = field(default_factory=list)
    close_note: str = ""
    #: The reserved additive object from ``round.open``, preserved not read.
    ext: dict[str, Any] | None = None

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
    def undisposed(self) -> list[Comment]:
        """Comments still owed an answer, in the order they were made.

        The disposition axis — see :attr:`Comment.undisposed`. Nothing to do
        with :attr:`resolved_threads` or :attr:`active_threads`.
        """
        return [c for c in self.comments.values() if c.undisposed]

    @property
    def orphans(self) -> list[Comment]:
        """Comments whose anchor was not found in the revision it was tried on.

        Separate axis from :attr:`undisposed`: that one is about whether anyone
        answered the comment, this one is about whether the tool can still show
        it where it belongs. A comment can be both, either, or neither.
        """
        return [c for c in self.comments.values() if c.orphaned]

    @property
    def active_threads(self) -> list[Comment]:
        """Conversations still going — the unresolved threads, and the default
        listing (G11).

        This is what "resolved is hidden by default" means at this layer.
        Hiding is a view decision, not a deletion: the records are all still in
        the ledger and :attr:`comments` still holds every one of them.
        """
        return [c for c in self.comments.values() if not c.resolved]

    @property
    def resolved_threads(self) -> list[Comment]:
        """Conversations someone closed — the separate list the toggle shows."""
        return [c for c in self.comments.values() if c.resolved]

    def threads(
        self, round_id: str | None = None, *, include_resolved: bool = False
    ) -> list[Comment]:
        """Threads, newest last, resolved ones left out unless asked for.

        The name says *thread* rather than *comment* because the axis it
        filters on is the thread's: a resolved thread drops out of this list
        while staying exactly where it was in :meth:`comments_in`.
        """
        return [
            c
            for c in self.comments.values()
            if (round_id is None or c.round == round_id)
            and (include_resolved or not c.resolved)
        ]

    def comments_in(self, round_id: str) -> list[Comment]:
        """Every comment in a round, resolved or not — the raw index.

        Deliberately unfiltered. Callers that want the default view ask
        :meth:`threads`; this one is for anybody who has to see all of it.
        """
        return [c for c in self.comments.values() if c.round == round_id]

    def undisposed_in(self, round_id: str) -> list[Comment]:
        """Comments in a round still owed a disposition — resolved or not.

        Unfiltered on purpose: this feeds ``round.close`` (I6), and a resolved
        thread whose comment nobody disposed is still something the close has
        to declare.
        """
        return [c for c in self.comments_in(round_id) if c.undisposed]

    def round_of(self, comment_id: str) -> Round:
        return self.rounds[self.comments[comment_id].round]


def check_position(event_id: str, seq: Any, position: int, *, where: str = "") -> None:
    """Enforce I2 — ``seq`` is the record's own position, nothing else.

    The one implementation of the rule. It used to live twice: once in the
    reader, which raised a schema error, and once here, which raised an
    invariant error — so a hand-reordered ledger produced two different
    diagnoses and two different exit codes depending on which path touched the
    file first. It is an invariant either way, which is what the caller has to
    act on: the file's history is wrong, not one record's shape.
    """
    if seq == position:
        return
    prefix = f"{where}: " if where else ""
    raise InvariantError(
        f"{prefix}record {event_id!r} claims seq {seq} but sits at position {position} — "
        "history was reordered or truncated"
    )


def _comment_or_raise(state: State, target: str, what: str) -> Comment:
    comment = state.comments.get(target)
    if comment is None:
        if target in state.rounds:
            raise InvariantError(
                f"{what} targets {target!r}, which is a round, not a comment"
            )
        if target in state.seen_ids:
            # A reply, a disposition, a re-anchor — a real event, but not
            # something anything can hang off. Saying so beats "unknown", which
            # sends the reader looking for a typo that is not there.
            raise InvariantError(
                f"{what} targets {target!r}, which is an event but not a comment "
                "or suggestion"
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
    check_position(event_id, record["seq"], state.count)
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
            ext=dict(record["ext"]) if "ext" in record else None,
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
            ext=dict(record["ext"]) if "ext" in record else None,
        )

    elif kind == REPLY:
        comment = _comment_or_raise(state, record["target"], f"reply {event_id!r}")
        if comment.resolved:
            # The one asymmetry in an otherwise permissive thread model (I11).
            # Resolved threads are hidden by default (G11), so a reply appended
            # under one lands where nobody looks — a lost answer wearing the
            # shape of a recorded one, which is the failure G3 exists to
            # prevent. Re-opening first costs one line and puts the
            # conversation back where its answer can be read.
            current = comment.resolution
            assert current is not None  # resolved implies a resolution
            raise InvariantError(
                f"comment {comment.id!r} is a resolved thread (closed by {current.id!r}) "
                "— reopen it before replying, or the reply lands in a hidden thread"
            )
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

    elif kind in ANCHOR_KINDS:
        comment = _comment_or_raise(state, record["target"], f"{kind} {event_id!r}")
        if comment.anchor is None:
            raise InvariantError(
                f"{kind} {event_id!r} targets comment {comment.id!r}, which has no anchor "
                "— a comment on the whole document has nothing to re-anchor"
            )
        # No round check on purpose: a comment outlives its round, and the
        # revision that moved its text usually lands after the round closed.
        payload = record.get("anchor")
        comment.anchorings.append(
            Anchoring(
                id=event_id,
                author=record["author"],
                ts=record["ts"],
                base=record["base"],
                anchor=Anchor.from_json(payload) if payload is not None else None,
                strategy=record.get("strategy"),
                ambiguous=bool(record.get("ambiguous", False)),
                reason=record.get("reason", ""),
            )
        )

    elif kind in THREAD_KINDS:
        comment = _comment_or_raise(state, record["target"], f"{kind} {event_id!r}")
        # No round check, and no refusal of a redundant assertion: a thread
        # outlives its round, and re-stating a state the thread is already in
        # is agreement, not contradiction. That is the whole difference from a
        # disposition, where a second verdict would overwrite a decision — here
        # a second resolve just says the same thing twice, and the record of who
        # said it is worth keeping. Idempotence is deliberate (I10): a caller
        # that closes an already-closed thread has made a harmless mistake, and
        # a tool that raised on it would turn that into an incident.
        comment.resolutions.append(
            Resolution(
                id=event_id,
                author=record["author"],
                actor=record["actor"],
                ts=record["ts"],
                resolved=kind == THREAD_RESOLVE,
                note=record.get("note", record.get("reason", "")),
            )
        )

    elif kind == ROUND_CLOSE:
        round_ = _live_round_or_raise(state, record["round"], f"round.close {event_id!r}")
        outstanding = sorted(c.id for c in state.undisposed_in(round_.id))
        # ``unresolved`` is this record's field name at v0 and stays that way on
        # disk; what it holds is the undisposed set (format §4).
        declared = sorted(record.get("unresolved", []))
        if declared != outstanding:
            # Closing over open comments is allowed, hiding them is not: the
            # event has to say which ones it walked away from (G3, loss 0).
            raise InvariantError(
                f"round.close {event_id!r} declares unresolved={declared} but round "
                f"{round_.id!r} has {outstanding} undisposed — the close must record them"
            )
        round_.status = CLOSED
        round_.closed_by = event_id
        round_.closed_ts = record["ts"]
        round_.undisposed_at_close = outstanding
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
