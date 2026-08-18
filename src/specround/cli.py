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
from specround.critic import COMMENT, DELETE, INSERT, Annotation, MarkupError
from specround.diffs import diff
from specround.errors import AnchorError, InvariantError, SpecroundError
from specround.events import ACTORS, ANSWERED, APPLIED, DEFERRED, HUMAN, REJECTED
from specround.fold import OPEN, Comment, Round, State
from specround.imports import BatchError, apply_plan, load_batch, parse_text, plan_import
from specround.locations import canonical_path
from specround.reanchor import FUZZY
from specround.store import HarvestReport, Placement, ReanchorReport, ReviewStore
from specround.viewtokens import ROTATED, STORED, token_for
from specround.webview import (
    DEFAULT_HOST,
    DERIVED,
    FALLBACK,
    PINNED,
    SHARE_SCOPES,
    PortTaken,
    WebView,
)
from specround.wire import (
    anchor_json,
    carry_json,
    comment_json,
    comments_on,
    disposition_json,
    reply_json,
    round_json,
    rounds_on,
)
from specround.workspace import MARKDOWN_SUFFIXES, Workspace

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


#: The verbs that open the document itself, and the whole of that list (G12).
#:
#: Everything else here is a conversation about a text the store already froze,
#: so it works whether or not the file is still on disk: a review outlives the
#: document it is a review of. These three do not, and each for a reason of its
#: own — ``round open`` freezes the text, ``harvest`` rewrites it, ``reanchor``
#: compares against it.
#:
#: A list rather than a habit, because the habit had a hole. Requiring the file
#: was :func:`_target`'s default and the read-only verbs opted out one at a
#: time, which left ``round close`` — pure ledger, never reads a byte of the
#: document — on the demanding side. A finished review whose document had been
#: withdrawn could then not be recorded as finished on any surface, and the
#: check it was stuck behind was satisfied by ``touch``ing an empty file. Naming
#: the three puts a new verb on the permissive side by default, which is where
#: all but three of them belong.
READS_THE_DOCUMENT = frozenset({"round.open", "harvest", "reanchor"})


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

    Every verb names a document, and a mistyped path that quietly reports "no
    comments" is worse than an error: the store is keyed by path, so a typo
    addresses a different (empty) history and the answer looks like a fact.

    ``must_exist`` is true only for :data:`READS_THE_DOCUMENT`, the three verbs
    that open the file. For the rest the missing file is not a typo — a document
    can be renamed or deleted while its history stays exactly where it was.
    :func:`_target` finishes that check against the store, because the thing
    that separates a rename from a typo is whether there is any history behind
    the path.
    """
    path = Path(value).expanduser()
    if must_exist and not path.is_file():
        raise UsageError(f"{path}: not a file — this verb reads the document itself")
    return canonical_path(path)


def _target(args: argparse.Namespace) -> Target:
    """The document this invocation is about, and whether it has to be on disk.

    The answer comes from :data:`READS_THE_DOCUMENT` and the verb's own name,
    not from what each handler remembered to pass. Which verbs need the file is
    one fact, and a fact each of fourteen call sites restates is a fact that
    drifts — it did, and ``round close`` was the verb it drifted on.
    """
    reads = getattr(args, "verb_name", None) in READS_THE_DOCUMENT
    path = _document(args.doc, must_exist=reads)
    store = ReviewStore.for_document(path, store=Path(args.store) if args.store else None)
    key = store.doc_key(path)
    if not reads and not path.is_file() and not _has_history(store, key):
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


def _standing_lines(standing: Mapping[str, Any]) -> list[str]:
    """:func:`_standing` in words, and only when there is something to say.

    A file that matches its base is the quiet case and stays quiet. The other
    two are not decoration: "+12 / -4 past this round's base" is how a reader
    who has been revising sees that the snapshot under the review is not what
    they have been editing, and "no longer on disk" is the state that used to
    reach them as a tooltip on a disabled button, if at all.
    """
    if not standing["present"]:
        return ["document  no longer on disk — the review below is the record of it"]
    if standing["matches"] is False:
        return [
            f"document  +{standing['added']} / -{standing['removed']} past this round's base "
            "(the base is what comments anchor in)"
        ]
    return []


def _standing(target: Target, rounds: list[Round]) -> dict[str, Any]:
    """Where the *document* stands against the text the review is about.

    Counts say where the conversation stands and said nothing about this, which
    left the two states a reader most needs indistinguishable from the ordinary
    one: a file that has moved past the base (normal, and the whole point of a
    revision) and a file that is not there any more (something to go and look
    at). Both read as silence.

    The comparison is against the newest round's base, open or closed, because
    that is the snapshot the review is of. ``matches`` is ``None`` when there is
    nothing to compare — no round yet, or no file — and the two are told apart
    by ``present`` rather than by folding them into one word.
    """
    absent = {"present": False, "matches": None, "added": 0, "removed": 0}
    if not target.path.is_file():
        return absent
    try:
        live = target.path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        # Unreadable is not missing, and neither is a comparison. Reporting it
        # as present with nothing to say beats guessing which one it resembles.
        return {"present": True, "matches": None, "added": 0, "removed": 0}
    if not rounds:
        return {"present": True, "matches": None, "added": 0, "removed": 0}
    computed = diff(target.store.base_text(rounds[-1].id), live)
    return {
        "present": True,
        "matches": computed.identical,
        "added": computed.added,
        "removed": computed.removed,
    }


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
    # The THREAD column exists exactly when it distinguishes something: the
    # default listing hides resolved threads, so every row is an open one and
    # the column would be noise. Once --all puts resolved rows on the screen,
    # leaving them unmarked showed a closed conversation as an open one (the
    # 2026-08-08 report behind the two-axis split hit this table).
    threaded = any(comment.resolved for comment in comments)
    rows: list[list[str]] = []
    for comment in comments:
        row = [
            comment.id,
            comment.kind,
            # The table wants one word per comment, so "nobody decided yet" gets
            # spelled here. It is a rendering choice and stays in the renderer:
            # the fold reports ``verdict is None`` and lets each surface say that
            # in its own vocabulary.
            comment.verdict or OPEN,
            comment.author,
            _anchor_cell(comment),
            _clip(comment.patch or comment.body, _BODY_WIDTH),
        ]
        if threaded:
            row.insert(3, "resolved" if comment.resolved else "")
        rows.append(row)
        for reply in comment.replies:
            reply_row = [f"{_REPLY_INDENT}{reply.id}", "reply", "", reply.author, "", _clip(reply.body, _BODY_WIDTH)]
            if threaded:
                reply_row.insert(3, "")
            rows.append(reply_row)
    header = ["ID", "KIND", "STATE", "AUTHOR", "ANCHOR", "BODY"]
    if threaded:
        header.insert(3, "THREAD")
    lines = _table(header, rows)

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
    misplaced = [c.id for c in comments if c.misplaced]
    if misplaced:
        # The ANCHOR column above still shows the quote, because the quote is
        # real — it is the *place* that belongs to another text (I12). Saying so
        # here is what stops the column from reading as a placement anyone can
        # act on.
        footers.append(
            f"{len(misplaced)} misplaced — quoted from another text than this round's base, "
            f"not drawn: {', '.join(misplaced)}"
        )
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
    # Opening a round is what carries the comments already on the document into
    # its base, so it is also what has to report the carry. Silence here would
    # make the one act that moves anchors the only one nobody sees.
    carried = carry_json(state, target.store.carry_of(round_id))
    payload = {
        **target.envelope(),
        "round": round_json(state, round_),
        "carried": carried,
    }
    lines = [f"opened {round_id} on {target.key} (base {_short(round_.base)})"]
    if round_.title:
        lines.append(f"title  {round_.title}")
    lines.append(f"store  {target.store.root}")
    if carried["changed"] or carried["unchanged"]:
        lines.append("")
        lines.extend(_carry_lines(carried, "carried onto this base"))
    return payload, lines


def _round_close(args: argparse.Namespace) -> tuple[dict[str, Any], list[str]]:
    target = _target(args)
    state = target.store.fold()
    round_ = _live_round(state, target.key, args.round, verb="close")
    outstanding = sorted(c.id for c in state.undisposed_in(round_.id))
    if outstanding and not args.allow_undisposed:
        # The store refuses this too, and it stays the gate. This check exists
        # only to name the flag a shell caller actually has — the library's own
        # message names its keyword argument, which is right there and wrong here.
        #
        # It says *undisposed*, and the second sentence says why: the reader who
        # hits this has often just resolved the thread and needs to know that
        # ending the talk was not the thing being asked for.
        raise InvariantError(
            f"round {round_.id} has {len(outstanding)} undisposed comment(s) "
            f"({', '.join(outstanding)}): dispose them, or pass --allow-undisposed "
            "to record that they were left open. Resolving the thread does not "
            "count — that closes the conversation, this asks for a verdict"
        )
    close_id = target.store.close_round(
        round_.id,
        author=_author(args),
        allow_undisposed=args.allow_undisposed,
        note=args.note or None,
    )
    state = target.store.fold()
    closed = state.rounds[round_.id]
    payload = {
        **target.envelope(),
        "round": round_json(state, closed),
        "close": close_id,
        "undisposed": list(closed.undisposed_at_close),
    }
    lines = [f"closed {round_.id} on {target.key}"]
    if closed.undisposed_at_close:
        lines.append(
            f"left undisposed ({len(closed.undisposed_at_close)}): "
            + ", ".join(closed.undisposed_at_close)
        )
    return payload, lines


def _round_status(args: argparse.Namespace) -> tuple[dict[str, Any], list[str]]:
    """Where the whole review stands, with the two outstanding axes kept apart.

    ``undisposed`` counts comments nobody has given a verdict; ``unresolved``
    counts conversations nobody has ended. They were one number under one word
    once, and the word was the one the ``resolve`` verb uses — so resolving a
    thread and watching the count hold still read as the tool ignoring the
    command, when it was answering a different question. Both are reported,
    named for the verb that moves each.
    """
    # A document that was renamed or deleted still has history, and the CLI is
    # the way to it (G12). _target refuses a path with nothing behind it.
    target = _target(args)
    state = target.store.fold()
    rounds = rounds_on(state, target.key)
    comments = comments_on(state, target.key)
    undisposed = [c.id for c in comments if c.undisposed]
    unresolved_threads = [c.id for c in comments if not c.resolved]
    orphans = [c.id for c in comments if c.orphaned]
    # I12, and a different question from the three above it: not "was it
    # answered", "can it be placed", or "is the talk over", but "do the offsets
    # we would draw belong to the text we would draw them on". It is a repair
    # backlog rather than review work, which is why it gets a number a person
    # can watch go to zero (``specround doctor``) instead of a listing.
    misplaced = [c.id for c in comments if c.misplaced]
    open_ids = [r.id for r in rounds if r.open]
    standing = _standing(target, rounds)
    payload = {
        **target.envelope(),
        "rounds": [round_json(state, r) for r in rounds],
        "document": standing,
        "open": open_ids,
        "undisposed": undisposed,
        "unresolved_threads": unresolved_threads,
        "orphans": orphans,
        "misplaced": misplaced,
        "counts": {
            "rounds": len(rounds),
            "comments": len(comments),
            "undisposed": len(undisposed),
            "unresolved_threads": len(unresolved_threads),
            "orphans": len(orphans),
            "misplaced": len(misplaced),
            "events": state.count,
        },
    }
    lines = [
        f"{target.key} — {len(rounds)} round(s), {len(open_ids)} open, "
        f"{len(comments)} comment(s), {len(undisposed)} undisposed, "
        f"{len(unresolved_threads)} unresolved thread(s), "
        f"{len(orphans)} orphaned",
        f"store  {target.store.root}",
    ]
    lines.extend(_standing_lines(standing))
    if misplaced:
        lines.append(
            f"{len(misplaced)} anchor(s) cut from another text than this round's base — "
            f"not drawn. Repair with 'specround doctor {target.key}'"
        )
    settled = [
        r.id
        for r in rounds
        if r.open
        and state.comments_in(r.id)
        and not any(c.undisposed for c in state.comments_in(r.id))
        and all(c.resolved for c in state.comments_in(r.id))
    ]
    for round_id in settled:
        # The question this answers is the one a reader asks out loud — "is it
        # finished?" — and it was answerable only by comparing two numbers to
        # zero and knowing which verb moves which.
        lines.append(
            f"{round_id} has nothing outstanding: every comment disposed, every thread "
            f"resolved. Record it with 'specround round close {target.key}'"
        )
    if rounds:
        lines.append("")
        lines.extend(
            _table(
                # The two columns sit side by side on purpose: adjacent numbers
                # that disagree are the shortest way to say they are two
                # questions, and this table is where the reader is looking.
                ["ROUND", "STATUS", "COMMENTS", "UNDISPOSED", "UNRESOLVED", "BASE", "TITLE"],
                [
                    [
                        r.id,
                        r.status,
                        str(len(state.comments_in(r.id))),
                        str(sum(1 for c in state.comments_in(r.id) if c.undisposed)),
                        str(sum(1 for c in state.comments_in(r.id) if not c.resolved)),
                        _short(r.base),
                        _clip(r.title, _BODY_WIDTH),
                    ]
                    for r in rounds
                ],
                right=frozenset({2, 3, 4}),
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
    # A document that was renamed or deleted still has history, and the CLI is
    # the way to it (G12). _target refuses a path with nothing behind it.
    target = _target(args)
    state = target.store.fold()
    items = comments_on(state, target.key)
    if args.round:
        chosen = state.rounds.get(args.round)
        if chosen is None or chosen.doc != target.key:
            raise UsageError(f"no round {args.round!r} on {target.key}")
        items = [c for c in items if c.round == args.round]
    if args.undisposed:
        items = [c for c in items if c.undisposed]
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


def _carry_lines(carried: Mapping[str, Any], headline: str) -> list[str]:
    strategies = carried["strategies"]
    ambiguous = carried["ambiguous"]

    def described(cid: str) -> str:
        marks = [m for m in (strategies.get(cid), "ambiguous" if cid in ambiguous else "") if m]
        return f"{cid} ({', '.join(marks)})" if marks else cid

    lines = [headline]
    for label, describe in (
        ("rebound", True),
        ("orphaned", False),
        ("unchanged", False),
        ("skipped", False),
    ):
        ids = carried[label]
        shown = ", ".join(described(c) if describe else c for c in ids)
        lines.append(f"  {label:<10}{len(ids):>3}  {shown}".rstrip())
    if ambiguous:
        lines.append("")
        lines.append(f"{len(ambiguous)} moved on a tie — worth a look: " + ", ".join(ambiguous))
    return lines


def _reanchor(args: argparse.Namespace) -> tuple[dict[str, Any], list[str]]:
    target = _target(args)
    report = target.store.reanchor_document(target.path, author=_author(args))
    carried = carry_json(target.store.fold(), report)
    payload = {**target.envelope(), **carried}
    return payload, _carry_lines(carried, f"re-anchored {target.key} against {_short(report.base)}")


def _doctor(args: argparse.Namespace) -> tuple[dict[str, Any], list[str]]:
    """Repair anchors that belong to another text than the base they are drawn on.

    Read-only by default for the reason ``harvest`` is: it writes to a history
    somebody else may be reading. Unlike ``harvest`` it never touches the
    document — and it does not read it either, so a file that has moved on or
    been deleted is no obstacle to fixing the ledger about it.
    """
    target = _target(args)
    report = target.store.repair_document(
        target.path, author=_author(args), apply=args.apply
    )
    payload = {
        **target.envelope(),
        "base": report.base,
        "applied": report.applied,
        "repaired": list(report.repaired),
        "orphaned": list(report.orphaned),
        "skipped": list(report.skipped),
        "strategies": dict(report.strategies),
        "reasons": dict(report.reasons),
    }
    if not report.found:
        settled = "nothing to repair" if not report.skipped else (
            f"nothing left to repair ({len(report.skipped)} already tried against this base)"
        )
        return payload, [f"{target.key} — {settled}"]

    verb = "repaired" if report.applied else "would repair"
    lines = [f"{target.key} against {_short(report.base)} — {verb}"]
    for cid in report.repaired:
        lines.append(f"  {cid}  re-read in this base ({report.strategies.get(cid, '?')})")
    for cid in report.orphaned:
        lines.append(f"  {cid}  orphaned — {_clip(report.reasons.get(cid, ''), _BODY_WIDTH)}")
    if not report.applied:
        lines.append("")
        lines.append("a dry run — pass --apply to append these corrections")
    return payload, lines


def _harvest(args: argparse.Namespace) -> tuple[dict[str, Any], list[str]]:
    """Take the inline markers out of the document and into the ledger (G6).

    A dry run by default, and this is the one verb where that is not caution for
    its own sake: it rewrites the reviewer's file. So the default answer is what
    *would* happen — every event, every anchor, and whether the document changes
    — and ``--apply`` is the sentence somebody typed.

    Both modes refuse the same things. A marker that cannot be placed in the
    round's base fails on the preview too, because a preview that is easier to
    pass than the real run is not a preview.
    """
    target = _target(args)
    state = target.store.fold()
    round_ = _live_round(state, target.key, args.round, verb="harvest into")
    try:
        report = target.store.harvest_document(
            target.path, round_.id, author=_author(args), apply=args.apply
        )
    except MarkupError as exc:
        # The document is what has to change, not the command line — but it is
        # still the caller's input, and 2 is the code that says "fix your input
        # and run it again". A 3 would send them to the ledger, which is fine.
        raise UsageError(str(exc)) from exc

    state = target.store.fold()
    payload = {
        **target.envelope(),
        "round": round_json(state, round_),
        "base": report.base,
        "applied": report.applied,
        "rewrite": report.rewrite,
        "annotations": [_placement_json(p) for p in report.placements],
        "skipped": [
            {
                "reason": s.reason,
                "opener": s.opener,
                "start": s.start,
                "line": s.line,
                "text": s.text,
            }
            for s in report.skipped
        ],
        "counts": {
            "comments": len(report.comments),
            "suggestions": len(report.suggestions),
            "skipped": len(report.skipped),
        },
    }
    return payload, _harvest_lines(target, report)


def _placement_json(placement: Placement) -> dict[str, Any]:
    annotation = placement.annotation
    return {
        "kind": annotation.kind,
        "event": placement.event,
        "body": annotation.body,
        "removed": annotation.removed,
        "added": annotation.added,
        "anchor": anchor_json(placement.anchor),
        "strategy": placement.strategy,
        "ambiguous": placement.ambiguous,
        "line": annotation.source_line,
    }


def _harvest_detail(annotation: Annotation) -> str:
    """What this marker says, in one cell."""
    if annotation.kind == COMMENT:
        return _clip(annotation.body, _BODY_WIDTH)
    if annotation.kind == INSERT:
        return f"add {_clip(annotation.added, _QUOTE_WIDTH)!r}"
    if annotation.kind == DELETE:
        return f"remove {_clip(annotation.removed, _QUOTE_WIDTH)!r}"
    return f"{_clip(annotation.removed, _QUOTE_WIDTH)!r} → {_clip(annotation.added, _QUOTE_WIDTH)!r}"


def _harvest_lines(target: Target, report: HarvestReport) -> list[str]:
    """The preview — or the receipt, which is the same table with ids in it."""
    mood = "harvested" if report.applied else "would harvest"
    head = (
        f"{mood} {len(report.comments)} comment(s) and {len(report.suggestions)} "
        f"suggestion(s) from {target.key} into {report.round}"
    )
    if not report.found and not report.skipped:
        return [f"no inline annotations in {target.key}", f"store  {target.store.root}"]
    lines = [head]
    if report.found:
        lines.append("")
        lines.extend(
            _table(
                ["LINE", "KIND", "ANCHOR", "WHAT", "EVENT"],
                [
                    [
                        str(p.annotation.source_line),
                        p.annotation.kind,
                        _clip(p.anchor.exact, _QUOTE_WIDTH) if p.anchor.exact else "(point)",
                        _harvest_detail(p.annotation),
                        p.event or "—",
                    ]
                    for p in report.placements
                ],
                right=frozenset({0}),
            )
        )
    footers = []
    carried = [p for p in report.placements if p.carried]
    if carried:
        # The document had moved on as well, so these anchors are where the
        # ladder put them rather than where the marker was. §4's reason for
        # recording a strategy at all is that this is worth a person's time.
        footers.append(
            f"{len(carried)} carried into the base by the ladder — worth a look: "
            + ", ".join(
                f"line {p.annotation.source_line} ({p.strategy}"
                + (", ambiguous" if p.ambiguous else "")
                + ")"
                for p in carried
            )
        )
    if report.skipped:
        footers.append(f"{len(report.skipped)} marker(s) left in the document:")
        footers.extend(
            f"  line {s.line}  {s.reason:<13}{_clip(s.text, _BODY_WIDTH)}"
            for s in report.skipped
        )
    if report.found and not report.applied:
        footers.append(
            "nothing recorded and the file is untouched — re-run with --apply"
            if report.rewrite
            else "nothing recorded — re-run with --apply"
        )
    elif report.applied and report.rewrite:
        footers.append(f"rewrote {target.path} without the markers")
    if footers:
        lines.append("")
        lines.extend(footers)
    return lines


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
        comment.id,
        author=_author(args),
        verdict=verdict,
        reason=args.why,
        supersede=args.supersede,
    )
    state = target.store.fold()
    disposed = state.comments[comment.id]
    payload = {
        **target.envelope(),
        "comment": comment_json(disposed),
        "disposition": disposition_json(disposed.disposition),
    }
    current = disposed.disposition
    assert current is not None  # just appended
    # An overturn says so on the line that records it. Two verdicts on one
    # comment read as a contradiction unless the second one is marked as having
    # been meant, and the reader of this line is usually the person who typed it.
    overturned = " (superseding)" if current.supersede else ""
    return payload, [
        f"{disposed.id} {current.verdict}{overturned} by {current.author} — {current.reason}"
    ]


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

    A directory is the same verb over a tree (H15) and takes the branch below.
    """
    if Path(args.doc).expanduser().is_dir():
        return _view_workspace(args)
    target = _target(args)
    token, token_source = token_for(target.path, rotate=args.rotate_token)
    view = _bind(
        WebView(
            store=target.store,
            path=target.path,
            author=_author(args),
            actor=_actor(args),
            round_hint=args.round or None,
            host=args.host,
            port=args.port,
            token=token,
            share_scope=args.share or "",
        )
    )
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
        "port_source": view.port_source,
        "port_note": _port_note(view),
        "token": view.token,
        "token_source": token_source,
        "token_note": _token_note(token_source),
        "share": _share_payload(view),
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
    lines.append(_port_line(view))
    lines.append(_token_line(token_source))
    if view.share_scope:
        lines.append(_share_line(view))
    if blocked:
        lines.append(f"note   {blocked}")
    lines.append("stop with ctrl-c — nothing is left running, the ledger has it all")

    return payload, lines, _serving(view, args.open)


