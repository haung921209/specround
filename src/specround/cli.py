"""The shell entry point (G4, G7).

One command opens a review, and the same command an agent runs is the one a
person runs. That is both guarantees at once: G7 says the way in is a shell
command, G4 says the agent is a first-class participant rather than something
bolted to the side, and the cheapest way to keep those from drifting apart is to
have exactly one surface with two output modes.

**Every verb takes ``--json``.** The human output is a plain table meant to be
read; the JSON is a stable object meant to be parsed. They are rendered from the
same computed result, so an agent and a person are never looking at two
different answers.

**The exit code is the verdict.** Nothing has to grep stdout to find out what
happened:

===== ==========================================================================
``0``  it worked
``2``  the command cannot be carried out as typed — fix the invocation
``3``  the command is fine, the recorded history refuses it — change the state
``1``  anything else broke
===== ==========================================================================

The 2/3 line is where the two error classes actually differ for a caller.
Commenting on a document with no open round is ``3``: the fix is
``specround round open``. Naming a quote that appears four times is ``2``: the
fix is ``--occurrence``. Success output goes to stdout and errors go to stderr,
so ``specround comments spec.md --json | jq`` never has to defend itself
against an error object arriving where a result was expected.

The verbs are deliberately few. Rounds, comments, replies, re-anchoring,
dispositions, and closing a thread are the loop the ledger already knows how to
enforce. ``view`` is the one verb that does not return: it prints a URL and then
serves a browser until interrupted, which is why :func:`main` delivers a verb's
output before running anything the verb handed back to do.
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from specround import __version__
from specround.anchors import Anchor, count_occurrences
from specround.errors import AnchorError, InvariantError, SpecroundError
from specround.events import ACTORS, ANSWERED, APPLIED, DEFERRED, HUMAN, REJECTED
from specround.fold import Comment, Round, State
from specround.imports import BatchError, apply_plan, load_batch, parse_text, plan_import
from specround.locations import canonical_path
from specround.reanchor import FUZZY
from specround.store import ReviewStore
from specround.webview import DEFAULT_HOST, WebView
from specround.wire import (
    anchor_json,
    comment_json,
    comments_on,
    disposition_json,
    reply_json,
    round_json,
    rounds_on,
)

#: Exit codes. See the module docstring — these are the contract, not the prose.
OK = 0
FAILURE = 1
USAGE = 2
STATE = 3

#: The ``--json`` envelope carries its own version, for the same reason the
#: ledger lines do: a consumer should be able to tell that the shape it parses
#: is the shape it was written against.
CLI_SCHEMA = "specround.cli/v0"

#: ``held`` is the word this CLI was specified with; ``deferred`` is the word
#: the ledger stores and every output prints. Both are accepted on the way in
#: and only one comes out, so the ledger's closed vocabulary stays the only
#: vocabulary a reader has to learn.
VERDICT_ALIASES = {"held": DEFERRED}
VERDICT_CHOICES = (APPLIED, REJECTED, ANSWERED, DEFERRED, "held")

#: Environment fallback for ``--author``.
AUTHOR_ENV = "SPECROUND_AUTHOR"
#: Environment fallback for ``--actor`` on the thread verbs.
#:
#: ``author`` says which participant, ``actor`` says which *kind* — the ledger
#: keeps them apart because ``agent:`` in a name is a convention no reader can
#: check. So this CLI does not infer one from the other either: it defaults to
#: ``human`` and lets an agent harness set this once in its environment, the
#: same shape as ``--author``.
ACTOR_ENV = "SPECROUND_ACTOR"

#: How much of a quote or body a table cell shows before it is clipped.
_QUOTE_WIDTH = 28
_BODY_WIDTH = 44
#: How much of a snapshot digest human output prints.
_REF_CHARS = 12
#: What puts a reply under its thread root in the table.
_REPLY_INDENT = "  └ "


class ArgvError(SpecroundError):
    """argparse refused the command line, before any verb ran.

    Carried rather than printed so the failure leaves through the one exit
    that knows about ``--json``. argparse writes plain text to stderr and
    exits, which put the argument axis outside the structured output G4 asks
    for: an agent got a JSON envelope for every failure except this one.
    """

    def __init__(self, message: str, *, usage: str, prog: str) -> None:
        super().__init__(message)
        self.usage = usage
        self.prog = prog

    @property
    def verb(self) -> str | None:
        """The verb argparse had resolved, or ``None`` if it never got one.

        ``prog`` is "specround round open" by the time a subparser refuses, and
        plain "specround" when the top level does. Null is the honest answer
        for the second case — guessing a verb into the envelope would be a
        field a consumer cannot trust.
        """
        parts = self.prog.split()[1:]
        return ".".join(parts) if parts else None


class _Parser(argparse.ArgumentParser):
    """An ``ArgumentParser`` that raises instead of exiting.

    ``add_subparsers`` hands this class down, so every subparser refuses the
    same way and the whole surface has one exit path.
    """

    def error(self, message: str) -> "None":  # type: ignore[override]
        raise ArgvError(message, usage=self.format_usage(), prog=self.prog)


class UsageError(SpecroundError):
    """The invocation cannot be carried out as typed.

    Distinct from :class:`~specround.errors.InvariantError` on purpose: this one
    means the caller should change the command, that one means the caller should
    change the history first. Collapsing them would leave an agent unable to
    tell "add ``--occurrence``" from "open a round".
    """


# -- shared plumbing -----------------------------------------------------


@dataclass(frozen=True)
class Target:
    """The document a verb was pointed at, and the store that holds it."""

    store: ReviewStore
    path: Path
    key: str

    def envelope(self) -> dict[str, Any]:
        """The fields every ``--json`` payload carries about its subject."""
        return {"doc": self.key, "path": str(self.path), "store": str(self.store.root)}


def _document(value: str, *, must_exist: bool = True) -> Path:
    """Resolve the document argument, refusing a path that is not there.

    Every verb names a document, including the read-only ones, and a mistyped
    path that quietly reports "no comments" is worse than an error: the store is
    keyed by path, so a typo addresses a different (empty) history and the answer
    looks like a fact.

    ``must_exist=False`` is for the read-only verbs, where the missing file is
    not necessarily a typo — a document can be renamed or deleted while its
    history stays exactly where it was. :func:`_target` finishes that check
    against the store, because the thing that separates a rename from a typo is
    whether there is any history behind the path.
    """
    path = Path(value).expanduser()
    if must_exist and not path.is_file():
        raise UsageError(f"{path}: not a file — every verb names the document under review")
    return canonical_path(path)


def _target(args: argparse.Namespace, *, missing_ok: bool = False) -> Target:
    path = _document(args.doc, must_exist=not missing_ok)
    store = ReviewStore.for_document(path, store=Path(args.store) if args.store else None)
    key = store.doc_key(path)
    if missing_ok and not path.is_file() and not _has_history(store, key):
        # The file is gone. That is a rename when the store holds history for
        # this key and a typo otherwise — one answer comes from a record, the
        # other would come from a store that was never anything.
        raise UsageError(
            f"{path}: not a file, and no history for it in {store.root} — "
            "check the path (a moved document keeps its old store)"
        )
    return Target(store=store, path=path, key=key)


def _has_history(store: ReviewStore, key: str) -> bool:
    """True when this store already holds a round for ``key``."""
    return store.ledger.exists() and bool(rounds_on(store.fold(), key))


def _author(args: argparse.Namespace) -> str:
    """Who is recording this — a person or an agent, in the same field (G4)."""
    candidates = [getattr(args, "author", None), os.environ.get(AUTHOR_ENV)]
    for candidate in candidates:
        if candidate and candidate.strip():
            return candidate.strip()
    try:
        name = getpass.getuser()
    except Exception:  # pragma: no cover - only on a host with no user record
        name = ""
    if name.strip():
        return name.strip()
    raise UsageError(f"cannot tell who you are: pass --author or set {AUTHOR_ENV}")


def _actor(args: argparse.Namespace) -> str:
    """Whether a person or an agent is closing this conversation (G11).

    Defaulting to ``human`` rather than guessing from ``--author``: the format
    is explicit that the ``agent:`` prefix is a convention, and a tool that read
    it as a fact would write an unverifiable guess into a closed vocabulary.
    """
    chosen = getattr(args, "actor", None) or os.environ.get(ACTOR_ENV) or HUMAN
    chosen = chosen.strip()
    if chosen not in ACTORS:
        raise UsageError(
            f"unknown actor {chosen!r} (from ${ACTOR_ENV}): use {' or '.join(ACTORS)}"
        )
    return chosen


def _live_round(state: State, key: str, wanted: str | None, *, verb: str) -> Round:
    """The round a writing verb applies to.

    Named explicitly with ``--round``, otherwise the one open round. Two open
    rounds is a ``2`` rather than a silent pick: which round a comment belongs to
    is not a detail the tool gets to guess.
    """
    if wanted:
        chosen = state.rounds.get(wanted)
        if chosen is None or chosen.doc != key:
            raise UsageError(f"no round {wanted!r} on {key}")
        if not chosen.open:
            raise InvariantError(
                f"round {wanted!r} is closed — open a new round to {verb}"
            )
        return chosen
    live = [r for r in rounds_on(state, key) if r.open]
    if not live:
        raise InvariantError(
            f"no open round on {key} — open one with 'specround round open' first"
        )
    if len(live) > 1:
        names = ", ".join(sorted(r.id for r in live))
        raise UsageError(f"{len(live)} rounds are open ({names}): name one with --round")
    return live[0]


def _comment(state: State, key: str, wanted: str) -> Comment:
    """Find a comment by id or by an unambiguous prefix of one.

    Ids are a prefix plus twelve hex characters, which nobody types twice, so
    the prefix form is what makes the shell usable. It is scoped to this
    document: ``<doc>`` names the review, and a comment from a different one is
    not addressable through it.
    """
    scoped = {c.id for c in comments_on(state, key)}
    if wanted in scoped:
        return state.comments[wanted]
    matches = sorted(cid for cid in scoped if cid.startswith(wanted))
    if not matches:
        raise UsageError(f"no comment {wanted!r} on {key}")
    if len(matches) > 1:
        raise UsageError(
            f"{wanted!r} matches {len(matches)} comments ({', '.join(matches)}): "
            "give more of the id"
        )
    return state.comments[matches[0]]


def _body(args: argparse.Namespace, what: str = "comment") -> str:
    if args.body is not None and args.body_file is not None:
        raise UsageError("--body and --body-file say the same thing twice: pick one")
    if args.body_file is not None:
        if args.body_file == "-":
            text = sys.stdin.read()
        else:
            path = Path(args.body_file).expanduser()
            if not path.is_file():
                raise UsageError(f"{path}: not a file")
            try:
                text = path.read_text(encoding="utf-8")
            except OSError as exc:
                raise UsageError(f"cannot read {path}: {exc}") from exc
    elif args.body is not None:
        text = args.body
    else:
        raise UsageError(
            f"a {what} needs a body: --body TEXT, --body-file PATH, or --body-file -"
        )
    text = text.strip()
    if not text:
        raise UsageError(f"the {what} body is empty")
    return text


def _anchor(store: ReviewStore, round_: Round, quote: str, occurrence: int | None) -> Anchor:
    """Cut an anchor out of the round's base — the text the reviewer read.

    Not the live document: a round's base is frozen (G2), so a quote taken from
    a file that has since been revised has to fail loudly rather than land on
    whatever the same offsets point at now.
    """
    if not quote:
        raise UsageError("--quote must not be empty")
    text = store.base_text(round_.id)
    total = count_occurrences(text, quote)
    if total == 0:
        raise UsageError(
            f"--quote {quote!r} is not in the snapshot round {round_.id} froze "
            "(the document may have been revised since — quote the base, or open a new round)"
        )
    if total > 1 and occurrence is None:
        raise UsageError(
            f"--quote {quote!r} appears {total} times in the base: "
            f"say which one with --occurrence 0..{total - 1}"
        )
    try:
        return store.anchor_in_round(round_.id, quote, occurrence=occurrence or 0)
    except AnchorError as exc:
        raise UsageError(str(exc)) from exc


# -- serialisation -------------------------------------------------------
#
# The shapes live in :mod:`specround.wire`, because the web view answers the
# same questions about the same state and a second copy of the projection
# would drift from this one silently.


# -- human output --------------------------------------------------------


def _clip(text: str, limit: int) -> str:
    """One line, at most ``limit`` characters — tables stay readable."""
    flat = " ".join(text.split())
    if len(flat) <= limit:
        return flat
    return flat[: limit - 1] + "…"


def _short(ref: str) -> str:
    """A snapshot reference, shortened for reading. JSON keeps the full digest."""
    _, _, digest = ref.partition(":")
    return (digest or ref)[:_REF_CHARS]


def _table(
    headers: Sequence[str],
    rows: Sequence[Sequence[str]],
    *,
    right: frozenset[int] = frozenset(),
) -> list[str]:
    """Column-aligned plain text. ``right`` names the columns holding counts."""
    widths = [len(h) for h in headers]
    for row in rows:
        for index, cell in enumerate(row):
            widths[index] = max(widths[index], len(cell))

    def line(cells: Sequence[str]) -> str:
        pad = [
            cell.rjust(widths[i]) if i in right else cell.ljust(widths[i])
            for i, cell in enumerate(cells)
        ]
        return "  ".join(pad).rstrip()

    return [line(headers), *(line(row) for row in rows)]


def _anchor_cell(comment: Comment) -> str:
    anchor = comment.current_anchor
    if anchor is None:
        return "(document)"
    return _clip(anchor.exact, _QUOTE_WIDTH)


def _moved_marks(comment: Comment) -> list[str]:
    """What about this comment's landing is worth a person's attention.

    Not every move is. A comment pushed down the page by an insertion above
    matched its quote verbatim and needs nobody — that is the distinction the
    closed strategy vocabulary exists to draw (§4). ``fuzzy`` means the quoted
    text itself was rewritten, and ``ambiguous`` means more than one span fit
    equally well and the old position broke the tie. Those two, and only those,
    are news a week later.
    """
    placed = comment.current_anchoring
    if placed is None:
        return []
    marks = []
    if placed.strategy == FUZZY:
        marks.append(FUZZY)
    if placed.ambiguous:
        marks.append("ambiguous")
    return marks


def _comment_rows(comments: Sequence[Comment], *, hidden: Sequence[str] = ()) -> list[str]:
    """The listing: one row per thread root, its replies indented beneath it.

    Replies are indented rather than given their own table because a reply has
    no state, no anchor, and no verdict of its own — it belongs to the thread
    above it, and a flat list of ids would make the reader reconstruct that.

    ``hidden`` names resolved threads left out of ``comments``. It is printed
    even though the rows are not: hiding is a view decision, and a view that
    hid something without saying so would read as "there is nothing else".
    """
    rows: list[list[str]] = []
    for comment in comments:
        rows.append(
            [
                comment.id,
                comment.kind,
                comment.state,
                comment.author,
                _anchor_cell(comment),
                _clip(comment.patch or comment.body, _BODY_WIDTH),
            ]
        )
        rows.extend(
            [f"{_REPLY_INDENT}{reply.id}", "reply", "", reply.author, "", _clip(reply.body, _BODY_WIDTH)]
            for reply in comment.replies
        )
    lines = _table(["ID", "KIND", "STATE", "AUTHOR", "ANCHOR", "BODY"], rows)

    # Footers rather than columns: each reports an uncommon case, and widening
    # every row for it would cost the common case to describe the exception.
    # Resolved goes above orphaned so the "and there is more" line is the last
    # thing read when both apply.
    footers = []
    if hidden:
        footers.append(f"{len(hidden)} resolved and hidden — show with --all")
    else:
        resolved = [c.id for c in comments if c.resolved]
        if resolved:
            footers.append(f"{len(resolved)} resolved: {', '.join(resolved)}")
    orphans = [c.id for c in comments if c.orphaned]
    if orphans:
        footers.append(f"{len(orphans)} orphaned: {', '.join(orphans)}")
    moved = [(c.id, _moved_marks(c)) for c in comments if not c.orphaned]
    flagged = [f"{cid} ({', '.join(marks)})" for cid, marks in moved if marks]
    if flagged:
        # The ``reanchor`` run said this once, to whoever happened to be running
        # it. This is where the reviewer reading the list afterwards finds out.
        footers.append(f"{len(flagged)} moved on rewritten text — worth a look: {', '.join(flagged)}")
    if footers:
        lines.append("")
        lines.extend(footers)
    return lines


def _moved_how(comment: Comment) -> str:
    """Why this comment's last move needs a human, or ``""`` if it does not."""
    anchoring = comment.anchoring
    if anchoring is None or anchoring.orphaned:
        return ""
    marks = [
        mark
        for mark in (
            anchoring.strategy if anchoring.strategy == FUZZY else "",
            "ambiguous" if anchoring.ambiguous else "",
        )
        if mark
    ]
    return ", ".join(marks)


