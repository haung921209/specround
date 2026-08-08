"""Fold: determinism, the disposition state model, and the rules it enforces."""

import pytest

from specround.errors import InvariantError
from specround.events import SCHEMA
from specround.fold import fold
from specround.store import ReviewStore


def test_fold_is_deterministic_across_repeated_reads(store, doc, round_id):
    cid = store.add_comment(round_id, author="bob", body="why 30 seconds?")
    store.reply(cid, author="alice", body="proxy timeout")
    store.dispose(cid, author="alice", verdict="applied", reason="raised to 60")
    records = store.ledger.read()
    assert fold(records) == fold(records)
    # And re-reading from disk in a fresh store gives the same answer.
    assert ReviewStore.for_document(doc).fold() == store.fold()


def test_fold_ignores_timestamp_order(tmp_path, clock):
    """Order comes from seq, not ts — a clock that jumps changes nothing."""
    doc = tmp_path / "spec.md"
    doc.write_text("body\n", encoding="utf-8")
    store = ReviewStore.for_document(doc, clock=clock)
    round_id = store.open_round(doc, author="alice")
    cid = store.add_comment(round_id, author="bob", body="q", )
    ordered = store.fold()

    shuffled = []
    stamps = ["2031-01-01T00:00:00Z", "1999-01-01T00:00:00Z"]
    for record, stamp in zip(store.ledger.read(), stamps):
        shuffled.append({**record, "ts": stamp})
    reversed_time = fold(shuffled)
    assert list(reversed_time.comments) == list(ordered.comments)
    assert reversed_time.comments[cid].state == ordered.comments[cid].state


def test_open_rounds_and_undisposed_comments_are_what_fold_is_for(store, doc, round_id):
    first = store.add_comment(round_id, author="bob", body="why 30 seconds?")
    second = store.add_comment(round_id, author="carol", body="retries?")
    state = store.fold()
    assert [r.id for r in state.open_rounds] == [round_id]
    assert [c.id for c in state.undisposed] == [first, second]

    store.dispose(first, author="alice", verdict="applied", reason="raised to 60")
    assert [c.id for c in store.fold().undisposed] == [second]


def test_round_records_its_document_base_and_title(store, doc, round_id):
    round_ = store.fold().rounds[round_id]
    assert round_.doc == "spec.md"
    assert round_.base.startswith("sha256:")
    assert round_.title == "first pass"
    assert round_.open is True
    assert store.snapshots.get_text(round_.base) == doc.read_text(encoding="utf-8")


def test_a_round_base_does_not_follow_the_document(store, doc, round_id):
    original = doc.read_text(encoding="utf-8")
    doc.write_text("rewritten from scratch\n", encoding="utf-8")
    assert store.base_text(round_id) == original


def test_comment_carries_its_anchor(store, doc, round_id):
    anchor = store.anchor_in_round(round_id, "30 seconds")
    cid = store.add_comment(round_id, author="bob", body="too short", anchor=anchor)
    stored = store.fold().comments[cid]
    assert stored.anchor == anchor
    assert stored.kind == "comment"
    assert stored.patch is None


def test_an_anchor_is_verified_against_the_round_base(store, doc, round_id):
    """I7 on the way in — and it raises what I7 raises on the way out.

    One condition, one exception class, whichever side of the store the caller
    is standing on: the read path refuses the same anchor with the same error
    (``tests/test_anchor_integrity.py``). Two classes here would be the "two
    copies of a check" §6 warns about, wearing a different coat.
    """
    from specround.anchors import Anchor
    from specround.errors import InvariantError

    with pytest.raises(InvariantError, match="I7"):
        store.add_comment(
            round_id,
            author="bob",
            body="nope",
            anchor=Anchor(exact="not in the document", start=0, end=19),
        )
    assert store.fold().comments == {}


def test_an_anchor_is_verified_against_the_base_even_after_the_document_moves(store, doc, round_id):
    anchor = store.anchor_in_round(round_id, "30 seconds")
    doc.write_text("completely different text\n", encoding="utf-8")
    # The round froze its base; comments still land on what the reviewer read.
    cid = store.add_comment(round_id, author="bob", body="too short", anchor=anchor)
    assert store.fold().comments[cid].anchor == anchor


