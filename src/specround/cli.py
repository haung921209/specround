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

The verbs are deliberately few. Rounds, comments, re-anchoring, and dispositions
are the loop the ledger already knows how to enforce; suggestions and resolve
are their own items and get their own verbs when they land.
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from specround import __version__
from specround.anchors import Anchor
from specround.errors import AnchorError, InvariantError, SpecroundError
from specround.events import ANSWERED, APPLIED, DEFERRED, REJECTED
from specround.fold import Comment, Disposition, Round, State
from specround.locations import canonical_path
from specround.store import ReviewStore

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

#: How much of a quote or body a table cell shows before it is clipped.
_QUOTE_WIDTH = 28
_BODY_WIDTH = 44
#: How much of a snapshot digest human output prints.
_REF_CHARS = 12


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
    return store.ledger.exists() and bool(_rounds_on(store.fold(), key))


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


def _rounds_on(state: State, key: str) -> list[Round]:
    return [r for r in state.rounds.values() if r.doc == key]


def _comments_on(state: State, key: str) -> list[Comment]:
    return [c for c in state.comments.values() if state.rounds[c.round].doc == key]


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
    live = [r for r in _rounds_on(state, key) if r.open]
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
    scoped = {c.id for c in _comments_on(state, key)}
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


def _body(args: argparse.Namespace) -> str:
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
            "a comment needs a body: --body TEXT, --body-file PATH, or --body-file -"
        )
    text = text.strip()
    if not text:
        raise UsageError("the comment body is empty")
    return text


def _occurrences(text: str, quote: str) -> int:
    """How many appearances of ``quote`` ``--occurrence`` can address.

    Stepping by one character rather than by the length of the quote, because
    that is exactly how :func:`~specround.anchors.anchor_for_quote` walks them:
    ``str.count`` skips overlaps, so ``"aa"`` in ``"aaa"`` would read as unique
    here and still be addressable as occurrence 1 there. A count that disagrees
    with the indexer is a count that lets the ambiguity check wave through the
    one case it exists to catch.
    """
    total = 0
    at = text.find(quote)
    while at != -1:
        total += 1
        at = text.find(quote, at + 1)
    return total


def _anchor(store: ReviewStore, round_: Round, quote: str, occurrence: int | None) -> Anchor:
    """Cut an anchor out of the round's base — the text the reviewer read.

    Not the live document: a round's base is frozen (G2), so a quote taken from
    a file that has since been revised has to fail loudly rather than land on
    whatever the same offsets point at now.
    """
    if not quote:
        raise UsageError("--quote must not be empty")
    text = store.base_text(round_.id)
    total = _occurrences(text, quote)
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


def _anchor_json(anchor: Anchor | None) -> dict[str, Any] | None:
    return anchor.to_json() if anchor is not None else None


def _disposition_json(disposition: Disposition | None) -> dict[str, Any] | None:
    if disposition is None:
        return None
    return {
        "id": disposition.id,
        "author": disposition.author,
        "ts": disposition.ts,
        "verdict": disposition.verdict,
        "reason": disposition.reason,
    }


def _comment_json(comment: Comment) -> dict[str, Any]:
    """One comment, with both anchors and every disposition it has collected.

    ``anchor`` is where the comment was made and never changes; ``current_anchor``
    is where it lives now after any re-anchoring. Both are here because a reader
    that only had the second could not tell a comment that moved from one that
    never did.
    """
    return {
        "id": comment.id,
        "round": comment.round,
        "kind": comment.kind,
        "author": comment.author,
        "ts": comment.ts,
        "body": comment.body,
        "patch": comment.patch,
        "anchor": _anchor_json(comment.anchor),
        "current_anchor": _anchor_json(comment.current_anchor),
        "state": comment.state,
        "unresolved": comment.unresolved,
        "orphaned": comment.orphaned,
        "replies": [
            {"id": r.id, "author": r.author, "ts": r.ts, "body": r.body}
            for r in comment.replies
        ],
        "dispositions": [_disposition_json(d) for d in comment.dispositions],
    }