# -- verbs ---------------------------------------------------------------


def _round_open(args: argparse.Namespace) -> tuple[dict[str, Any], list[str]]:
    target = _target(args)
    state = target.store.fold()
    live = [r for r in rounds_on(state, target.key) if r.open]
    if live:
        raise InvariantError(
            f"round {live[0].id!r} is already open on {target.key}: close it first. "
            "One open round per document is what lets the other verbs say 'the round' "
            "without asking"
        )
    round_id = target.store.open_round(
        target.path, author=_author(args), title=args.title or None
    )
    state = target.store.fold()
    round_ = state.rounds[round_id]
    payload = {**target.envelope(), "round": round_json(state, round_)}
    lines = [f"opened {round_id} on {target.key} (base {_short(round_.base)})"]
    if round_.title:
        lines.append(f"title  {round_.title}")
    lines.append(f"store  {target.store.root}")
    return payload, lines


def _round_close(args: argparse.Namespace) -> tuple[dict[str, Any], list[str]]:
    target = _target(args)
    state = target.store.fold()
    round_ = _live_round(state, target.key, args.round, verb="close")
    outstanding = sorted(c.id for c in state.unresolved_in(round_.id))
    if outstanding and not args.allow_unresolved:
        # The store refuses this too, and it stays the gate. This check exists
        # only to name the flag a shell caller actually has — the library's own
        # message names its keyword argument, which is right there and wrong here.
        raise InvariantError(
            f"round {round_.id} has {len(outstanding)} unresolved comment(s) "
            f"({', '.join(outstanding)}): dispose them, or pass --allow-unresolved "
            "to record that they were left open"
        )
    close_id = target.store.close_round(
        round_.id,
        author=_author(args),
        allow_unresolved=args.allow_unresolved,
        note=args.note or None,
    )
    state = target.store.fold()
    closed = state.rounds[round_.id]
    payload = {
        **target.envelope(),
        "round": round_json(state, closed),
        "close": close_id,
        "unresolved": list(closed.unresolved_at_close),
    }
    lines = [f"closed {round_.id} on {target.key}"]
    if closed.unresolved_at_close:
        lines.append(
            f"left unresolved ({len(closed.unresolved_at_close)}): "
            + ", ".join(closed.unresolved_at_close)
        )
    return payload, lines


