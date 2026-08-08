"""The JSON shape of a folded review — one definition for every reader (G4).

The CLI's ``--json`` and the web view's API answer the same questions about the
same state, so the shape of that answer lives here instead of in either of them.
Two copies of "what a comment looks like on the wire" is the failure the ledger
format is built to avoid one layer down: a consumer handed the second shape has
no way to tell it apart from the first, and the two drift the moment one grows a
field.

Nothing here touches the filesystem or decides anything. It is a projection of
the :mod:`specround.fold` types onto plain dicts, so the same folded state
renders identically whether a shell or a browser asked for it.
"""

from __future__ import annotations

from typing import Any

from specround.anchors import Anchor
from specround.fold import Anchoring, Comment, Disposition, Reply, Resolution, Round, State

__all__ = [
    "EMPTY_SUMMARY",
    "anchor_json",
    "anchoring_json",
    "comment_json",
    "comments_on",
    "disposition_json",
    "document_summary",
    "reply_json",
    "resolution_json",
    "round_json",
    "rounds_on",
]

#: What a document with no history looks like, in the shape
#: :func:`document_summary` returns. A document nobody has reviewed is a normal
#: answer, not a missing one, and a listing that left it out would be a listing
#: of stores rather than of documents.
EMPTY_SUMMARY: dict[str, Any] = {
    "rounds": 0,
    "open_rounds": 0,
    "comments": 0,
    "undisposed": 0,
    "orphans": 0,
    "misplaced": 0,
    "resolved": 0,
    "last_activity": None,
}


def rounds_on(state: State, key: str) -> list[Round]:
    """Every round opened on one document, oldest first."""
    return [r for r in state.rounds.values() if r.doc == key]


def comments_on(state: State, key: str) -> list[Comment]:
    """Every comment on one document, across all its rounds."""
    return [c for c in state.comments.values() if state.rounds[c.round].doc == key]


def document_summary(state: State, key: str) -> dict[str, Any]:
    """One document seen from outside it: how much review, not what it says.

    This is what a listing of documents needs and the per-document payload does
    not answer — the counts are over *every* round on the document rather than
    the one a view is writing to. It lives here with the rest of the wire shapes
    because a navigation bar and a ``--json`` caller must not learn two different
    definitions of "undisposed"; the counts below are the same ones
    :func:`round_json` and :func:`comment_json` derive, read off the same fold.

    ``undisposed`` and ``resolved`` are the two axes a reader confuses, so both
    are here and neither is derived from the other: how many comments nobody has
    decided, and how many conversations somebody has ended.

    ``last_activity`` is the latest timestamp on anything recorded about this
    document. It is **display only**. The ledger is explicit that timestamps
    never order anything (a second's resolution and a clock nobody controls),
    so nothing here or above may sort by it — it answers "has this gone quiet",
    which is a question a reader asks and a machine must not.
    """
    rounds = rounds_on(state, key)
    comments = comments_on(state, key)
    stamps = [r.ts for r in rounds]
    for comment in comments:
        stamps.append(comment.ts)
        stamps.extend(reply.ts for reply in comment.replies)
        stamps.extend(d.ts for d in comment.dispositions)
        stamps.extend(r.ts for r in comment.resolutions)
        stamps.extend(a.ts for a in comment.anchorings)
    return {
        "rounds": len(rounds),
        "open_rounds": sum(1 for r in rounds if r.open),
        "comments": len(comments),
        "undisposed": sum(1 for c in comments if c.undisposed),
        "orphans": sum(1 for c in comments if c.orphaned),
        # Beside ``orphans`` because a reader scanning the bar would otherwise
        # read "0 orphaned" as "every comment can be shown". I12 is the other
        # way a comment ends up undrawable, and it is the one nobody expects.
        "misplaced": sum(1 for c in comments if c.misplaced),
        "resolved": sum(1 for c in comments if c.resolved),
        "last_activity": max(stamps) if stamps else None,
    }


def anchor_json(anchor: Anchor | None) -> dict[str, Any] | None:
    return anchor.to_json() if anchor is not None else None


def disposition_json(disposition: Disposition | None) -> dict[str, Any] | None:
    if disposition is None:
        return None
    return {
        "id": disposition.id,
        "author": disposition.author,
        "ts": disposition.ts,
        "verdict": disposition.verdict,
        "reason": disposition.reason,
    }