def test_suggestion_is_a_comment_whose_substance_is_a_patch(store, round_id):
    sid = store.add_suggestion(
        round_id,
        author="agent:reviewer",
        patch="-Timeouts are 30 seconds.\n+Timeouts are 60 seconds.\n",
        body="align with the proxy",
    )
    suggestion = store.fold().comments[sid]
    assert suggestion.kind == "suggestion"
    assert suggestion.patch.startswith("-Timeouts")
    assert suggestion.body == "align with the proxy"
    assert suggestion.undisposed is True


def test_replies_attach_in_order(store, round_id):
    cid = store.add_comment(round_id, author="bob", body="why 30 seconds?")
    store.reply(cid, author="alice", body="proxy limit")
    store.reply(cid, author="bob", body="then say so")
    replies = store.fold().comments[cid].replies
    assert [r.author for r in replies] == ["alice", "bob"]
    assert [r.body for r in replies] == ["proxy limit", "then say so"]


@pytest.mark.parametrize("verdict", ["applied", "rejected", "answered"])
def test_terminal_verdicts_settle_a_comment(store, round_id, verdict):
    cid = store.add_comment(round_id, author="bob", body="why?")
    store.dispose(cid, author="alice", verdict=verdict, reason="a reason")
    comment = store.fold().comments[cid]
    assert comment.settled is True
    assert comment.undisposed is False
    assert comment.state == verdict


def test_deferred_leaves_a_comment_outstanding(store, round_id):
    cid = store.add_comment(round_id, author="bob", body="retries?")
    store.dispose(cid, author="alice", verdict="deferred", reason="needs the retry spec")
    comment = store.fold().comments[cid]
    assert comment.state == "deferred"
    assert comment.undisposed is True
    assert [c.id for c in store.fold().undisposed] == [cid]


def test_a_deferred_comment_can_be_disposed_again(store, round_id):
    cid = store.add_comment(round_id, author="bob", body="retries?")
    store.dispose(cid, author="alice", verdict="deferred", reason="needs the retry spec")
    store.dispose(cid, author="alice", verdict="applied", reason="retry section added")
    comment = store.fold().comments[cid]
    assert comment.state == "applied"
    assert [d.verdict for d in comment.dispositions] == ["deferred", "applied"]
    assert store.fold().undisposed == []


def test_a_settled_comment_cannot_be_re_disposed(store, round_id):
    cid = store.add_comment(round_id, author="bob", body="why?")
    store.dispose(cid, author="alice", verdict="rejected", reason="out of scope")
    with pytest.raises(InvariantError, match="already settled as 'rejected'"):
        store.dispose(cid, author="alice", verdict="applied", reason="changed my mind")
    assert len(store.fold().comments[cid].dispositions) == 1


def test_every_disposition_carries_its_reason(store, round_id):
    cid = store.add_comment(round_id, author="bob", body="why?")
    store.dispose(cid, author="alice", verdict="answered", reason="see the reply")
    assert store.fold().comments[cid].disposition.reason == "see the reply"


def test_a_comment_must_name_a_live_round(store, round_id):
    with pytest.raises(InvariantError, match="unknown round"):
        store.add_comment("r-nonexistent", author="bob", body="why?")
    store.close_round(round_id, author="alice")
    with pytest.raises(InvariantError, match="is closed — open a new round"):
        store.add_comment(round_id, author="bob", body="late")


def test_a_disposition_must_name_a_known_comment(store, round_id):
    with pytest.raises(InvariantError, match="unknown comment"):
        store.dispose("c-nonexistent", author="alice", verdict="applied", reason="x")
    with pytest.raises(InvariantError, match="is a round, not a comment"):
        store.dispose(round_id, author="alice", verdict="applied", reason="x")


def test_a_reply_must_name_a_known_comment(store, round_id):
    with pytest.raises(InvariantError, match="unknown comment"):
        store.reply("c-nonexistent", author="alice", body="hello")


def test_closing_over_undisposed_comments_needs_an_explicit_decision(store, round_id):
    cid = store.add_comment(round_id, author="bob", body="retries?")
    with pytest.raises(InvariantError, match="1 undisposed comment"):
        store.close_round(round_id, author="alice")
    assert store.fold().rounds[round_id].open is True

    store.close_round(round_id, author="alice", allow_undisposed=True, note="next round")
    round_ = store.fold().rounds[round_id]
    assert round_.open is False
    assert round_.undisposed_at_close == [cid]
    assert round_.close_note == "next round"