def _round_status(args: argparse.Namespace) -> tuple[dict[str, Any], list[str]]:
    # Read-only: a document that was renamed or deleted still has history, and
    # the CLI is the way to it (B5). _target refuses a path with nothing behind it.
    target = _target(args, missing_ok=True)
    state = target.store.fold()
    rounds = rounds_on(state, target.key)
    comments = comments_on(state, target.key)
    unresolved = [c.id for c in comments if c.unresolved]
    orphans = [c.id for c in comments if c.orphaned]
    open_ids = [r.id for r in rounds if r.open]
    payload = {
        **target.envelope(),
        "rounds": [round_json(state, r) for r in rounds],
        "open": open_ids,
        "unresolved": unresolved,
        "orphans": orphans,
        "counts": {
            "rounds": len(rounds),
            "comments": len(comments),
            "unresolved": len(unresolved),
            "orphans": len(orphans),
            "events": state.count,
        },
    }
    lines = [
        f"{target.key} — {len(rounds)} round(s), {len(open_ids)} open, "
        f"{len(comments)} comment(s), {len(unresolved)} unresolved, "
        f"{len(orphans)} orphaned",
        f"store  {target.store.root}",
    ]
    if rounds:
        lines.append("")
        lines.extend(
            _table(
                ["ROUND", "STATUS", "COMMENTS", "UNRESOLVED", "BASE", "TITLE"],
                [
                    [
                        r.id,
                        r.status,
                        str(len(state.comments_in(r.id))),
                        str(sum(1 for c in state.comments_in(r.id) if c.unresolved)),
                        _short(r.base),
                        _clip(r.title, _BODY_WIDTH),
                    ]
                    for r in rounds
                ],
                right=frozenset({2, 3}),
            )
        )
    else:
        lines.append("no rounds yet")
    return payload, lines


