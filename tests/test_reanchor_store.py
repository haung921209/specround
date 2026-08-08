"""Carrying comments across a revision: what lands in the ledger, and what does not.

The unit under test here is not the matcher (``test_reanchor.py`` covers that)
but the bookkeeping around it — which passes append events, which stay quiet,
and what ``fold`` says afterwards.

**The carry happens when a round opens.** Freezing the revision is what creates
the space the comments have to move into, so it is also what moves them; a
separate verb the caller had to remember was a step that could be skipped, and
skipping it (or running it with nothing frozen) is what put twelve comments on
sentences they were not about. ``reanchor`` is still here as the idempotent
re-drive of that same carry, and ``test_anchor_space.py`` holds the refusal that
keeps it from inventing a space of its own.
"""

import pytest

from specround.errors import InvariantError, SpecroundError
from specround.events import ANCHOR_ORPHAN, ANCHOR_REANCHOR
from specround.reanchor import FUZZY, POSITION, QUOTE
from specround.store import ReviewStore

QUOTE_TEXT = "Timeouts are 30 seconds."


@pytest.fixture
def anchored_comment(store, doc, round_id):
    anchor = store.anchor_in_round(round_id, QUOTE_TEXT)
    return store.add_comment(round_id, author="bob", body="too short", anchor=anchor)


def revise(doc, old, new):
    doc.write_text(doc.read_text(encoding="utf-8").replace(old, new), encoding="utf-8")


def carry(store, doc, **kwargs):
    """Freeze the document as it is now and let the round opening carry the comments."""
    round_id = store.open_round(doc, author="agent:reanchor", **kwargs)
    return store.carry_of(round_id)


def kinds(store):
    return [record["type"] for record in store.ledger.read()]


# -- the happy path -------------------------------------------------------


def test_a_comment_follows_its_text_down_the_page(store, doc, anchored_comment, doc_text):
    doc.write_text("> Draft.\n\n" + doc_text, encoding="utf-8")
    report = carry(store, doc)

    assert report.rebound == [anchored_comment]
    assert report.orphaned == []
    assert kinds(store)[-1] == ANCHOR_REANCHOR

    comment = store.fold().comments[anchored_comment]
    assert comment.orphaned is False
    assert comment.current_anchor.exact == QUOTE_TEXT
    assert comment.current_anchor != comment.anchor  # it moved
    assert comment.anchoring.strategy == QUOTE
    comment.current_anchor.verify(doc.read_text(encoding="utf-8"))


def test_the_original_record_is_never_rewritten(store, doc, anchored_comment, doc_text):
    before = store.ledger.read()[1]
    doc.write_text("> Draft.\n\n" + doc_text, encoding="utf-8")
    carry(store, doc)
    assert store.ledger.read()[1] == before  # append-only, not update-in-place


def test_an_unchanged_document_writes_nothing(store, doc, anchored_comment):
    report = store.reanchor_document(doc, author="agent:reanchor")
    assert report.unchanged == [anchored_comment]
    assert report.changed is False
    assert kinds(store) == ["round.open", "comment.add"]


def test_a_second_pass_is_a_no_op(store, doc, anchored_comment, doc_text):
    doc.write_text("> Draft.\n\n" + doc_text, encoding="utf-8")
    carry(store, doc)
    count = store.ledger.count()

    again = store.reanchor_document(doc, author="agent:reanchor")
    assert again.changed is False
    assert store.ledger.count() == count


# -- orphans --------------------------------------------------------------


def test_a_deleted_span_is_recorded_as_an_orphan(store, doc, anchored_comment):
    revise(doc, QUOTE_TEXT, "")
    report = carry(store, doc)

    assert report.orphaned == [anchored_comment]
    assert kinds(store)[-1] == ANCHOR_ORPHAN

    comment = store.fold().comments[anchored_comment]
    assert comment.orphaned is True
    assert comment.anchoring.reason
    assert store.fold().orphans == [comment]
    assert store.orphans(doc) == [comment]


def test_an_orphan_keeps_its_last_good_anchor(store, doc, anchored_comment):
    """Orphaning reports that the text is gone; it does not forget where it was."""
    revise(doc, QUOTE_TEXT, "")
    carry(store, doc)
    comment = store.fold().comments[anchored_comment]
    assert comment.current_anchor is not None
    assert comment.current_anchor.exact == QUOTE_TEXT


