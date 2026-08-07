"""A tree of documents, and how much review each one has (H15).

A spec is never one file, and a server per file is a real cost — so ``view``
takes a directory and serves the tree from one process. **This module is
navigation and nothing else.** It finds the markdown under a root, asks each
document's own store how much review it has, and turns a key back into a path.
Rounds, anchors, and the ledger stay per-document axes exactly as they were:
there is no workspace round, no workspace store, and no second definition of
"unresolved" here — the counts come from :func:`specround.wire.document_summary`,
which is the same fold the per-document payload reads.

**Every document keeps its own store.** In the default central layout that is
one store directory per file, and the listing below never merges them; when a
config puts one in-tree store over a folder they share it and the fold is read
once. Either way the question asked per document is the store's own
``doc_key`` — the workspace's keys and a store's keys are two different
relative spaces (a central store keys ``docs/sub/a.md`` as ``a.md``, this module
keys it ``sub/a.md``), and conflating them would attach one document's history
to another's name.

The walk skips anything whose name begins with a dot — ``.git``, ``.specround``,
and every editor's droppings in one rule rather than a list that goes stale. It
follows directory symlinks but remembers where it has been, so a link back up
the tree ends the branch instead of the process. Nothing is ever truncated
quietly: what is not shown is counted and said.
"""

from __future__ import annotations

import os
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from specround.errors import SpecroundError
from specround.fold import State
from specround.ledger import Clock
from specround.locations import canonical_path
from specround.store import ReviewStore
from specround.wire import EMPTY_SUMMARY, document_summary

__all__ = ["DEFAULT_LIMIT", "Document", "Listing", "MARKDOWN_SUFFIXES", "Workspace"]

#: What counts as a document. The renderer's scope is markdown (H11) and a
#: navigation bar that lists files the main panel cannot open is a bar full of
#: dead ends.
MARKDOWN_SUFFIXES = (".md", ".markdown")

#: How many documents a listing shows before it starts holding some back. The
#: limit is on the *listing*, never on the walk: what is hidden is counted, and
#: a document with review activity is never one of them (see :meth:`Workspace.list`).
DEFAULT_LIMIT = 500


@dataclass(frozen=True)
class Document:
    """One file in the tree, with the review its own store knows about."""

    #: The path from the workspace root, POSIX-spelled. This is the name the
    #: page and the API use to say "that one"; it is *not* the store's doc key.
    key: str
    path: Path
    #: Which store holds this document's history, as a path a person can read.
    store: Path
    summary: dict[str, Any]
    #: Why this document's history could not be read, when it could not. A
    #: broken ledger under one file must not blank the whole navigation bar,
    #: and hiding the breakage would be worse than showing it.
    error: str | None = None

    @property
    def active(self) -> bool:
        """True when a review has happened here.

        A round with no comments still counts: someone opened a review on this
        document, which is exactly what the filter is asked to find. Comments
        without a round cannot exist, so this one test covers both.
        """
        return bool(self.summary.get("rounds"))

    def to_json(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "path": str(self.path),
            "store": str(self.store),
            "active": self.active,
            "error": self.error,
            **self.summary,
        }


@dataclass(frozen=True)
class Listing:
    """The documents a workspace shows, and the ones it is holding back."""

    root: Path
    documents: list[Document]
    #: How many markdown documents the walk found, before the limit.
    found: int
    limit: int
    #: Directories the walk refused to enter twice — a symlink pointing back up
    #: the tree is the ordinary cause, and silence about it would leave a
    #: reviewer looking for a file the bar decided not to list.
    revisits: int = 0

    @property
    def hidden(self) -> int:
        return self.found - len(self.documents)

    @property
    def note(self) -> str | None:
        """The one line a caller prints when the listing is not the whole tree."""
        parts = []
        if self.hidden:
            parts.append(
                f"{self.hidden} document(s) not listed — over the {self.limit} shown "
                "(documents with review activity are never held back)"
            )
        if self.revisits:
            parts.append(f"{self.revisits} directory link(s) already visited, not followed again")
        return " · ".join(parts) or None

    def to_json(self) -> dict[str, Any]:
        return {
            "root": str(self.root),
            "documents": [document.to_json() for document in self.documents],
            "found": self.found,
            "shown": len(self.documents),
            "hidden": self.hidden,
            "limit": self.limit,
            "revisits": self.revisits,
            "note": self.note,
            "counts": {
                "documents": len(self.documents),
                "active": sum(1 for d in self.documents if d.active),
                "open_rounds": sum(d.summary.get("open_rounds", 0) for d in self.documents),
                "unresolved": sum(d.summary.get("unresolved", 0) for d in self.documents),
            },
        }