def _view_workspace(args: argparse.Namespace) -> tuple[dict[str, Any], list[str], Callable[[], None]]:
    """Serve a whole tree from one process — one server, a bar, three modes (H15).

    A spec is never one file. The workspace layer is navigation and nothing
    else: the document a request names decides which per-document projection
    answers it, so rounds, anchors, and the ledger stay exactly where they were.

    The view starts on the **first document in path order**. Not the most
    recently active one, tempting as that is: the only thing that could rank
    "recent" is a timestamp, and this project's timestamps order nothing (they
    are seconds off a clock nobody controls). A default that shuffles with the
    ledger is a default nobody can predict, and the bar is one click away.

    Two refusals, both because the alternative is a quiet wrong answer. A tree
    with no markdown in it has nothing to serve. And ``--round`` names a round,
    which belongs to one document — honouring it here would mean picking a
    document for the caller and hiding that under a flag about rounds.
    """
    root = canonical_path(Path(args.doc).expanduser())
    if args.round:
        raise UsageError(
            f"--round names a round, and a round belongs to one document — point view at "
            f"that file to use it, or drop the flag to serve {root}"
        )
    space = Workspace(root=root, store=Path(args.store) if args.store else None)
    listing = space.list()
    if not listing.documents:
        suffixes = " or ".join(MARKDOWN_SUFFIXES)
        raise UsageError(
            f"{root}: no markdown documents under it ({suffixes}, dotted names skipped) — "
            "there is nothing to review here"
        )
    opening = listing.documents[0]
    store = space.store_for(opening.path)
    # The tree, not the document it opens on — the same axis the port counts
    # from (``WebView.port_path``). Keying the token on the opening document
    # would give the workspace a URL whose halves disagree about what it is a
    # view of, and move one of them the day a file sorts before that one.
    token, token_source = token_for(root, rotate=args.rotate_token)
    view = _bind(
        WebView(
            store=store,
            path=opening.path,
            author=_author(args),
            actor=_actor(args),
            host=args.host,
            port=args.port,
            token=token,
            share_scope=args.share or "",
            workspace=space,
            doc=opening.key,
        )
    )
    counts = listing.to_json()["counts"]
    payload = {
        "doc": view.key,
        "path": str(opening.path),
        "store": str(store.root),
        "root": str(root),
        "url": view.url,
        "host": view.host,
        "port": view.port,
        "port_source": view.port_source,
        "port_note": _port_note(view),
        "token": view.token,
        "token_source": token_source,
        "token_note": _token_note(token_source),
        "share": _share_payload(view),
        "workspace": {**listing.to_json(), "selected": opening.key},
    }
    # The URL first and alone, exactly as the single-document view promises: a
    # consumer that places this view reads one line and does not learn a second
    # shape because the argument was a directory.
    lines = [
        view.url,
        f"serving {counts['documents']} document(s) under {root} — "
        f"{counts['active']} with review activity, {counts['undisposed']} undisposed",
        f"open   {opening.key}",
    ]
    stores = {document.store for document in listing.documents}
    if len(stores) == 1:
        lines.append(f"store  {stores.pop()}")
    else:
        # The default layout gives every document its own central store, so
        # naming one of them would be naming the wrong one for every other
        # document in the bar.
        lines.append(f"stores {len(stores)} — one per document, listed in the workspace payload")
    lines.append(_port_line(view))
    lines.append(_token_line(token_source))
    if view.share_scope:
        lines.append(_share_line(view))
    if listing.note:
        lines.append(f"note   {listing.note}")
    lines.append("stop with ctrl-c — nothing is left running, the ledger has it all")
    return payload, lines, _serving(view, args.open)