def test_a_clean_close_records_nothing_left_open(store, round_id):
    cid = store.add_comment(round_id, author="bob", body="why?")
    store.dispose(cid, author="alice", verdict="applied", reason="fixed")
    close_id = store.close_round(round_id, author="alice")
    round_ = store.fold().rounds[round_id]
    assert round_.undisposed_at_close == []
    assert round_.closed_by == close_id
    assert round_.closed_ts is not None


def test_a_hand_written_close_cannot_hide_open_comments(store, round_id):
    cid = store.add_comment(round_id, author="bob", body="retries?")
    records = store.ledger.read()
    forged = {
        "schema": SCHEMA,
        "seq": len(records),
        "ts": "2020-01-01T00:00:09Z",
        "type": "round.close",
        "id": "x-f0f0f0f0f0f0",  # hand written, but shaped like a close id
        "author": "alice",
        "round": round_id,
    }
    # The rule lives in the format, not just in the convenience API: a close
    # that omits what it left open is invalid however it was produced.
    with pytest.raises(InvariantError, match=f"has \\['{cid}'\\] undisposed"):
        fold([*records, forged])


def test_a_round_cannot_be_closed_twice(store, round_id):
    store.close_round(round_id, author="alice")
    with pytest.raises(InvariantError, match="is closed"):
        store.close_round(round_id, author="alice")


def test_a_comment_in_a_closed_round_can_still_be_disposed(store, round_id):
    cid = store.add_comment(round_id, author="bob", body="retries?")
    store.close_round(round_id, author="alice", allow_undisposed=True)
    # Deferred work outlives the round it was raised in.
    store.dispose(cid, author="alice", verdict="applied", reason="landed later")
    assert store.fold().comments[cid].state == "applied"


def test_duplicate_event_ids_are_refused(store, round_id):
    records = store.ledger.read()
    duplicate = {**records[0], "seq": 1}
    with pytest.raises(InvariantError, match="duplicate event id"):
        fold([*records, duplicate])


def test_fold_rejects_a_gap_in_the_sequence(store, round_id):
    records = store.ledger.read()
    with pytest.raises(InvariantError, match="reordered or truncated"):
        fold([*records, {**records[0], "id": "r-aaaaaaaaaaaa", "seq": 7}])


def test_multiple_rounds_can_be_open_at_once(doc, clock):
    # Two documents in one ledger is what a folder store is for; the default
    # store is keyed by document, so this property is asked of the folder one.
    store = ReviewStore.at(doc.parent, clock=clock)
    other = doc.parent / "other.md"
    other.write_text("second document\n", encoding="utf-8")
    first = store.open_round(doc, author="alice")
    second = store.open_round(other, author="alice")
    state = store.fold()
    assert {r.id for r in state.open_rounds} == {first, second}
    assert {r.doc for r in state.open_rounds} == {"spec.md", "other.md"}
    # Comments name their round, so concurrent rounds are unambiguous.
    a = store.add_comment(first, author="bob", body="on the spec")
    b = store.add_comment(second, author="bob", body="on the other")
    assert [c.id for c in store.fold().undisposed_in(first)] == [a]
    assert [c.id for c in store.fold().undisposed_in(second)] == [b]


def test_state_helpers_navigate_the_graph(store, doc, round_id):
    cid = store.add_comment(round_id, author="bob", body="why?")
    state = store.fold()
    assert [c.id for c in state.comments_in(round_id)] == [cid]
    assert state.round_of(cid).id == round_id
    assert state.count == len(store.ledger.read())


def test_latest_round_filters_by_document(doc, clock):
    store = ReviewStore.at(doc.parent, clock=clock)
    other = doc.parent / "other.md"
    other.write_text("second document\n", encoding="utf-8")
    first = store.open_round(doc, author="alice")
    second = store.open_round(other, author="alice")
    assert store.latest_round().id == second
    assert store.latest_round(doc).id == first
    assert store.latest_round(other).id == second


def test_empty_store_has_no_latest_round(store):
    assert store.latest_round() is None