def test_an_orphan_is_bound_again_when_the_text_comes_back(store, doc, anchored_comment, doc_text):
    revise(doc, QUOTE_TEXT, "")
    carry(store, doc)
    assert store.fold().comments[anchored_comment].orphaned is True

    doc.write_text(doc_text.replace("# Widget", "# Widget v2"), encoding="utf-8")
    report = carry(store, doc)

    assert report.rebound == [anchored_comment]
    comment = store.fold().comments[anchored_comment]
    assert comment.orphaned is False
    assert store.fold().orphans == []
    # The whole path is still in the log: orphaned, then found again.
    assert [step.orphaned for step in comment.anchorings] == [True, False]


def test_an_orphan_is_bound_again_when_the_text_comes_back_to_the_same_place(
    store, doc, anchored_comment, doc_text
):
    """A revert is the same event as any other restoration, and reads as one.

    The sibling test above restores the text somewhere else, so a rung past the
    first answers and appends. Put it back byte for byte — a ``git revert``, an
    undo, a paragraph pasted back where it came from — and rung 1 answers
    instead. "Nothing changed" is true of the anchor and false of the comment:
    it was orphaned a moment ago and it is placed now, and that is exactly the
    difference the ledger has to carry.
    """
    revise(doc, QUOTE_TEXT, "")
    carry(store, doc)
    assert store.fold().comments[anchored_comment].orphaned is True

    doc.write_text(doc_text, encoding="utf-8")  # byte-identical to the first base
    report = carry(store, doc)

    assert report.rebound == [anchored_comment]
    assert report.unchanged == []
    assert kinds(store)[-1] == ANCHOR_REANCHOR
    comment = store.fold().comments[anchored_comment]
    assert comment.orphaned is False
    assert comment.anchoring.strategy == POSITION
    assert store.fold().orphans == []
    assert store.orphans(doc) == []


def test_a_restored_comment_is_not_re_reported_on_the_next_pass(
    store, doc, anchored_comment, doc_text
):
    """Recording the restoration must not turn into recording it every pass."""
    revise(doc, QUOTE_TEXT, "")
    carry(store, doc)
    doc.write_text(doc_text, encoding="utf-8")
    carry(store, doc)
    count = store.ledger.count()

    again = store.reanchor_document(doc, author="agent:reanchor")
    assert again.changed is False
    assert store.ledger.count() == count


def test_a_placed_comment_at_its_old_offset_still_writes_nothing(
    store, doc, anchored_comment, doc_text
):
    """The quiet majority stays quiet: only a comment that *was* lost is news."""
    doc.write_text(doc_text, encoding="utf-8")
    report = carry(store, doc)
    assert report.unchanged == [anchored_comment]
    assert report.changed is False
    assert kinds(store) == ["round.open", "comment.add", "round.open"]


def test_an_orphan_is_not_re_reported_on_the_same_revision(store, doc, anchored_comment):
    revise(doc, QUOTE_TEXT, "")
    carry(store, doc)
    count = store.ledger.count()

    report = store.reanchor_document(doc, author="agent:reanchor")
    assert report.skipped == [anchored_comment]
    assert store.ledger.count() == count


def test_an_orphan_is_not_a_disposition(store, doc, anchored_comment):
    """Two axes: whether anyone answered it, and whether we can still place it."""
    revise(doc, QUOTE_TEXT, "")
    carry(store, doc)
    state = store.fold()
    assert state.orphans == state.undisposed  # both, right now

    store.dispose(anchored_comment, author="alice", verdict="rejected", reason="dropped")
    state = store.fold()
    assert [c.id for c in state.orphans] == [anchored_comment]
    assert state.undisposed == []  # settled, and still orphaned


# -- scope ----------------------------------------------------------------


def test_only_comments_on_that_document_are_touched(tmp_path, clock, doc_text):
    first = tmp_path / "one.md"
    second = tmp_path / "two.md"
    first.write_text(doc_text, encoding="utf-8")
    second.write_text(doc_text, encoding="utf-8")
    store = ReviewStore.at(tmp_path, clock=clock)

    rounds = {path: store.open_round(path, author="alice") for path in (first, second)}
    comments = {
        path: store.add_comment(
            rounds[path],
            author="bob",
            body="here",
            anchor=store.anchor_in_round(rounds[path], QUOTE_TEXT),
        )
        for path in (first, second)
    }

    first.write_text("> Draft.\n\n" + doc_text, encoding="utf-8")
    report = carry(store, first)
    assert report.rebound == [comments[first]]
    assert comments[second] not in report.rebound + report.unchanged + report.skipped


def test_a_whole_document_comment_has_nothing_to_re_anchor(store, doc, round_id, doc_text):
    unanchored = store.add_comment(round_id, author="bob", body="the tone is off")
    doc.write_text("> Draft.\n\n" + doc_text, encoding="utf-8")
    report = carry(store, doc)
    assert unanchored not in report.rebound + report.orphaned + report.unchanged


