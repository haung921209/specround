"""I7 — an anchor agrees with the snapshot it names, on the way in *and* out.

Every other invariant can be settled from the record sequence alone, so
:func:`specround.fold.fold` settles them and stays the pure function §8 promises.
I7 cannot: deciding it means opening a snapshot. That is the whole reason it was
the one invariant with no reader behind it — the write path checked it, the read
path could not, and §6's own warning about "two copies of a check" came true as
"one copy and a hole".

The hole is what these tests are about. A hand-written line that names text which
is not in the snapshot used to be rendered as fact, ``--json`` included. The rule
now runs from one implementation on both paths, and the read path lives one layer
out from ``fold`` — in the store, which is the only thing that has the objects.
"""

import json

import pytest

from specround.anchors import Anchor
from specround.errors import InvariantError, SnapshotError
from specround.events import ANCHOR_ORPHAN, ANCHOR_REANCHOR, COMMENT_ADD, canonical_json, derive_id
from specround.store import ReviewStore

QUOTE_TEXT = "Timeouts are 30 seconds."

#: A well formed reference to something no store has ever held.
ABSENT_BASE = "sha256:" + "ab" * 32


def handwrite(store, record):
    """Append a record straight to the file, past every gate the API applies.

    This is the participant the format is written for: "이 줄들을 쓸 수 있으면
    어떤 언어·에디터·에이전트든 참여자". The reader is what has to hold the line.
    """
    path = store.ledger.path
    lines = path.read_text(encoding="utf-8").splitlines()
    full = {"schema": "specround.ledger/v0", "seq": len(lines), "ts": "2026-02-01T09:00:00Z", **record}
    full["id"] = derive_id(full)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(canonical_json(full) + "\n")
    return full["id"]


@pytest.fixture
def anchored_comment(store, doc, round_id):
    anchor = store.anchor_in_round(round_id, QUOTE_TEXT)
    return store.add_comment(round_id, author="bob", body="too short", anchor=anchor)


# -- the hole ------------------------------------------------------------


def test_a_comment_anchored_to_text_that_is_not_there_is_refused(store, doc, round_id):
    """The reviewer never read this sentence: it is not in what the round froze."""
    handwrite(
        store,
        {
            "type": COMMENT_ADD,
            "author": "attacker",
            "round": round_id,
            "body": "anchor points at text that is not there",
            "anchor": {"exact": "ZZZZZZZZZ", "start": 0, "end": 9},
        },
    )
    with pytest.raises(InvariantError, match="I7"):
        store.fold()


def test_a_reanchor_whose_offsets_run_past_its_snapshot_is_refused(
    store, doc, round_id, anchored_comment, doc_text
):
    base = store.snapshots.put_text(doc_text)
    handwrite(
        store,
        {
            "type": ANCHOR_REANCHOR,
            "author": "attacker",
            "target": anchored_comment,
            "base": base,
            "anchor": {"exact": QUOTE_TEXT, "start": 9000, "end": 9000 + len(QUOTE_TEXT)},
            "strategy": "quote",
        },
    )
    with pytest.raises(InvariantError, match="I7"):
        store.fold()


def test_a_reanchor_naming_a_snapshot_the_store_does_not_have_is_refused(
    store, doc, round_id, anchored_comment
):
    """A base nobody can open is not an anchor anybody can trust.

    Distinct from the two above on purpose, and it keeps its own error class:
    those say the recorded history is wrong, this one says the object store
    cannot answer for it. A caller does different things about the two.
    """
    handwrite(
        store,
        {
            "type": ANCHOR_REANCHOR,
            "author": "attacker",
            "target": anchored_comment,
            "base": ABSENT_BASE,
            "anchor": {"exact": QUOTE_TEXT, "start": 0, "end": len(QUOTE_TEXT)},
            "strategy": "quote",
        },
    )
    with pytest.raises(SnapshotError):
        store.fold()


