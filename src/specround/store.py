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

from pathlib import Path
from typing import Any, Mapping

from specround.anchors import Anchor, anchor_for_quote
from specround.errors import InvariantError, SpecroundError
from specround.events import (
    COMMENT_ADD,
    DISPOSITION,
    REPLY,
    ROUND_CLOSE,
    ROUND_OPEN,
    SUGGESTION_ADD,
)
from specround.fold import Round, State
from specround.ledger import Clock, Ledger
from specround.locations import (
    DIRECTORY,
    DOCUMENT,
    ORIGIN_FILENAME,
    STORE_DIRNAME,
    Origin,
    read_origin,
    resolve_location,
    write_origin,
)
from specround.snapshots import SnapshotStore

LEDGER_FILENAME = "ledger.jsonl"

__all__ = ["LEDGER_FILENAME", "STORE_DIRNAME", "ReviewStore"]


class ReviewStore:
    """Rounds, comments, and dispositions for what one store directory serves."""

    def __init__(
        self,
        root: Path,
        *,
        origin: Origin | None = None,
        clock: Clock | None = None,
    ) -> None:
        self.root = Path(root).resolve()
        # A store that already exists says what it serves; only a fresh one is
        # assumed to serve its parent folder, which is the in-tree layout.
        if origin is None:
            origin = read_origin(self.root) or Origin(DIRECTORY, self.root.parent)
        self.origin = origin
        self.ledger = Ledger(self.root / LEDGER_FILENAME, clock=clock)
        self.snapshots = SnapshotStore(self.root)

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
        return cls(Path(directory).resolve() / STORE_DIRNAME, clock=clock)

    @classmethod
    def open(cls, root: Path, *, clock: Clock | None = None) -> "ReviewStore":
        """Open an existing store by its directory, reading what it serves.

        This is the way back from a central store, whose name is a digest: the
        directory itself is the only thing a caller has, and ``origin`` is the
        only thing that turns it back into a document.
        """
        root = Path(root).resolve()
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
        resolved = Path(doc).resolve()
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
        """The current state, computed from the ledger alone."""
        return self.ledger.state()

    def base_text(self, round_id: str) -> str:
        """The document as this round froze it."""
        state = self.fold()
        round_ = state.rounds.get(round_id)
        if round_ is None:
            raise InvariantError(f"unknown round {round_id!r}")
        return self.snapshots.get_text(round_.base)

    def anchor_in_round(self, round_id: str, quote: str, *, occurrence: int = 0) -> Anchor:
        """Build an anchor by quoting the round's base snapshot."""
        return anchor_for_quote(self.base_text(round_id), quote, occurrence=occurrence)

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
        path = Path(doc).resolve()
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
        """
        if anchor is None:
            return None
        resolved = anchor if isinstance(anchor, Anchor) else Anchor.from_json(anchor)
        resolved.verify(self.base_text(round_id))
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

    # -- convenience -----------------------------------------------------

    def open_rounds(self) -> list[Round]:
        return self.fold().open_rounds

    def latest_round(self, doc: Path | None = None) -> Round | None:
        """The most recently opened round, optionally for one document."""
        key = self.doc_key(doc) if doc is not None else None
        rounds = [r for r in self.fold().rounds.values() if key is None or r.doc == key]
        return rounds[-1] if rounds else None