def _comment_add(args: argparse.Namespace) -> tuple[dict[str, Any], list[str]]:
    target = _target(args)
    state = target.store.fold()
    round_ = _live_round(state, target.key, args.round, verb="comment on")
    body = _body(args)
    if args.occurrence is not None and not args.quote:
        # Dropping it silently makes a comment on the whole document look like
        # an anchored one to the caller who typed it — exit 0 and no anchor.
        raise UsageError(
            "--occurrence picks between appearances of --quote: give a --quote, or drop it"
        )
    anchor = _anchor(target.store, round_, args.quote, args.occurrence) if args.quote else None
    comment_id = target.store.add_comment(
        round_.id, author=_author(args), body=body, anchor=anchor
    )
    state = target.store.fold()
    comment = state.comments[comment_id]
    payload = {**target.envelope(), "comment": comment_json(comment)}
    where = f'on "{_clip(anchor.exact, _QUOTE_WIDTH)}"' if anchor else "on the document"
    return payload, [f"{comment_id} added to {round_.id} {where}"]


def _reply(args: argparse.Namespace) -> tuple[dict[str, Any], list[str]]:
    """Answer a comment — the same verb for a person and for an agent (G4)."""
    target = _target(args)
    state = target.store.fold()
    comment = _comment(state, target.key, args.comment)
    body = _body(args, "reply")
    reply_id = target.store.reply(comment.id, author=_author(args), body=body)
    state = target.store.fold()
    answered = state.comments[comment.id]
    reply = answered.replies[-1]
    payload = {
        **target.envelope(),
        "comment": comment_json(answered),
        "reply": reply_json(reply),
    }
    return payload, [
        f"{reply.id} replied to {answered.id} by {reply.author} "
        f"({len(answered.replies)} in this thread)"
    ]