def anchoring_json(anchoring: Anchoring | None) -> dict[str, Any] | None:
    """The latest attempt to carry a comment onto a revision, or ``None``.

    ``reanchor`` reports this in the moment and the listing did not carry it at
    all, so a reviewer reading the list afterwards could not tell which
    comments had moved — least of all which ones moved on the ``fuzzy`` rung,
    the ones the format says a person is supposed to look at.
    """
    if anchoring is None:
        return None
    return {
        "id": anchoring.id,
        "author": anchoring.author,
        "ts": anchoring.ts,
        "base": anchoring.base,
        "anchor": anchor_json(anchoring.anchor),
        "strategy": anchoring.strategy,
        "ambiguous": anchoring.ambiguous,
        "reason": anchoring.reason,
        "orphaned": anchoring.orphaned,
    }


def reply_json(reply: Reply) -> dict[str, Any]:
    return {"id": reply.id, "author": reply.author, "ts": reply.ts, "body": reply.body}


def resolution_json(resolution: Resolution) -> dict[str, Any]:
    return {
        "id": resolution.id,
        "author": resolution.author,
        "actor": resolution.actor,
        "ts": resolution.ts,
        "resolved": resolution.resolved,
        "note": resolution.note,
    }


def comment_json(comment: Comment) -> dict[str, Any]:
    """One thread: the root comment, its replies, and everything decided about it.

    ``anchor`` is where the comment was made and never changes; ``current_anchor``
    is where it lives now after any re-anchoring. Both are here because a reader
    that only had the second could not tell a comment that moved from one that
    never did.

    ``strategy`` and ``ambiguous`` say *how* it got there, and they are on the
    comment rather than only in the output of the ``reanchor`` run that moved it.
    That distinction is the whole point of the closed strategy vocabulary (§4):
    ``fuzzy`` means the quoted text was rewritten and a person should look, and a
    reviewer reading the list a week later is exactly the person meant. Both come
    from the last attempt that *placed* the comment, so an orphan still reports
    how it reached the anchor it is keeping.

    The three axes are three separate keys, because they are three separate
    questions and collapsing any two would make the answer unreadable:
    ``undisposed`` (has anyone decided this?), ``orphaned`` (can it still be
    placed on the document?), ``resolved`` (is the conversation over?).

    ``misplaced`` is not a fourth axis but a warning about ``current_anchor``
    itself (I12): the offsets belong to some other text than the base this
    comment is painted on, so a consumer that draws them draws them on the wrong
    sentence. It reads ``False`` on a comment folded without a store, because
    only a store can open the snapshot to find out — see
    :attr:`~specround.fold.Comment.misplaced`.

    There is no ``unresolved`` key on a comment. The thread axis is ``resolved``
    and a single comment's answer to "is this conversation over" is one boolean,
    so a second key would only be the first one negated — under the word that
    used to mean the *other* axis. A consumer still reading ``unresolved`` gets
    a missing key, which is a bug it can see, rather than a boolean that quietly
    inverted.
    """
    placed = comment.current_anchoring
    return {
        "id": comment.id,
        "round": comment.round,
        "kind": comment.kind,
        "author": comment.author,
        "ts": comment.ts,
        "body": comment.body,
        "patch": comment.patch,
        "anchor": anchor_json(comment.anchor),
        "current_anchor": anchor_json(comment.current_anchor),
        "state": comment.state,
        "undisposed": comment.undisposed,
        "orphaned": comment.orphaned,
        "misplaced": comment.misplaced,
        "anchoring": anchoring_json(comment.anchoring),
        "ext": comment.ext,
        "strategy": placed.strategy if placed else None,
        "ambiguous": placed.ambiguous if placed else False,
        "resolved": comment.resolved,
        "replies": [reply_json(r) for r in comment.replies],
        "dispositions": [disposition_json(d) for d in comment.dispositions],
        "resolutions": [resolution_json(r) for r in comment.resolutions],
        "anchorings": [anchoring_json(a) for a in comment.anchorings],
    }


def round_json(state: State, round_: Round) -> dict[str, Any]:
    """One round, with both outstanding counts kept apart.

    ``undisposed_count`` is what ``round.close`` has to account for (I6);
    ``unresolved_thread_count`` is how much of the conversation this round
    started is still going. A round can close with the first at zero and the
    second not, which is ordinary — the fix landed and the talk carries on.
    """
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
        "undisposed_at_close": list(round_.undisposed_at_close),
        "ext": round_.ext,
        "comment_count": len(comments),
        "undisposed_count": sum(1 for c in comments if c.undisposed),
        "unresolved_thread_count": sum(1 for c in comments if not c.resolved),
    }