def _round_json(state: State, round_: Round) -> dict[str, Any]:
    comments = state.comments_in(round_.id)
    return {
        "id": round_.id,
        "doc": round_.doc,
        "base": round_.base,
        "author": round_.author,
        "ts": round_.ts,
        "title": round_.title,
        "status": round_.status,
        "closed_by": round_.closed_by,
        "closed_ts": round_.closed_ts,
        "close_note": round_.close_note,
        "unresolved_at_close": list(round_.unresolved_at_close),
        "comment_count": len(comments),
        "unresolved_count": sum(1 for c in comments if c.unresolved),
    }


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


def _comment_rows(comments: Sequence[Comment]) -> list[str]:
    rows = [
        [
            c.id,
            c.kind,
            c.state,
            c.author,
            _anchor_cell(c),
            _clip(c.patch or c.body, _BODY_WIDTH),
        ]
        for c in comments
    ]
    lines = _table(["ID", "KIND", "STATE", "AUTHOR", "ANCHOR", "BODY"], rows)
    orphans = [c.id for c in comments if c.orphaned]
    if orphans:
        # A footer rather than a column: orphaning is rare, and widening every
        # row for it would cost the common case to report the uncommon one.
        lines.append("")
        lines.append(f"{len(orphans)} orphaned: {', '.join(orphans)}")
    return lines


# -- verbs ---------------------------------------------------------------


def _round_open(args: argparse.Namespace) -> tuple[dict[str, Any], list[str]]:
    target = _target(args)
    state = target.store.fold()
    live = [r for r in _rounds_on(state, target.key) if r.open]
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
    payload = {**target.envelope(), "round": _round_json(state, round_)}
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
        "round": _round_json(state, closed),
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
    rounds = _rounds_on(state, target.key)
    comments = _comments_on(state, target.key)
    unresolved = [c.id for c in comments if c.unresolved]
    orphans = [c.id for c in comments if c.orphaned]
    open_ids = [r.id for r in rounds if r.open]
    payload = {
        **target.envelope(),
        "rounds": [_round_json(state, r) for r in rounds],
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
    anchor = _anchor(target.store, round_, args.quote, args.occurrence) if args.quote else None
    comment_id = target.store.add_comment(
        round_.id, author=_author(args), body=body, anchor=anchor
    )
    state = target.store.fold()
    comment = state.comments[comment_id]
    payload = {**target.envelope(), "comment": _comment_json(comment)}
    where = f'on "{_clip(anchor.exact, _QUOTE_WIDTH)}"' if anchor else "on the document"
    return payload, [f"{comment_id} added to {round_.id} {where}"]


def _comments(args: argparse.Namespace) -> tuple[dict[str, Any], list[str]]:
    # Read-only: a document that was renamed or deleted still has history, and
    # the CLI is the way to it (B5). _target refuses a path with nothing behind it.
    target = _target(args, missing_ok=True)
    state = target.store.fold()
    items = _comments_on(state, target.key)
    if args.round:
        chosen = state.rounds.get(args.round)
        if chosen is None or chosen.doc != target.key:
            raise UsageError(f"no round {args.round!r} on {target.key}")
        items = [c for c in items if c.round == args.round]
    if args.unresolved:
        items = [c for c in items if c.unresolved]
    payload = {**target.envelope(), "comments": [_comment_json(c) for c in items]}
    if not items:
        return payload, [f"no comments on {target.key}"]
    return payload, _comment_rows(items)


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
        "comment": _comment_json(settled),
        "disposition": _disposition_json(settled.disposition),
    }
    current = settled.disposition
    assert current is not None  # just appended
    return payload, [f"{settled.id} {current.verdict} by {current.author} — {current.reason}"]


# -- parser --------------------------------------------------------------


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

    parser = argparse.ArgumentParser(
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

    listing = verbs.add_parser(
        "comments", parents=[common], help="list comments with their round and disposition"
    )
    listing.add_argument("doc")
    listing.add_argument("--round", metavar="ID", help="only comments in this round")
    listing.add_argument(
        "--unresolved", action="store_true", help="only comments still owed an answer"
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
        payload, lines = args.handler(args)
    except UsageError as exc:
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
    return OK


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