def _bind(view: WebView) -> WebView:
    """Take the port, translating the one refusal a caller can act on.

    A pinned port that is held is the caller's to fix, like a malformed
    command — so it is a ``2`` and not the ``1`` a bare ``OSError`` would get.
    The derived port never arrives here: it falls back instead, and says so.
    """
    try:
        return view.bind()
    except PortTaken as exc:
        raise UsageError(str(exc)) from exc


def _port_note(view: WebView) -> str | None:
    """Why this URL is not the one this document usually gets, or ``None``.

    Prose in the payload, like ``blocked`` beside it: a ``--json`` consumer never
    reads the printed lines, and a fallback that only showed up there would be a
    URL changing under an embedder with no field to explain it.
    """
    if view.port_source != FALLBACK:
        return None
    # Naming the likely holder earns its clause: on the port a document always
    # gets, the thing most likely to be sitting there is that document's own
    # earlier view. "Possibly" rather than "probably" — nothing here checked, and
    # a guess dressed as a finding is what this codebase refuses everywhere else.
    return (
        f"{view.wanted_port} is this document's usual port and something already holds it "
        f"({view.port_reason}) — possibly a view of this document that is still up. This one "
        "took a free port, so the URL differs from last time"
    )


def _port_line(view: WebView) -> str:
    """One line saying which port this is and whether it will come back.

    The question a reader has is "can I keep this URL", and each of the four
    answers is different. A fallback is the one that has to be loud: it is the
    case where the address moved without anybody asking it to.
    """
    note = _port_note(view)
    if note is not None:
        return f"port   {view.port} — {note}"
    if view.port_source == DERIVED:
        return f"port   {view.port} — derived from the path, so a restart lands here again"
    if view.port_source == PINNED:
        return f"port   {view.port} — pinned with --port"
    return f"port   {view.port} — a free port (--port 0), so a restart will land elsewhere"