def _thread(args: argparse.Namespace, *, close: bool) -> tuple[dict[str, Any], list[str]]:
    """Close or re-open a conversation (G11).

    One function for both because they are one decision with a sign. The only
    asymmetry is the text: closing may carry a note, re-opening must carry a
    reason — overturning something already in the log says why.

    Re-stating the state a thread is already in exits ``0`` and says nothing
    changed. That is the ledger's rule (I10) reaching the shell: a caller who
    resolves a resolved thread wanted it resolved and it is, and turning that
    into a failure would make retries dangerous for the agents this is for.
    """
    target = _target(args)
    state = target.store.fold()
    comment = _comment(state, target.key, args.comment)
    author, actor = _author(args), _actor(args)
    if close:
        event = target.store.resolve(comment.id, author=author, actor=actor, note=args.note or None)
    else:
        event = target.store.reopen(comment.id, author=author, actor=actor, reason=args.why)
    state = target.store.fold()
    updated = state.comments[comment.id]
    payload = {
        **target.envelope(),
        "comment": comment_json(updated),
        "resolved": updated.resolved,
        "changed": event is not None,
        "event": event,
    }
    word = "resolved" if close else "reopened"
    if event is None:
        return payload, [f"{updated.id} was already {word} — nothing recorded"]
    return payload, [f"{updated.id} {word} by {author} ({actor})"]


def _resolve(args: argparse.Namespace) -> tuple[dict[str, Any], list[str]]:
    return _thread(args, close=True)


def _reopen(args: argparse.Namespace) -> tuple[dict[str, Any], list[str]]:
    return _thread(args, close=False)


def _comments(args: argparse.Namespace) -> tuple[dict[str, Any], list[str]]:
    # Read-only: a document that was renamed or deleted still has history, and
    # the CLI is the way to it (B5). _target refuses a path with nothing behind it.
    target = _target(args, missing_ok=True)
    state = target.store.fold()
    items = comments_on(state, target.key)
    if args.round:
        chosen = state.rounds.get(args.round)
        if chosen is None or chosen.doc != target.key:
            raise UsageError(f"no round {args.round!r} on {target.key}")
        items = [c for c in items if c.round == args.round]
    if args.unresolved:
        items = [c for c in items if c.unresolved]
    # The thread axis filters last, so --all reports against the same set the
    # other filters produced rather than against the whole document.
    hidden = [] if args.all else [c.id for c in items if c.resolved]
    if hidden:
        items = [c for c in items if not c.resolved]
    payload = {
        **target.envelope(),
        "comments": [comment_json(c) for c in items],
        "include_resolved": bool(args.all),
        "hidden": hidden,
    }
    if not items:
        if hidden:
            # Not "no comments": there are some, this view is just not showing
            # them. Saying "none" here would be the hiding-as-deleting failure
            # G11 is explicit about.
            return payload, [
                f"no open threads on {target.key} — {len(hidden)} resolved, show with --all"
            ]
        return payload, [f"no comments on {target.key}"]
    return payload, _comment_rows(items, hidden=hidden)


def _reanchor(args: argparse.Namespace) -> tuple[dict[str, Any], list[str]]:
    target = _target(args)
    report = target.store.reanchor_document(target.path, author=_author(args))
    state = target.store.fold()
    strategies = {
        cid: state.comments[cid].anchoring.strategy
        for cid in report.rebound
        if state.comments[cid].anchoring is not None
    }
    reasons = {
        cid: state.comments[cid].anchoring.reason
        for cid in report.orphaned
        if state.comments[cid].anchoring is not None
    }
    payload = {
        **target.envelope(),
        "base": report.base,
        "changed": report.changed,
        "rebound": list(report.rebound),
        "orphaned": list(report.orphaned),
        "unchanged": list(report.unchanged),
        "skipped": list(report.skipped),
        "ambiguous": list(report.ambiguous),
        "strategies": strategies,
        "reasons": reasons,
    }

    def described(cid: str) -> str:
        marks = [m for m in (strategies.get(cid), "ambiguous" if cid in report.ambiguous else "") if m]
        return f"{cid} ({', '.join(marks)})" if marks else cid

    lines = [f"re-anchored {target.key} against {_short(report.base)}"]
    for label, ids, describe in (
        ("rebound", report.rebound, True),
        ("orphaned", report.orphaned, False),
        ("unchanged", report.unchanged, False),
        ("skipped", report.skipped, False),
    ):
        shown = ", ".join(described(c) if describe else c for c in ids)
        lines.append(f"  {label:<10}{len(ids):>3}  {shown}".rstrip())
    if report.ambiguous:
        lines.append("")
        lines.append(
            f"{len(report.ambiguous)} moved on a tie — worth a look: "
            + ", ".join(report.ambiguous)
        )
    return payload, lines


