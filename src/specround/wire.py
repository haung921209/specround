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
    "anchor_json",
    "anchoring_json",
    "comment_json",
    "comments_on",
    "disposition_json",
    "reply_json",
    "resolution_json",
    "round_json",
    "rounds_on",
]


def rounds_on(state: State, key: str) -> list[Round]:
    """Every round opened on one document, oldest first."""
    return [r for r in state.rounds.values() if r.doc == key]


def comments_on(state: State, key: str) -> list[Comment]:
    """Every comment on one document, across all its rounds."""
    return [c for c in state.comments.values() if state.rounds[c.round].doc == key]


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
    ``unresolved`` (has anyone decided this?), ``orphaned`` (can it still be
    placed on the document?), ``resolved`` (is the conversation over?).
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
        "unresolved": comment.unresolved,
        "orphaned": comment.orphaned,
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
        "ext": round_.ext,
        "comment_count": len(comments),
        "unresolved_count": sum(1 for c in comments if c.unresolved),
    }