def _token_note(source: str) -> str | None:
    """Why this URL is not the one this document usually gets, or ``None``.

    The token's half of :func:`_port_note`, and non-``None`` in the one case
    that matches a fallback: the address held but the grant did not, so a pane
    that kept the URL is now refused at it. Rotation is asked for rather than
    stumbled into, which is exactly why the caller has to be able to see that
    it happened without reading the printed lines.
    """
    if source != ROTATED:
        return None
    return (
        "the stored token was replaced with --rotate-token, so this URL is not the one the "
        "last view handed out — anything still holding that one gets 403 until it is given "
        "this URL instead"
    )


def _token_line(source: str) -> str:
    """One line saying whether the *whole* URL comes back, not just the port.

    A stable port under a token minted per start was a URL that returned to the
    right address and was refused there, which is the failure this pairs with
    :func:`_port_line` to close. Three answers, and the loud one is rotation.
    """
    if source == ROTATED:
        return f"token  rotated — {_token_note(source)}"
    if source == STORED:
        return "token  this document's own, so the URL is the one it was served on last time"
    return "token  new — this document had none stored; from here the URL comes back"


def _share_payload(view: WebView) -> dict[str, str] | None:
    """The share grant's half of the payload, or ``None`` for an unshared view."""
    if not view.share_scope:
        return None
    return {"url": view.share_url, "scope": view.share_scope}