def _import(args: argparse.Namespace) -> tuple[dict[str, Any], list[str]]:
    """Absorb comments made in another tool, through a documented file (H9).

    Dry run by default. What an import does is write somebody else's judgement
    into this ledger under a round, and the anchoring is the part that can be
    wrong — so the plan is the thing you read first, and ``--apply`` is a second
    sentence you type.

    Refusals are per-item and the exit stays ``0``, the same shape ``reanchor``
    already has for a comment it could not place: the run did what it could and
    says what it could not, rather than turning one moved paragraph into a
    failure that hides the twenty comments that were fine.
    """
    target = _target(args)
    state = target.store.fold()
    round_ = _live_round(state, target.key, args.round, verb="import into")
    if args.file == "-":
        batch = parse_text(sys.stdin.read())
    else:
        path = Path(args.file).expanduser()
        if not path.is_file():
            raise UsageError(f"{path}: not a file — --file names the import JSON")
        batch = load_batch(path)
    plan = plan_import(target.store, round_.id, target.key, batch)

    written: list[tuple[Any, str]] = []
    if args.apply:
        written = apply_plan(plan, target.store, author=_author(args))

    payload = {
        **target.envelope(),
        "source": plan.source,
        "round": round_.id,
        "applied": bool(args.apply),
        "imported": [
            {
                "source_id": item.id,
                "comment": comment_id,
                "how": entry.how,
            }
            for (item, comment_id), entry in zip(written, plan.planned)
        ],
        "planned": [
            {
                "source_id": entry.item.id,
                "how": entry.how,
                "quote": entry.anchor.exact if entry.anchor else None,
                "body": entry.item.body,
                "author": entry.item.author,
                "ts": entry.item.ts,
            }
            for entry in plan.planned
        ],
        "skipped": [
            {"source_id": entry.item.id, "comment": entry.comment} for entry in plan.skipped
        ],
        "rejected": [
            {"source_id": entry.item.id, "reason": entry.reason} for entry in plan.rejected
        ],
        "counts": {
            "total": plan.total,
            "planned": len(plan.planned),
            "skipped": len(plan.skipped),
            "rejected": len(plan.rejected),
        },
    }

    verb = "imported" if args.apply else "would import"
    lines = [
        f"{verb} {len(plan.planned)} of {plan.total} from {plan.source!r} "
        f"into {round_.id} on {target.key}"
    ]
    rows: list[list[str]] = []
    landed = dict(zip((entry.item.id for entry in plan.planned), (cid for _, cid in written)))
    for entry in plan.planned:
        rows.append(
            [
                landed.get(entry.item.id, "import"),
                _clip(entry.item.id, _QUOTE_WIDTH),
                entry.how,
                _clip(entry.anchor.exact, _QUOTE_WIDTH) if entry.anchor else "(document)",
                _clip(entry.item.body, _BODY_WIDTH),
            ]
        )
    for entry in plan.skipped:
        rows.append(
            ["skip", _clip(entry.item.id, _QUOTE_WIDTH), "", "", f"already imported as {entry.comment}"]
        )
    for entry in plan.rejected:
        rows.append(
            ["refuse", _clip(entry.item.id, _QUOTE_WIDTH), "", "", _clip(entry.reason, _BODY_WIDTH)]
        )
    if rows:
        lines.append("")
        lines.extend(_table(["RESULT", "SOURCE ID", "VIA", "ANCHOR", "NOTE"], rows))
    if plan.rejected:
        lines.append("")
        # Full text, not the clipped cell: a refusal names what to change, and a
        # reason cut off at the column width is a reason nobody can act on.
        lines.append(f"{len(plan.rejected)} refused — nothing was guessed at:")
        lines.extend(f"  {entry.item.id}: {entry.reason}" for entry in plan.rejected)
    if not args.apply and plan.planned:
        lines.append("")
        lines.append("nothing written — run again with --apply")
    return payload, lines


def _dispose(args: argparse.Namespace) -> tuple[dict[str, Any], list[str]]:
    target = _target(args)
    state = target.store.fold()
    comment = _comment(state, target.key, args.comment)
    verdict = VERDICT_ALIASES.get(args.verdict, args.verdict)
    target.store.dispose(
        comment.id, author=_author(args), verdict=verdict, reason=args.why
    )
    state = target.store.fold()
    settled = state.comments[comment.id]
    payload = {
        **target.envelope(),
        "comment": comment_json(settled),
        "disposition": disposition_json(settled.disposition),
    }
    current = settled.disposition
    assert current is not None  # just appended
    return payload, [f"{settled.id} {current.verdict} by {current.author} — {current.reason}"]


def _view(args: argparse.Namespace) -> tuple[dict[str, Any], list[str], Callable[[], None]]:
    """Serve the document to a browser, three modes over one anchor space (G6).

    The URL is the first line of stdout and nothing opens a browser, because the
    first-class consumer is an embedder: a multiplexer's browser pane takes that
    line and places the view where the reviewer already is. ``--open`` is for
    when the caller is a person at a shell instead.

    Read-only is a normal outcome here rather than a refusal. The CLI's writing
    verbs need an open round and say so; a *view* of a document whose rounds are
    all closed still shows the review that happened, and the payload carries the
    reason the two commenting routes are shut. Two open rounds is the same
    answer: the note names ``--round`` and the history is still readable.

    A ``--round`` that names nothing is the exception, and it is a ``2``. The
    others are "there is less to write than you might expect"; this one is "what
    you asked for is not here", and serving something else under that name would
    be the quiet wrong answer the exit codes exist to separate.
    """
    target = _target(args, missing_ok=True)
    view = WebView(
        store=target.store,
        path=target.path,
        author=_author(args),
        actor=_actor(args),
        round_hint=args.round or None,
        host=args.host,
        port=args.port,
    ).bind()
    state = target.store.fold()
    round_, blocked = view.resolve_round(state)
    if args.round and round_ is None:
        assert blocked is not None
        raise UsageError(blocked)
    payload = {
        **target.envelope(),
        "url": view.url,
        "host": view.host,
        "port": view.port,
        "token": view.token,
        "round": round_json(state, round_) if round_ is not None else None,
        "commentable": round_ is not None and round_.open,
        "blocked": blocked,
    }
    # The URL first and alone: a consumer that places this view reads one line.
    lines = [view.url]
    if round_ is not None:
        lines.append(
            f"serving {target.key} — {round_.id} ({round_.status}, base {_short(round_.base)})"
        )
    else:
        lines.append(f"serving {target.key}")
    lines.append(f"store  {target.store.root}")
    if blocked:
        lines.append(f"note   {blocked}")
    lines.append("stop with ctrl-c — nothing is left running, the ledger has it all")

    def serve() -> None:
        if args.open:
            import webbrowser

            webbrowser.open(view.url)
        view.serve_forever()

    return payload, lines, serve