def test_suggestions_travel_the_same_way_as_comments(store, doc, round_id, doc_text):
    anchor = store.anchor_in_round(round_id, QUOTE_TEXT)
    suggestion = store.add_suggestion(round_id, author="agent:reviewer", patch="-30\n+60\n", anchor=anchor)
    doc.write_text("> Draft.\n\n" + doc_text, encoding="utf-8")
    report = carry(store, doc)
    assert report.rebound == [suggestion]


def test_the_carry_reaches_a_comment_whose_round_is_closed(
    store, doc, round_id, anchored_comment, doc_text
):
    """A comment outlives its round; the revision that moves it usually lands later."""
    store.dispose(anchored_comment, author="alice", verdict="deferred", reason="next round")
    store.close_round(round_id, author="alice", allow_undisposed=True)
    doc.write_text("> Draft.\n\n" + doc_text, encoding="utf-8")
    report = carry(store, doc)
    assert report.rebound == [anchored_comment]


def test_re_anchoring_a_missing_document_is_refused(store, tmp_path):
    with pytest.raises(SpecroundError, match="not a file"):
        store.reanchor_document(tmp_path / "nope.md", author="agent:reanchor")


# -- ambiguity is surfaced, not swallowed ---------------------------------


def test_an_ambiguous_move_is_flagged_in_the_ledger(tmp_path, clock):
    text = "x\n\nrepeat me\n\nx\n\nrepeat me\n\nx\n"
    path = tmp_path / "repeats.md"
    path.write_text(text, encoding="utf-8")
    store = ReviewStore.at(tmp_path, clock=clock)
    round_id = store.open_round(path, author="alice")
    # No context on the anchor: nothing tells the two occurrences apart.
    from specround.anchors import Anchor

    at = text.index("repeat me")
    comment = store.add_comment(
        round_id,
        author="bob",
        body="which one?",
        anchor=Anchor(exact="repeat me", start=at, end=at + 9),
    )

    path.write_text("pad\n" + text, encoding="utf-8")
    report = carry(store, path)

    assert report.ambiguous == [comment]
    assert store.ledger.read()[-1]["ambiguous"] is True


def test_an_unambiguous_move_omits_the_flag(store, doc, anchored_comment, doc_text):
    doc.write_text("> Draft.\n\n" + doc_text, encoding="utf-8")
    carry(store, doc)
    assert "ambiguous" not in store.ledger.read()[-1]


# -- the similarity floor is the caller's dial ----------------------------


def test_the_floor_decides_between_a_fuzzy_move_and_an_orphan(store, doc, anchored_comment):
    revise(doc, QUOTE_TEXT, "Timeouts are 45 seconds.")
    report = carry(store, doc, min_similarity=0.99)
    assert report.orphaned == [anchored_comment]

    doc.write_text(doc.read_text(encoding="utf-8") + "\ntrailing\n", encoding="utf-8")
    report = carry(store, doc, min_similarity=0.5)
    assert report.rebound == [anchored_comment]
    assert store.fold().comments[anchored_comment].anchoring.strategy == FUZZY


# -- ledger invariants ----------------------------------------------------


def test_an_anchor_event_must_target_a_real_comment(store, doc, round_id):
    with pytest.raises(InvariantError, match="unknown comment"):
        store.ledger.append(
            {
                "type": ANCHOR_ORPHAN,
                "author": "agent:reanchor",
                "target": "c-nope",
                "base": "sha256:" + "0" * 64,
                "reason": "gone",
            }
        )


def test_an_anchor_event_must_target_an_anchored_comment(store, doc, round_id):
    unanchored = store.add_comment(round_id, author="bob", body="the tone is off")
    with pytest.raises(InvariantError, match="has no anchor"):
        store.ledger.append(
            {
                "type": ANCHOR_ORPHAN,
                "author": "agent:reanchor",
                "target": unanchored,
                "base": "sha256:" + "0" * 64,
                "reason": "gone",
            }
        )


def test_re_anchoring_leaves_a_ledger_the_reader_still_accepts(store, doc, anchored_comment, doc_text):
    doc.write_text("> Draft.\n\n" + doc_text, encoding="utf-8")
    carry(store, doc)
    revise(doc, "Timeouts are 30 seconds.", "")
    carry(store, doc)

    records = store.ledger.read()  # re-validates every line
    assert [r["seq"] for r in records] == list(range(len(records)))
    assert store.fold().count == len(records)