def _share_line(view: WebView) -> str:
    """One line handing over the *other* URL, and saying what it is not.

    The scope is on the line because the person reading it is about to paste
    the URL somewhere, and "what can they do with it" is the question that
    decides where. The lifetimes are on it for the same reason: a share dies
    with this process, and the owner URL is the one that does not.
    """
    what = "looks" if view.share_scope == "read" else "comments too"
    return (
        f"share  {view.share_url} — scope {view.share_scope} ({what}, never disposes); "
        "dies when this view stops, the owner URL above does not"
    )


def _serving(view: WebView, open_browser: bool) -> Callable[[], None]:
    """What to do once the URL has been delivered — the two views' one answer."""

    def serve() -> None:
        if open_browser:
            import webbrowser

            webbrowser.open(view.url)
        view.serve_forever()

    return serve


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
        "--allow-undisposed",
        action="store_true",
        help="close over comments with no verdict, recording which ones were left",
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
        "--undisposed", action="store_true", help="only comments still owed a verdict"
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
        help="re-drive the carry onto the base this document is painted on",
        description="Carry every anchored comment onto the base the round froze. Opening a "
        "round already does this, so this is the idempotent re-drive; once the file on disk "
        "has moved past that base it is refused, because nothing has frozen the revision.",
    )
    rebind.add_argument("doc")
    rebind.set_defaults(handler=_reanchor, verb_name="reanchor")

    mending = verbs.add_parser(
        "doctor",
        parents=[common, writing],
        help="repair anchors whose offsets were cut from some other text",
        description="Find anchors that do not hold in the base they are painted on (I12) and "
        "re-interpret their quote there, appending the correction. Written by older versions "
        "that re-anchored onto the live file. A dry run unless --apply.",
    )
    mending.add_argument("doc")
    mending.add_argument(
        "--apply",
        action="store_true",
        help="append the corrections (default: report what they would be)",
    )
    mending.set_defaults(handler=_doctor, verb_name="doctor")

    reaping = verbs.add_parser(
        "harvest",
        parents=[common, writing],
        help="take CriticMarkup markers out of the document and into the ledger",
        description="Read {>>comments<<}, {++insertions++}, {--deletions--} and "
        "{~~substitutions~>replacements~~} out of the document, record them against the "
        "open round, and rewrite the file without them. A dry run unless --apply.",
    )
    reaping.add_argument("doc")
    reaping.add_argument(
        "--round", metavar="ID", help="harvest into this round rather than the open one"
    )
    reaping.add_argument(
        "--apply",
        action="store_true",
        help="record the annotations and rewrite the document (default: preview only)",
    )
    reaping.set_defaults(handler=_harvest, verb_name="harvest")

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
    dispose.add_argument(
        "--supersede",
        action="store_true",
        help="overturn a verdict that already settled this comment "
        "(deferred needs no flag — completing it later is the ordinary path)",
    )
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
        help="serve a document — or a whole directory of them — to a browser",
        description=(
            "Serve a document to a browser: render, raw, and diff over one anchor "
            "space. The URL is the first line of stdout and no browser is opened. "
            "The same document comes back on the same URL, so a pane holding it "
            "survives a restart. Rounds are resolved per request, not at startup — "
            "closing a round and opening the next one reaches a running view, and "
            "restarting to pick one up is never necessary. While a round is open, "
            "render and raw show the base it froze (that is what a comment anchors "
            "against); edits to the file show in the diff mode until the next round "
            "freezes them."
        ),
    )
    viewing.add_argument(
        "doc",
        metavar="DOC|DIR",
        help=(
            "the document to serve, or a directory: one server for the tree, with a "
            "bar listing its markdown and the review each document has"
        ),
    )
    viewing.add_argument(
        "--port",
        type=int,
        default=None,
        metavar="N",
        help=(
            "pin the port; 0 asks for any free one (default: derived from the "
            "document's path, and with the token kept beside it a restart keeps "
            "the whole URL)"
        ),
    )
    viewing.add_argument(
        "--host", default=DEFAULT_HOST, help=f"address to bind (default: {DEFAULT_HOST})"
    )
    viewing.add_argument(
        "--rotate-token",
        action="store_true",
        help=(
            "issue a new token and store it, refusing the URL the last view handed "
            "out (the token is otherwise this document's, like the port)"
        ),
    )
    viewing.add_argument(
        "--share",
        choices=SHARE_SCOPES,
        help=(
            "also mint a weaker second URL to hand to everyone else: 'read' looks, "
            "'comment' also speaks, neither disposes. The share is not stored — "
            "restarting the view revokes every share at once, and the owner URL "
            "survives the same restart"
        ),
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