# -- parser --------------------------------------------------------------


def _add_actor(parser: argparse.ArgumentParser) -> None:
    """The ``--actor`` flag the thread verbs share."""
    parser.add_argument(
        "--actor",
        choices=ACTORS,
        help=(
            f"which kind of participant is deciding this, person or agent "
            f"(default: ${ACTOR_ENV}, else {HUMAN})"
        ),
    )


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--json",
        action="store_true",
        help="structured output on stdout instead of a table (errors stay on stderr)",
    )
    common.add_argument(
        "--store",
        metavar="DIR",
        help=(
            "use this store directory instead of the resolved one; document keys "
            "count from its parent (docs/ledger-format.md §1.2)"
        ),
    )

    writing = argparse.ArgumentParser(add_help=False)
    writing.add_argument(
        "--author",
        metavar="NAME",
        help=f"who is recording this, person or agent (default: ${AUTHOR_ENV}, else the login name)",
    )

    parser = _Parser(
        prog="specround",
        description="Spec review rounds for humans and agents — anchored comments, "
        "an append-only ledger, no server and no git.",
        epilog="Exit codes: 0 ok · 2 fix the command · 3 the history refuses · 1 other.",
    )
    parser.add_argument("--version", action="version", version=f"specround {__version__}")
    verbs = parser.add_subparsers(dest="verb", required=True, metavar="VERB")

    rounds = verbs.add_parser("round", help="open, close, and inspect review rounds")
    round_verbs = rounds.add_subparsers(dest="round_verb", required=True, metavar="ACTION")

    opener = round_verbs.add_parser(
        "open",
        parents=[common, writing],
        help="freeze the document as a new round's base",
    )
    opener.add_argument("doc")
    opener.add_argument("--title", default="", help="a name for this round")
    opener.set_defaults(handler=_round_open, verb_name="round.open")

    closer = round_verbs.add_parser(
        "close", parents=[common, writing], help="close the open round"
    )
    closer.add_argument("doc")
    closer.add_argument("--round", metavar="ID", help="close this round rather than the open one")
    closer.add_argument("--note", default="", help="a closing note")
    closer.add_argument(
        "--allow-unresolved",
        action="store_true",
        help="close over undisposed comments, recording which ones were left",
    )
    closer.set_defaults(handler=_round_close, verb_name="round.close")

    status = round_verbs.add_parser(
        "status", parents=[common], help="rounds, counts, and what is outstanding"
    )
    status.add_argument("doc")
    status.set_defaults(handler=_round_status, verb_name="round.status")

    comment = verbs.add_parser(
        "comment", parents=[common, writing], help="add a comment to the open round"
    )
    comment.add_argument("doc")
    comment.add_argument("--quote", help="anchor the comment to this text in the round's base")
    comment.add_argument(
        "--occurrence",
        type=int,
        metavar="N",
        help="which appearance of --quote to anchor to, 0-based (required when it repeats)",
    )
    comment.add_argument("--body", help="the comment text")
    comment.add_argument(
        "--body-file", metavar="PATH", help="read the comment text from a file, or - for stdin"
    )
    comment.add_argument("--round", metavar="ID", help="comment on this round rather than the open one")
    comment.set_defaults(handler=_comment_add, verb_name="comment")

    answer = verbs.add_parser(
        "reply", parents=[common, writing], help="answer a comment, continuing its thread"
    )
    answer.add_argument("doc")
    answer.add_argument(
        "--comment", required=True, metavar="ID", help="comment id, or a prefix of one"
    )
    answer.add_argument("--body", help="the reply text")
    answer.add_argument(
        "--body-file", metavar="PATH", help="read the reply text from a file, or - for stdin"
    )
    answer.set_defaults(handler=_reply, verb_name="reply")

    closing = verbs.add_parser(
        "resolve", parents=[common, writing], help="close a thread — this conversation is over"
    )
    closing.add_argument("doc")
    closing.add_argument(
        "--comment", required=True, metavar="ID", help="thread root id, or a prefix of one"
    )
    closing.add_argument("--note", default="", help="a closing note")
    _add_actor(closing)
    closing.set_defaults(handler=_resolve, verb_name="resolve")

    reopening = verbs.add_parser(
        "reopen", parents=[common, writing], help="re-open a thread that was closed too early"
    )
    reopening.add_argument("doc")
    reopening.add_argument(
        "--comment", required=True, metavar="ID", help="thread root id, or a prefix of one"
    )
    reopening.add_argument(
        "--why", required=True, help="the reason — required, this overturns a recorded decision"
    )
    _add_actor(reopening)
    reopening.set_defaults(handler=_reopen, verb_name="reopen")

    listing = verbs.add_parser(
        "comments", parents=[common], help="list threads with their replies and disposition"
    )
    listing.add_argument("doc")
    listing.add_argument("--round", metavar="ID", help="only comments in this round")
    listing.add_argument(
        "--unresolved", action="store_true", help="only comments still owed an answer"
    )
    listing.add_argument(
        "--all",
        action="store_true",
        help="include resolved threads, which the default view hides (they are never deleted)",
    )
    listing.set_defaults(handler=_comments, verb_name="comments")

    rebind = verbs.add_parser(
        "reanchor",
        parents=[common, writing],
        help="carry every anchored comment onto the document as it is now",
    )
    rebind.add_argument("doc")
    rebind.set_defaults(handler=_reanchor, verb_name="reanchor")

    dispose = verbs.add_parser(
        "dispose", parents=[common, writing], help="settle a comment, with a reason"
    )
    dispose.add_argument("doc")
    dispose.add_argument("--comment", required=True, metavar="ID", help="comment id, or a prefix of one")
    dispose.add_argument(
        "--as",
        dest="verdict",
        required=True,
        choices=VERDICT_CHOICES,
        help="applied · rejected · answered · deferred (held is accepted for deferred)",
    )
    dispose.add_argument("--why", required=True, help="the reason — required for every verdict")
    dispose.set_defaults(handler=_dispose, verb_name="dispose")

    importing = verbs.add_parser(
        "import",
        parents=[common, writing],
        help="take in comments made in another tool, from a specround.import/v0 file",
    )
    importing.add_argument("doc")
    importing.add_argument(
        "--file",
        required=True,
        metavar="PATH",
        help="the import JSON (docs/import-format.md), or - for stdin",
    )
    importing.add_argument(
        "--round", metavar="ID", help="import into this round rather than the open one"
    )
    importing.add_argument(
        "--apply",
        action="store_true",
        help="record the comments (without this, the plan is printed and nothing is written)",
    )
    importing.set_defaults(handler=_import, verb_name="import")

    viewing = verbs.add_parser(
        "view",
        parents=[common, writing],
        help="serve the document to a browser — render, raw, and round diff",
    )
    viewing.add_argument("doc")
    viewing.add_argument(
        "--port", type=int, default=0, metavar="N", help="pin the port (default: any free one)"
    )
    viewing.add_argument(
        "--host", default=DEFAULT_HOST, help=f"address to bind (default: {DEFAULT_HOST})"
    )
    viewing.add_argument("--round", metavar="ID", help="write to this round rather than the open one")
    viewing.add_argument(
        "--open",
        action="store_true",
        help="also open a browser (off by default: the URL goes to stdout for an embedder)",
    )
    _add_actor(viewing)
    viewing.set_defaults(handler=_view, verb_name="view")

    return parser