@dataclass
class Workspace:
    """A directory of documents, addressed by paths relative to it."""

    root: Path
    #: An explicit store directory, when the caller named one. It is passed
    #: through to each document's own resolution rather than used directly:
    #: ``--store`` means the same thing here as it does for every other verb.
    store: Path | None = None
    clock: Clock | None = None
    limit: int = DEFAULT_LIMIT
    #: Store handles, kept because they are handles — a store caches immutable
    #: snapshot text and re-reads the ledger on every fold, so reusing one can
    #: never serve a stale answer.
    _stores: dict[Path, ReviewStore] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        self.root = canonical_path(self.root)

    # -- discovery -------------------------------------------------------

    def scan(self) -> tuple[list[str], int]:
        """Every markdown document under the root, and how many links were skipped.

        Breadth-first over sorted entries, so the answer does not depend on the
        order a filesystem hands names back. Unreadable directories are stepped
        over: a listing that dies on one bad permission is a listing nobody can
        use, and the document under it was never reachable anyway.
        """
        keys: list[str] = []
        seen = {self.root}
        revisits = 0
        queue: deque[tuple[Path, str]] = deque([(self.root, "")])
        while queue:
            directory, prefix = queue.popleft()
            try:
                with os.scandir(directory) as entries:
                    listed = sorted(entries, key=lambda entry: entry.name)
            except OSError:
                continue
            for entry in listed:
                if entry.name.startswith("."):
                    continue
                path = Path(entry.path)
                try:
                    if entry.is_dir():
                        # The identity of a directory is where it really is, not
                        # the name that reached it. Following a link is worth
                        # doing — a docs tree that points at a shared folder is
                        # ordinary — and following it twice is a loop.
                        real = canonical_path(path)
                        if real in seen:
                            revisits += 1
                            continue
                        seen.add(real)
                        queue.append((path, f"{prefix}{entry.name}/"))
                    elif entry.is_file() and path.suffix.lower() in MARKDOWN_SUFFIXES:
                        keys.append(f"{prefix}{entry.name}")
                except OSError:
                    continue
        return sorted(keys), revisits

    def list(self) -> Listing:
        """The tree with its badges — one fold per store, not one per document.

        The limit hides documents with **no** review activity first, and only
        those: a reviewer opening a tree is looking for the documents that have
        comments on them, and a cap that could hide one of those would make the
        filter lie. What is held back is counted, never dropped in silence.
        """
        keys, revisits = self.scan()
        folds: dict[Path, State] = {}
        documents = [self._describe(key, folds) for key in keys]
        active = [document for document in documents if document.active]
        room = max(0, self.limit - len(active))
        quiet = [document for document in documents if not document.active][:room]
        shown = sorted(active + quiet, key=lambda document: document.key)
        return Listing(
            root=self.root,
            documents=shown,
            found=len(documents),
            limit=self.limit,
            revisits=revisits,
        )

    def _describe(self, key: str, folds: dict[Path, State]) -> Document:
        path = self.root / key
        store = self.store_for(path)
        try:
            doc_key = store.doc_key(path)
            if not store.ledger.exists():
                # Nothing has ever been recorded here. Not an error and not a
                # fold: the common case in any real tree is a file nobody has
                # reviewed, and reading a store that does not exist to learn
                # that would cost the walk its cheapness.
                return Document(key=key, path=path, store=store.root, summary=dict(EMPTY_SUMMARY))
            state = folds.get(store.root)
            if state is None:
                state = store.fold()
                folds[store.root] = state
            summary = document_summary(state, doc_key)
        except (SpecroundError, OSError) as exc:
            # One document's history refusing to fold (a hand-edited ledger, an
            # anchor that no longer agrees with its snapshot) is a fact about
            # that document. Opening it still raises in the reviewer's face
            # through the per-document route; the bar says so and lists the rest.
            return Document(
                key=key, path=path, store=store.root, summary=dict(EMPTY_SUMMARY), error=str(exc)
            )
        return Document(key=key, path=path, store=store.root, summary=summary)

    # -- addressing ------------------------------------------------------

    def store_for(self, path: Path) -> ReviewStore:
        """The store that owns one document, by the ordinary resolution rules."""
        resolved = canonical_path(path)
        held = self._stores.get(resolved)
        if held is None:
            held = ReviewStore.for_document(resolved, store=self.store, clock=self.clock)
            self._stores[resolved] = held
        return held

    def resolve(self, key: str) -> Path:
        """Turn a key from the page back into a path under this root.

        Structural, and deliberately not a membership test against
        :meth:`list`: the limit is a display decision, and a document the bar is
        holding back is still a document this workspace serves. What is refused
        is anything that would address a file the workspace does not contain —
        an absolute path, a ``..`` segment, a dotted component the walk skips,
        or a name that is not markdown.

        The caller here is a page holding this view's token, so this is not a
        privilege boundary; it is the guard that keeps a mistyped or stale key
        from quietly opening some other file's history under this one's name.
        """
        if not key or key != key.strip():
            raise SpecroundError("a document key must not be empty or padded")
        candidate = Path(key)
        if candidate.is_absolute() or candidate.drive:
            raise SpecroundError(f"{key!r} is an absolute path — keys count from the workspace root")
        parts = candidate.parts
        for part in parts:
            if part in ("..", "."):
                raise SpecroundError(f"{key!r} steps outside the workspace")
            if part.startswith("."):
                raise SpecroundError(f"{key!r} names a hidden entry, which the workspace skips")
        if candidate.suffix.lower() not in MARKDOWN_SUFFIXES:
            suffixes = " or ".join(MARKDOWN_SUFFIXES)
            raise SpecroundError(f"{key!r} is not a markdown document ({suffixes})")
        path = self.root.joinpath(*parts)
        if not path.is_file():
            raise SpecroundError(f"no document {key!r} under {self.root}")
        return path
