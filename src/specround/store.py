"""The front door — a review store for a document (G2, G5, G7).

A store is one directory holding both halves of the history: ``ledger.jsonl``
(what happened) and ``objects/`` (the frozen document snapshots rounds are
measured against), plus an ``origin`` line saying what it was made for.

By default that directory is **not** next to the document — it is a central
store under the user's data directory, keyed by the document's absolute path
(see :mod:`specround.locations` for the why and the opt-ins). Records still name
their document with a path relative to the store's origin, so a store that does
live in a working tree keeps working after the folder is moved or cloned.

Everything here is filesystem work. No git, no server, no network — a document
that is untracked, or that lives outside any repository, gets the full loop.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from specround.anchors import Anchor, anchor_for, anchor_for_quote
from specround.critic import COMMENT, Annotation, Skipped, parse
from specround.diffs import unified_patch
from specround.errors import AnchorError, InvariantError, SpecroundError
from specround.events import (
    ANCHOR_ORPHAN,
    ANCHOR_REANCHOR,
    COMMENT_ADD,
    DISPOSITION,
    REPLY,
    ROUND_CLOSE,
    ROUND_OPEN,
    SUGGESTION_ADD,
    THREAD_REOPEN,
    THREAD_RESOLVE,
)
from specround.fold import Comment, Round, State
from specround.ledger import Clock, Ledger
from specround.locations import (
    DIRECTORY,
    DOCUMENT,
    ORIGIN_FILENAME,
    STORE_DIRNAME,
    Origin,
    canonical_path,
    read_origin,
    resolve_location,
    write_origin,
)
from specround.reanchor import MIN_SIMILARITY, POSITION, Rebind, reanchor
from specround.snapshots import SnapshotStore

LEDGER_FILENAME = "ledger.jsonl"

#: Which text a harvested anchor was cut from, when it had to be carried into the
#: round's base. Named for the ``ext`` provenance record — the web view's
#: ``base``/``revision`` do not cover it, because the harvest reads the document
#: *minus its annotation syntax*, which is neither of those two strings.
CLEAN = "clean"

#: Suffix of the temporary file a harvest writes before replacing the document.
_HARVEST_TEMP = ".specround-harvest"

__all__ = [
    "CLEAN",
    "LEDGER_FILENAME",
    "STORE_DIRNAME",
    "HarvestReport",
    "Placement",
    "ReanchorReport",
    "ReviewStore",
]


@dataclass(frozen=True)
class ReanchorReport:
    """What one pass over a revised document did to the comments on it.

    ``unchanged`` is the quiet majority and gets no ledger event: a comment
    whose anchor still verifies has nothing to record, and writing "still
    fine" on every revision would bury the entries that matter under noise.

    The test for that is the comment's state, not the matcher's rung. An orphan
    whose text came back to the very offset it left from also matches on rung 1,
    and there "nothing changed" is only true of the anchor — the comment went
    from unplaceable to placed, which is the change worth a line. It lands in
    ``rebound``.
    """

    #: Snapshot of the revised document this pass ran against.
    base: str
    rebound: list[str] = field(default_factory=list)
    orphaned: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    #: Comments already processed against this snapshot by an earlier pass.
    skipped: list[str] = field(default_factory=list)
    #: Rebound comments where more than one span fit equally well (subset of
    #: ``rebound``) — a human should look at these before trusting the move.
    ambiguous: list[str] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        """True when this pass appended anything."""
        return bool(self.rebound or self.orphaned)


@dataclass(frozen=True)
class Placement:
    """One inline annotation, and where it landed in the round's base.

    ``strategy`` is ``None`` in the ordinary case: the reviewer typed the
    markers into the document the round froze, so removing them restores that
    exact text and the span needs no carrying. It names a rung of the ladder
    when the document had drifted as well, which is the only case where a
    harvested anchor is not an exact cut of the base.
    """

    annotation: Annotation
    anchor: Anchor
    strategy: str | None = None
    ambiguous: bool = False
    #: Id of the appended event, or ``None`` on a dry run.
    event: str | None = None

    @property
    def carried(self) -> bool:
        """True when the ladder moved this span rather than the offsets holding."""
        return self.strategy is not None and self.strategy != POSITION

    def landed(self, event: str) -> "Placement":
        """The same placement, now recorded."""
        return Placement(
            annotation=self.annotation,
            anchor=self.anchor,
            strategy=self.strategy,
            ambiguous=self.ambiguous,
            event=event,
        )


@dataclass(frozen=True)
class HarvestReport:
    """What one pass over an annotated document found, and whether it was kept.

    A dry run and an applied run compute the same report — the placements, the
    refusals, and the text that would be written are all decided before anything
    is appended. ``applied`` is what separates them, and it is the only thing
    that differs besides the event ids.
    """

    #: The round the annotations were recorded against.
    round: str
    #: The snapshot that round froze — the text the anchors verify in.
    base: str
    #: The document with every harvested marker removed.
    clean: str
    #: True when ``clean`` differs from what is on disk, i.e. there is a rewrite
    #: to do. False when the markers were all skipped, or there were none.
    rewrite: bool
    #: False for a dry run: nothing was appended and the file was not touched.
    applied: bool
    placements: list[Placement] = field(default_factory=list)
    #: Markers left in the document — see :class:`~specround.critic.Skipped`.
    skipped: list[Skipped] = field(default_factory=list)

    @property
    def comments(self) -> list[Placement]:
        return [p for p in self.placements if p.annotation.kind == COMMENT]

    @property
    def suggestions(self) -> list[Placement]:
        return [p for p in self.placements if p.annotation.suggestion]

    @property
    def found(self) -> bool:
        """True when there was anything to harvest."""
        return bool(self.placements)

    @property
    def events(self) -> list[str]:
        return [p.event for p in self.placements if p.event is not None]


class ReviewStore:
    """Rounds, comments, and dispositions for what one store directory serves."""

    def __init__(
        self,
        root: Path,
        *,
        origin: Origin | None = None,
        clock: Clock | None = None,
    ) -> None:
        self.root = canonical_path(root)
        # A store that already exists says what it serves; only a fresh one is
        # assumed to serve its parent folder, which is the in-tree layout.
        if origin is None:
            origin = read_origin(self.root) or Origin(DIRECTORY, self.root.parent)
        self.origin = origin
        self.ledger = Ledger(self.root / LEDGER_FILENAME, clock=clock)
        self.snapshots = SnapshotStore(self.root)
        #: Snapshot texts already read, by reference. Objects are immutable, so
        #: this only ever remembers facts — see :meth:`_snapshot_text`.
        self._texts: dict[str, str] = {}

    @classmethod
    def for_document(
        cls,
        doc: Path,
        *,
        store: Path | None = None,
        base: Path | None = None,
        clock: Clock | None = None,
    ) -> "ReviewStore":
        """The store that owns ``doc``, wherever the resolution rules put it.

        ``store`` names a directory outright and wins over any configuration;
        ``base`` says what its document keys count from when the default (the
        store's parent) is wrong. With neither, :func:`resolve_location` decides.
        """
        location = resolve_location(doc, store=store, base=base)
        return cls(location.root, origin=location.origin, clock=clock)

    @classmethod
    def at(cls, directory: Path, *, clock: Clock | None = None) -> "ReviewStore":
        """The in-tree store for a folder of documents — ``<directory>/.specround``."""
        return cls(canonical_path(directory) / STORE_DIRNAME, clock=clock)

    @classmethod
    def open(cls, root: Path, *, clock: Clock | None = None) -> "ReviewStore":
        """Open an existing store by its directory, reading what it serves.

        This is the way back from a central store, whose name is a digest: the
        directory itself is the only thing a caller has, and ``origin`` is the
        only thing that turns it back into a document.
        """
        root = canonical_path(root)
        origin = read_origin(root)
        if origin is None:
            raise SpecroundError(
                f"{root} is not a specround store: no {ORIGIN_FILENAME} record"
            )
        return cls(root, origin=origin, clock=clock)

    @property
    def base_dir(self) -> Path:
        """The folder the store serves — document keys are relative to it."""
        return self.origin.base_dir

    # -- documents -------------------------------------------------------

    def doc_key(self, doc: Path) -> str:
        """The key a record uses to name ``doc``: a relative POSIX path.

        Keys are relative so a ledger stays valid when the folder is moved,
        renamed, or cloned somewhere else.
        """
        resolved = canonical_path(doc)
        if self.origin.kind == DOCUMENT and resolved != self.origin.path:
            raise SpecroundError(
                f"{resolved} is not the document this store holds ({self.origin.path}) — "
                "a store keyed by document path carries one document's history"
            )
        try:
            relative = resolved.relative_to(self.base_dir)
        except ValueError as exc:
            raise SpecroundError(
                f"{resolved} is outside {self.base_dir} — it belongs to a different store"
            ) from exc
        return relative.as_posix()

    def doc_path(self, key: str) -> Path:
        return self.base_dir / key

    # -- state -----------------------------------------------------------

    def fold(self) -> State:
        """The current state — the records, plus the one invariant they cannot settle.

        Everything a ledger can contradict on its own is settled while folding,
        and that keeps :func:`~specround.fold.fold` the pure function §8 specifies:
        same lines in, same state out, no clock and no filesystem. I7 is the one
        rule that does not fit, because deciding whether an anchor agrees with
        its snapshot means opening the snapshot.

        So it is enforced here instead — one layer out, in the only object that
        has the objects. §6's "the reading code is the writing gate" is what this
        preserves: :meth:`_check_anchor` below is the single implementation, and
        both the writer and every read through a store go through it. A line
        someone typed by hand meets the same oracle as one the API appended, and
        there is still only one copy of the rule to drift.
        """
        state = self.ledger.state()
        self._verify_anchors(state)
        return state

    def _snapshot_text(self, base: str) -> str:
        """The text of a snapshot, remembered for the life of this store object.

        Objects are content addressed and immutable, so this caches a fact, not
        state — the thing §8 says not to cache is the folded present, and that is
        still recomputed every time. Without it a fold would re-read and re-hash
        the same base once per anchored comment, and appending is a fold, so the
        cost would land on every write. It grows with the number of distinct
        snapshots a caller touches, which is revisions, not comments.
        """
        cached = self._texts.get(base)
        if cached is None:
            cached = self.snapshots.get_text(base)
            self._texts[base] = cached
        return cached

    def _check_anchor(self, anchor: Anchor, base: str, what: str) -> None:
        """I7: this anchor agrees with the snapshot it names, or the history is wrong.

        The two failures are kept apart because a caller does different things
        about them. An anchor that does not hold in a snapshot the store *can*
        open is a claim the recorded history cannot support — that is the
        invariant, and it reads as one. A base the store cannot open at all is
        the object store failing to answer, which is not the ledger's fault and
        keeps :class:`~specround.errors.SnapshotError`.
        """
        try:
            anchor.verify(self._snapshot_text(base))
        except AnchorError as exc:
            raise InvariantError(f"I7: {what} does not hold in snapshot {base}: {exc}") from exc

    def _verify_anchors(self, state: State) -> None:
        for comment in state.comments.values():
            if comment.anchor is not None:
                self._check_anchor(
                    comment.anchor,
                    state.rounds[comment.round].base,
                    f"the anchor on {comment.id!r}",
                )
            for attempt in comment.anchorings:
                # An orphan names a base and carries no anchor: there is nothing
                # to agree, which is the point of recording it.
                if attempt.anchor is not None:
                    self._check_anchor(
                        attempt.anchor, attempt.base, f"the anchor on {attempt.id!r}"
                    )

    def round_base(self, round_id: str) -> str:
        """The snapshot reference this round froze."""
        round_ = self.fold().rounds.get(round_id)
        if round_ is None:
            raise InvariantError(f"unknown round {round_id!r}")
        return round_.base

    def base_text(self, round_id: str) -> str:
        """The document as this round froze it."""
        return self._snapshot_text(self.round_base(round_id))

    def anchor_in_round(self, round_id: str, quote: str, *, occurrence: int = 0) -> Anchor:
        """Build an anchor by quoting the round's base snapshot."""
        return anchor_for_quote(self.base_text(round_id), quote, occurrence=occurrence)

    def anchor_span_in_round(self, round_id: str, start: int, end: int) -> Anchor:
        """Build an anchor from character offsets into the round's base.

        The offsets form a view reports back. A quote is what a shell caller has
        (there is nothing to point at in a terminal); a span is what a selection
        in a browser already is, and going through a quote would reintroduce the
        ambiguity the offsets had already settled.
        """
        return anchor_for(self.base_text(round_id), start, end)

    def carry_span_into_round(
        self, round_id: str, revised: str, start: int, end: int
    ) -> Rebind:
        """Where a span of a *revised* document sits in this round's base.

        The diff view shows the revision beside the frozen base, so a reviewer
        can select a line the base does not have. The comment still has to
        anchor in the round's base (I7) — that is the text this round is a review
        of — so the span is cut from the revision and carried backwards by the
        same ladder that carries comments forwards across an edit (§5.1). The
        direction is new; the machine is not, which is what SPEC §3 means by the
        re-anchor machine doubling as the diff-line-to-anchor conversion.

        Returns the :class:`~specround.reanchor.Rebind` as it comes: an anchor
        when a rung placed the text, and an orphan with a reason when the
        revision's text has no home in the base — the honest answer for a line
        the revision added. Nothing is guessed onto a neighbouring span, here
        least of all, because the caller is about to write a comment on it.
        """
        return reanchor(anchor_for(revised, start, end), self.base_text(round_id))

    # -- writing ---------------------------------------------------------

    def _append(self, record: Mapping[str, Any]) -> str:
        write_origin(self.root, self.origin)
        return self.ledger.append(record)["id"]

    def open_round(
        self,
        doc: Path,
        *,
        author: str,
        title: str | None = None,
        ext: Mapping[str, Any] | None = None,
    ) -> str:
        """Freeze ``doc`` as a new round's base and record the round.

        The snapshot is the round's base, not a commit: nothing is staged and
        nothing is committed to open a review (G10).
        """
        path = canonical_path(doc)
        if not path.is_file():
            raise SpecroundError(f"cannot open a round on {path}: not a file")
        key = self.doc_key(path)
        # Leave the breadcrumb before the first object, so a store never holds
        # history it cannot name the owner of.
        write_origin(self.root, self.origin)
        base = self.snapshots.put_file(path)
        record: dict[str, Any] = {
            "type": ROUND_OPEN,
            "author": author,
            "doc": key,
            "base": base,
        }
        if title:
            record["title"] = title
        if ext:
            record["ext"] = dict(ext)
        return self._append(record)

    def _verify_anchor(self, round_id: str, anchor: Anchor | Mapping[str, Any] | None) -> dict[str, Any] | None:
        """Check an anchor against the round's base before it becomes history.

        An anchor is verified where it was made — against the snapshot this
        round froze — because that is the text the reviewer was reading. The live
        document may have moved on; carrying a comment across a revision is
        re-anchoring (H4) and is not this function's job.

        The check itself is :meth:`_check_anchor`, the same one the read path
        runs, down to the exception it raises. Two paths through one rule is the
        arrangement §6 asks for; two exception classes for one condition is how
        that arrangement quietly stops being true, because a caller then has to
        learn which side of the store it is standing on to know what to catch.
        """
        if anchor is None:
            return None
        resolved = anchor if isinstance(anchor, Anchor) else Anchor.from_json(anchor)
        self._check_anchor(
            resolved, self.round_base(round_id), f"the anchor given for round {round_id!r}"
        )
        return resolved.to_json()

    def add_comment(
        self,
        round_id: str,
        *,
        author: str,
        body: str,
        anchor: Anchor | Mapping[str, Any] | None = None,
        ext: Mapping[str, Any] | None = None,
    ) -> str:
        """Record a comment, optionally anchored to a span of the round's base."""
        record: dict[str, Any] = {
            "type": COMMENT_ADD,
            "author": author,
            "round": round_id,
            "body": body,
        }
        anchor_json = self._verify_anchor(round_id, anchor)
        if anchor_json is not None:
            record["anchor"] = anchor_json
        if ext:
            record["ext"] = dict(ext)
        return self._append(record)

    def add_suggestion(
        self,
        round_id: str,
        *,
        author: str,
        patch: str,
        body: str = "",
        anchor: Anchor | Mapping[str, Any] | None = None,
        ext: Mapping[str, Any] | None = None,
    ) -> str:
        """Record a suggestion — a comment whose substance is a patch (G8)."""
        record: dict[str, Any] = {
            "type": SUGGESTION_ADD,
            "author": author,
            "round": round_id,
            "patch": patch,
        }
        if body:
            record["body"] = body
        anchor_json = self._verify_anchor(round_id, anchor)
        if anchor_json is not None:
            record["anchor"] = anchor_json
        if ext:
            record["ext"] = dict(ext)
        return self._append(record)

    def reply(self, target: str, *, author: str, body: str) -> str:
        """Answer a comment or suggestion. Replies are flat — targets are comments."""
        return self._append(
            {"type": REPLY, "author": author, "target": target, "body": body}
        )

    def dispose(self, target: str, *, author: str, verdict: str, reason: str) -> str:
        """Settle a comment: applied, rejected, answered, or deferred — with a reason.

        ``deferred`` is the only verdict that leaves a comment outstanding, so a
        deferred comment can be disposed again later; the other three are final.
        """
        return self._append(
            {
                "type": DISPOSITION,
                "author": author,
                "target": target,
                "verdict": verdict,
                "reason": reason,
            }
        )

    # -- threads ---------------------------------------------------------

    def resolve(
        self, target: str, *, author: str, actor: str, note: str | None = None
    ) -> str | None:
        """Close a thread — this conversation is over (G11).

        Returns the id of the appended event, or ``None`` when the thread was
        already resolved and nothing was written. Closing a closed thread is a
        no-op rather than an error: the caller wanted it closed and it is
        closed. Writing the redundant line instead would put "still fine" noise
        in the log, the same reason an unchanged anchor records nothing.

        Independent of :meth:`dispose`. Resolving does not settle the comment,
        and a resolved thread whose comment nobody disposed still has to be
        declared by ``round.close``.
        """
        return self._assert_thread(
            THREAD_RESOLVE, target, author=author, actor=actor, text=note, resolved=True
        )

    def reopen(self, target: str, *, author: str, actor: str, reason: str) -> str | None:
        """Re-open a thread that was closed too early.

        Returns ``None`` when the thread was already open. ``reason`` is
        required — re-opening overturns a decision that is already in the log,
        and everything in this format that overturns or refuses something says
        why.
        """
        return self._assert_thread(
            THREAD_REOPEN, target, author=author, actor=actor, text=reason, resolved=False
        )

    def _assert_thread(
        self,
        kind: str,
        target: str,
        *,
        author: str,
        actor: str,
        text: str | None,
        resolved: bool,
    ) -> str | None:
        comment = self.fold().comments.get(target)
        if comment is not None and comment.resolved == resolved:
            return None
        record: dict[str, Any] = {
            "type": kind,
            "author": author,
            "actor": actor,
            "target": target,
        }
        if kind == THREAD_REOPEN:
            record["reason"] = text or ""
        elif text:
            record["note"] = text
        # An unknown target falls through to the append, where folding the
        # prospective history refuses it (I8) — one oracle, not a second copy of
        # the rule here.
        return self._append(record)

    def close_round(
        self,
        round_id: str,
        *,
        author: str,
        allow_unresolved: bool = False,
        note: str | None = None,
    ) -> str:
        """Close a round, recording anything it leaves unresolved.

        Refusing by default is the gate: walking away from open comments has to
        be a decision someone typed, not the shape of a quiet exit. The record
        carries the list either way, so the ledger never loses them (G3).
        """
        state = self.fold()
        round_ = state.rounds.get(round_id)
        if round_ is None:
            raise InvariantError(f"unknown round {round_id!r}")
        outstanding = sorted(c.id for c in state.unresolved_in(round_id))
        if outstanding and not allow_unresolved:
            raise InvariantError(
                f"round {round_id!r} has {len(outstanding)} unresolved comment(s): "
                f"{', '.join(outstanding)} — dispose them, or close with "
                "allow_unresolved=True to record that they were left open"
            )
        record: dict[str, Any] = {
            "type": ROUND_CLOSE,
            "author": author,
            "round": round_id,
        }
        if outstanding:
            record["unresolved"] = outstanding
        if note:
            record["note"] = note
        return self._append(record)

    # -- re-anchoring ----------------------------------------------------

    def reanchor_document(
        self,
        doc: Path,
        *,
        author: str,
        min_similarity: float = MIN_SIMILARITY,
    ) -> ReanchorReport:
        """Carry every anchored comment on ``doc`` onto the document as it is now.

        This is G1 doing its work: the document was revised, and each comment
        either follows its text to the new place or is reported orphaned. The
        original records are never touched — a move appends
        ``anchor.reanchor``, a loss appends ``anchor.orphan``, and the history
        of both stays readable in order (G3, append-only).

        Running it twice in a row is a no-op. A comment that moved now verifies
        where it landed, and a comment already processed against this exact
        snapshot is skipped, so a second pass has nothing left to say.
        """
        path = canonical_path(doc)
        if not path.is_file():
            raise SpecroundError(f"cannot re-anchor {path}: not a file")
        key = self.doc_key(path)
        base = self.snapshots.put_file(path)
        text = self._snapshot_text(base)

        state = self.fold()
        report = ReanchorReport(base=base)
        for comment in self._anchored_comments(state, key):
            if comment.bound_to == base:
                report.skipped.append(comment.id)
                continue
            result = reanchor(comment.current_anchor, text, min_similarity=min_similarity)
            if result.strategy == POSITION and not comment.orphaned:
                report.unchanged.append(comment.id)
                continue
            if result.found:
                # Never append an anchor that does not hold — same rule, same call.
                self._check_anchor(result.anchor, base, "the re-anchored span")
                record: dict[str, Any] = {
                    "type": ANCHOR_REANCHOR,
                    "author": author,
                    "target": comment.id,
                    "base": base,
                    "anchor": result.anchor.to_json(),
                    "strategy": result.strategy,
                }
                if result.ambiguous:
                    record["ambiguous"] = True
                    report.ambiguous.append(comment.id)
                self._append(record)
                report.rebound.append(comment.id)
            else:
                self._append(
                    {
                        "type": ANCHOR_ORPHAN,
                        "author": author,
                        "target": comment.id,
                        "base": base,
                        "reason": result.reason,
                    }
                )
                report.orphaned.append(comment.id)
        return report

    def _anchored_comments(self, state: State, key: str) -> list[Comment]:
        """Comments on one document that have somewhere to be re-anchored."""
        return [
            comment
            for comment in state.comments.values()
            if comment.anchor is not None and state.rounds[comment.round].doc == key
        ]

    # -- inline annotations ----------------------------------------------

    def harvest_document(
        self,
        doc: Path,
        round_id: str,
        *,
        author: str,
        apply: bool = False,
        min_similarity: float = MIN_SIMILARITY,
    ) -> HarvestReport:
        """Read the CriticMarkup markers out of ``doc`` and record them (G6).

        Comments become ``comment.add``, edits become ``suggestion.add``, and the
        document is rewritten without the markers — which is the order the two
        halves have to happen in. **The anchors count in the text the markers are
        gone from**, because that is the text the file will hold and the text the
        round's base is: leaving a marker in would push every offset after it.

        With ``apply=False`` (the default) nothing is appended and the file is not
        touched. The report is otherwise identical, so a preview cannot promise
        something the applied run would refuse — including the refusals, which
        are raised on the dry run too.

        The append comes **before** the rewrite. If the process dies between
        them, the annotations are in the ledger *and* still in the file: a second
        harvest would record them twice, which a reader can see. The other order
        risks losing them from both, and a lost comment is the failure this whole
        format is built against (G3).
        """
        path = canonical_path(doc)
        if not path.is_file():
            raise SpecroundError(f"cannot harvest {path}: not a file")
        key = self.doc_key(path)
        round_ = self.fold().rounds.get(round_id)
        if round_ is None or round_.doc != key:
            raise InvariantError(f"no round {round_id!r} on {key}")
        text = _document_text(path)
        harvest = parse(text)
        base = self._snapshot_text(round_.base)
        placements = [
            self._place(round_, harvest.clean, base, text, annotation, min_similarity)
            for annotation in harvest.annotations
        ]
        report = HarvestReport(
            round=round_.id,
            base=round_.base,
            clean=harvest.clean,
            rewrite=harvest.clean != text,
            applied=False,
            placements=placements,
            skipped=harvest.skipped,
        )
        if not apply:
            return report
        recorded = [
            placement.landed(self._record(round_, key, harvest.clean, placement, author))
            for placement in placements
        ]
        if report.rewrite:
            _write_document(path, harvest.clean)
        return HarvestReport(
            round=report.round,
            base=report.base,
            clean=report.clean,
            rewrite=report.rewrite,
            applied=True,
            placements=recorded,
            skipped=report.skipped,
        )

    def _place(
        self,
        round_: Round,
        clean: str,
        base: str,
        text: str,
        annotation: Annotation,
        min_similarity: float,
    ) -> Placement:
        """Where an annotation's span sits in the round's base.

        Two of the three answers are exact, and they are the two workflows people
        actually have:

        * **Annotated after the round opened.** Removing the markers restores the
          text the round froze, so the clean offsets *are* base offsets.
        * **Annotated before the round opened.** The base still holds the
          markers, so the span is inside one of them — three characters right of
          its opener (:attr:`~specround.critic.Annotation.source_span`). Also
          exact, and deliberately not a search: the offsets are known, and
          matching for something you can compute is how a tool ends up guessing.

        Only genuine drift — prose that moved as well — reaches the ladder, run
        backwards the way the diff view already runs it (§5.1). No second matcher.

        A span the ladder cannot place refuses the whole harvest. The batch is
        atomic because the clean text has to be both the anchor basis *and* the
        text written to disk: leaving one marker behind would shift every offset
        after it, so "harvest the rest" is not a smaller version of this
        operation. The exit is in the message and it works — re-opening the round
        on the annotated document lands in the exact second case above.
        """
        span = (annotation.start, annotation.end)
        if clean == base:
            return Placement(annotation=annotation, anchor=anchor_for(base, *span))
        if text == base:
            start, end = annotation.source_span
            if end <= len(base) and base[start:end] == annotation.removed:
                return Placement(annotation=annotation, anchor=anchor_for(base, start, end))
            # The arithmetic disagrees with the bytes. That should not happen, and
            # falling through to the ladder is the safe way to be wrong about it.
        rebind = reanchor(anchor_for(clean, *span), base, min_similarity=min_similarity)
        if rebind.orphaned:
            raise InvariantError(
                f"the {annotation.kind} on line {annotation.source_line} has no place in "
                f"the base round {round_.id} froze ({rebind.reason}) — the document has "
                "moved on beyond its markers, so close this round and open a new one on "
                "the document as it is now, or take that marker off text the round cannot see"
            )
        assert rebind.anchor is not None  # found, by the branch above
        return Placement(
            annotation=annotation,
            anchor=rebind.anchor,
            strategy=rebind.strategy,
            ambiguous=rebind.ambiguous,
        )

    def _record(
        self, round_: Round, key: str, clean: str, placement: Placement, author: str
    ) -> str:
        """Append the event one placement stands for.

        A carried anchor takes an ``ext`` note saying which rung placed it, for
        the reason the view records the same thing (§2): without it a comment the
        fuzzy rung moved reads exactly like one cut straight out of the base, and
        §4 is explicit that the first is worth a person's time.
        """
        annotation = placement.annotation
        ext: dict[str, Any] | None = None
        if placement.carried:
            ext = {
                "harvest": {
                    "space": CLEAN,
                    "strategy": placement.strategy,
                    "ambiguous": placement.ambiguous,
                }
            }
        if annotation.kind == COMMENT:
            return self.add_comment(
                round_.id,
                author=author,
                body=annotation.body,
                anchor=placement.anchor,
                ext=ext,
            )
        return self.add_suggestion(
            round_.id,
            author=author,
            patch=unified_patch(clean, annotation.proposed(clean), label=key),
            anchor=placement.anchor,
            ext=ext,
        )

    def orphans(self, doc: Path | None = None) -> list[Comment]:
        """Comments whose text the latest re-anchor pass could not find."""
        state = self.fold()
        key = self.doc_key(doc) if doc is not None else None
        return [
            comment
            for comment in state.orphans
            if key is None or state.rounds[comment.round].doc == key
        ]

    # -- convenience -----------------------------------------------------

    def open_rounds(self) -> list[Round]:
        return self.fold().open_rounds

    def latest_round(self, doc: Path | None = None) -> Round | None:
        """The most recently opened round, optionally for one document."""
        key = self.doc_key(doc) if doc is not None else None
        rounds = [r for r in self.fold().rounds.values() if key is None or r.doc == key]
        return rounds[-1] if rounds else None


# -- document text -------------------------------------------------------
#
# Bytes both ways, decoded and encoded here rather than by ``read_text``. A
# snapshot keeps the bytes it was handed (§5), so ``\r\n`` survives into the base
# text — and text-mode IO would translate it on the way in *and* on the way out.
# Either translation makes the clean text a different string from the base for a
# CRLF document, which is the quiet kind of wrong: the anchors would be off by
# one character per line and nothing would fail.


def _document_text(path: Path) -> str:
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise SpecroundError(f"cannot read {path}: {exc}") from exc
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SpecroundError(f"{path} is not valid UTF-8: {exc}") from exc


def _write_document(path: Path, text: str) -> None:
    """Replace ``path`` with ``text``, via a temporary file in the same folder.

    A rewrite of somebody's document is the one destructive thing in this
    package, so it is never a partial write: the replace is atomic, and a failure
    before it leaves the original exactly as it was.
    """
    temp = path.with_name(path.name + _HARVEST_TEMP)
    try:
        temp.write_bytes(text.encode("utf-8"))
        os.replace(temp, path)
    except BaseException:
        temp.unlink(missing_ok=True)
        raise