# -- entry point ---------------------------------------------------------


def _dump(payload: Mapping[str, Any]) -> str:
    # Sorted keys and no ASCII escaping, like the ledger: a parser wants stable
    # shape, and a person reading Korean in a body wants to read it.
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except ArgvError as exc:
        return _refuse_argv(argv, exc)
    except SystemExit as exc:
        # argparse already printed the diagnosis; returning the code instead of
        # letting it escape keeps main() callable as a function. --help and
        # --version leave None (success); anything argparse refuses is a 2, and a
        # non-integer code would only ever be a message, which is not a verdict.
        if exc.code is None:
            return OK
        return exc.code if isinstance(exc.code, int) else USAGE

    verb = getattr(args, "verb_name", args.verb)
    try:
        payload, lines, *rest = args.handler(args)
    except (UsageError, BatchError) as exc:
        # A malformed import file is the caller's to fix, like a malformed
        # command line — the history has not been consulted yet and refuses
        # nothing. Per-item refusals are a different thing entirely and never
        # arrive here; they are part of a successful plan.
        return _fail(args, verb, exc, USAGE, "usage")
    except InvariantError as exc:
        return _fail(args, verb, exc, STATE, "state")
    except (SpecroundError, OSError) as exc:
        return _fail(args, verb, exc, FAILURE, "error")

    if args.json:
        print(_dump({"schema": CLI_SCHEMA, "verb": verb, **payload}))
    else:
        for line in lines:
            print(line)

    # A verb may hand back something to do once its output has been delivered.
    # ``view`` serves until interrupted, and its URL is the whole point of the
    # invocation — printing it after the server stops would make the one line a
    # caller needs arrive when it is no longer true.
    after = rest[0] if rest else None
    if after is not None:
        sys.stdout.flush()
        try:
            after()
        except KeyboardInterrupt:
            print("specround: stopped", file=sys.stderr)
    return OK


def _refuse_argv(argv: Sequence[str] | None, exc: ArgvError) -> int:
    """Report a command line argparse would not take, in the asked-for shape.

    ``--json`` is read off the raw argv because parsing is what just failed —
    there is no namespace to ask. Without it the output is what argparse would
    have printed, so a shell user sees no change.
    """
    words = list(argv) if argv is not None else sys.argv[1:]
    if "--json" in words:
        message = _dump(
            {
                "schema": CLI_SCHEMA,
                "verb": exc.verb,
                "error": {"kind": "usage", "exit": USAGE, "message": str(exc)},
            }
        )
    else:
        message = f"{exc.usage}{exc.prog}: error: {exc}"
    print(message, file=sys.stderr)
    return USAGE


def _fail(args: argparse.Namespace, verb: str, exc: Exception, code: int, kind: str) -> int:
    """Report a failure on stderr and hand back the exit code that judges it."""
    if getattr(args, "json", False):
        message = _dump(
            {
                "schema": CLI_SCHEMA,
                "verb": verb,
                "error": {"kind": kind, "exit": code, "message": str(exc)},
            }
        )
    else:
        message = f"specround: {exc}"
    print(message, file=sys.stderr)
    return code


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