def test_the_refusal_reaches_the_shell_as_a_state_verdict(store, doc, round_id, capsys):
    from specround.cli import main

    handwrite(
        store,
        {
            "type": COMMENT_ADD,
            "author": "attacker",
            "round": round_id,
            "body": "not there",
            "anchor": {"exact": "ZZZZZZZZZ", "start": 0, "end": 9},
        },
    )
    # No ``--store``: the resolved central store is the one the fixture wrote to.
    code = main(["comments", str(doc), "--json"])
    assert code == 3
    assert capsys.readouterr().out == ""  # nothing was reported as fact


def test_both_paths_refuse_the_same_anchor_with_the_same_error(store, doc, round_id):
    """One condition, one exception class, whichever direction it is found in.

    Two exception classes for one rule is the same drift §6 warns about: a
    caller has to know which side of the store it is standing on to know what to
    catch, and the two sides then get to disagree about what the rule even is.
    """
    bad = {"exact": "ZZZZZZZZZ", "start": 0, "end": 9}

    with pytest.raises(InvariantError, match="I7") as on_write:
        store.add_comment(round_id, author="bob", body="nope", anchor=Anchor(**bad))

    handwrite(
        store,
        {
            "type": COMMENT_ADD,
            "author": "attacker",
            "round": round_id,
            "body": "nope",
            "anchor": bad,
        },
    )
    with pytest.raises(InvariantError, match="I7") as on_read:
        store.fold()

    assert type(on_write.value) is type(on_read.value)


# -- and no false positives ----------------------------------------------


def test_a_history_written_through_the_api_folds_clean(store, doc, round_id, anchored_comment, doc_text):
    """Every shape the writer can produce has to survive its own reader."""
    store.add_comment(round_id, author="bob", body="on the whole document")
    store.add_suggestion(
        round_id,
        author="agent:reviewer",
        patch="-30\n+60\n",
        anchor=store.anchor_in_round(round_id, QUOTE_TEXT),
    )
    doc.write_text("> Draft.\n\n" + doc_text, encoding="utf-8")
    store.reanchor_document(doc, author="agent:reanchor")  # rebinds
    doc.write_text("# Gadget\n\nNothing above survives.\n", encoding="utf-8")
    store.reanchor_document(doc, author="agent:reanchor")  # orphans

    state = store.fold()
    assert len(state.comments) == 3
    assert any(c.orphaned for c in state.comments.values())


def test_an_orphan_carries_no_anchor_and_is_not_checked(store, doc, anchored_comment):
    """``anchor.orphan`` names a base and no anchor — there is nothing to agree."""
    handwrite(
        store,
        {
            "type": ANCHOR_ORPHAN,
            "author": "agent:reanchor",
            "target": anchored_comment,
            "base": ABSENT_BASE,
            "reason": "the text is gone",
        },
    )
    assert store.fold().comments[anchored_comment].orphaned is True


# -- fold stays pure (§8) -------------------------------------------------


def test_fold_itself_still_reads_nothing_but_the_records(store, round_id, doc_text):
    """The reason I7 lives one layer out, stated as a test.

    ``fold`` is specified as a pure function of the record sequence. It has to
    accept a record it cannot judge rather than grow a filesystem dependency to
    judge it — the invariant moves to the layer that has the objects, not the
    purity.
    """
    from specround.fold import fold

    records = [json.loads(line) for line in store.ledger.path.read_text(encoding="utf-8").splitlines()]
    records.append(
        {
            "schema": "specround.ledger/v0",
            "seq": len(records),
            "ts": "2026-02-01T09:00:00Z",
            "id": "c-000000000000",
            "type": COMMENT_ADD,
            "author": "attacker",
            "round": round_id,
            "body": "not there",
            "anchor": {"exact": "ZZZZZZZZZ", "start": 0, "end": 9},
        }
    )
    state = fold(records)
    assert state.comments["c-000000000000"].anchor == Anchor(exact="ZZZZZZZZZ", start=0, end=9)
