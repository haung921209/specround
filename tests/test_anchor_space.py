"""The space an anchor lives in — one definition, checked where it is painted.

``current_anchor`` is what every surface draws: the web view paints it over the
round's base, the CLI prints its quote. So the question this file asks is not
"is the anchor self-consistent" (``test_anchor_integrity`` asks that, and I7
answers it against each event's own base) but the one the reviewer sees:
**does the anchor hold in the text it is drawn on.**

A ledger that answers yes to the first and no to the second is the failure this
module exists to catch, and it is not hypothetical — it was measured on a real
review, 12 of 17 comments landing on sentences they were never about.
"""

import json

import pytest

from specround.cli import main
from specround.errors import InvariantError
from specround.events import ANCHOR_KINDS, ANCHOR_REANCHOR, ROUND_OPEN
from specround.reanchor import reanchor
from specround.wire import comment_json, document_summary

QUOTE_TEXT = "Timeouts are 30 seconds."
DRAFT = "> Draft.\n\n"


@pytest.fixture
def run(capsys):
    """The CLI as a shell would call it — code, stdout, stderr together."""

    class Result:
        def __init__(self, code, out, err):
            self.code, self.out, self.err = code, out, err

        @property
        def json(self):
            return json.loads(self.out)

        @property
        def error(self):
            return json.loads(self.err)["error"]

    def invoke(*argv):
        code = main([str(arg) for arg in argv])
        captured = capsys.readouterr()
        return Result(code, captured.out, captured.err)

    return invoke


@pytest.fixture
def anchored_comment(store, round_id):
    anchor = store.anchor_in_round(round_id, QUOTE_TEXT)
    return store.add_comment(round_id, author="bob", body="too short", anchor=anchor)


def pollute(store, doc, comment_id, revised):
    """Write the anchoring an older specround wrote: one cut from the revision.

    This is what the measured ledger holds, so the reproduction has to build it
    the same way rather than through the API that now refuses. The record is
    self-consistent — the anchor really does hold in the snapshot it names — and
    that is precisely why I7 passed it through.
    """
    doc.write_text(revised, encoding="utf-8")
    base = store.snapshots.put_file(doc)
    landed = reanchor(store.fold().comments[comment_id].current_anchor, revised)
    store.ledger.append(
        {
            "type": ANCHOR_REANCHOR,
            "author": "agent:old",
            "target": comment_id,
            "base": base,
            "anchor": landed.anchor.to_json(),
            "strategy": landed.strategy,
        }
    )
    return base


def test_an_anchor_cut_from_another_space_is_reported_misplaced(
    store, doc, doc_text, round_id, anchored_comment
):
    """The whole bug in one assertion: it verifies where it was cut, not where it is drawn."""
    pollute(store, doc, anchored_comment, DRAFT + doc_text)

    comment = store.fold().comments[anchored_comment]
    painted = store.base_text(round_id)
    anchor = comment.current_anchor

    # Self-consistent against its own base — which is why nothing caught it.
    assert anchor.exact == QUOTE_TEXT
    # ...and wrong against the text the view actually paints.
    assert painted[anchor.start : anchor.end] != anchor.exact
    assert comment.misplaced is True


def test_a_comment_in_the_painted_space_is_not_misplaced(store, round_id, anchored_comment):
    assert store.fold().comments[anchored_comment].misplaced is False


def test_misplaced_is_not_the_orphan_axis(store, doc, doc_text, round_id, anchored_comment):
    """Two different absences: the text is gone, versus the offsets are somebody else's."""
    pollute(store, doc, anchored_comment, DRAFT + doc_text)
    comment = store.fold().comments[anchored_comment]
    assert comment.orphaned is False  # a rung placed it — in the wrong space
    assert comment.misplaced is True


def test_the_wire_form_carries_the_finding(store, doc, doc_text, round_id, anchored_comment):
    """A consumer that paints ``current_anchor`` has to be able to see this."""
    pollute(store, doc, anchored_comment, DRAFT + doc_text)
    payload = comment_json(store.fold().comments[anchored_comment])
    assert payload["misplaced"] is True


def test_the_document_summary_counts_them(store, doc, doc_text, round_id, anchored_comment):
    """The navigation bar says "0 orphaned" about these — it must not stop there."""
    pollute(store, doc, anchored_comment, DRAFT + doc_text)
    summary = document_summary(store.fold(), store.doc_key(doc))
    assert summary["orphans"] == 0
    assert summary["misplaced"] == 1


def test_the_listing_marks_it_rather_than_quoting_it_plainly(
    run, store, doc, doc_text, round_id, anchored_comment
):
    """The CLI prints the quote from an anchor it cannot place — it has to say so."""
    pollute(store, doc, anchored_comment, DRAFT + doc_text)
    result = run("comments", doc)
    assert result.code == 0
    assert "misplaced" in result.out


def test_round_status_counts_them(run, store, doc, doc_text, round_id, anchored_comment):
    """A number a person can watch go to zero after a repair."""
    pollute(store, doc, anchored_comment, DRAFT + doc_text)
    result = run("round", "status", doc, "--json")
    assert result.code == 0
    assert result.json["counts"]["misplaced"] == 1


# -- only round.open makes a space ----------------------------------------


def test_opening_a_round_carries_the_live_comments_onto_its_base(
    store, doc, doc_text, round_id, anchored_comment
):
    """The carry moves to the one moment a new anchor space comes into existence."""
    store.close_round(round_id, author="alice", allow_undisposed=True)
    doc.write_text(DRAFT + doc_text, encoding="utf-8")
    second = store.open_round(doc, author="alice", title="second pass")

    comment = store.fold().comments[anchored_comment]
    assert comment.misplaced is False
    assert comment.current_anchor.exact == QUOTE_TEXT
    assert comment.current_anchor.matches(store.base_text(second))
    assert comment.anchoring.base == store.round_base(second)


def test_a_carry_that_cannot_find_the_text_records_an_orphan(store, doc, round_id, anchored_comment):
    doc.write_text("# Widget protocol\n\nThis page was replaced wholesale.\n", encoding="utf-8")
    store.open_round(doc, author="alice")

    comment = store.fold().comments[anchored_comment]
    assert comment.orphaned is True
    assert comment.anchoring.reason


def test_opening_the_first_round_carries_nothing(store, doc):
    store.open_round(doc, author="alice")
    assert [record["type"] for record in store.ledger.read()] == ["round.open"]


def test_opening_a_round_on_an_unchanged_document_appends_nothing_extra(
    store, doc, round_id, anchored_comment
):
    """Same bytes, same content address — the comments are already in that space."""
    store.open_round(doc, author="alice")
    assert [record["type"] for record in store.ledger.read()] == [
        "round.open",
        "comment.add",
        "round.open",
    ]


def test_re_anchoring_is_refused_once_the_document_moved_past_the_round_base(
    store, doc, doc_text, round_id, anchored_comment
):
    """The invocation that made the measured mess is the one that now fails loudly.

    Nothing froze the revision, so an anchor cut from it would name a space no
    surface shows — which is what happened, silently, twelve times.
    """
    doc.write_text(DRAFT + doc_text, encoding="utf-8")
    with pytest.raises(InvariantError) as raised:
        store.reanchor_document(doc, author="agent:reanchor")

    message = str(raised.value)
    assert "round open" in message  # way out 1: freeze the revision, comments carry
    assert store.ledger.count() == 2  # and nothing was written


def test_re_anchoring_an_unmoved_document_is_still_a_no_op(store, doc, round_id, anchored_comment):
    report = store.reanchor_document(doc, author="agent:reanchor")
    assert report.unchanged == [anchored_comment]
    assert store.ledger.count() == 2


def test_the_cli_refusal_is_a_state_error(run, doc, doc_text, round_id, anchored_comment):
    doc.write_text(DRAFT + doc_text, encoding="utf-8")
    result = run("reanchor", doc, "--author", "agent:reanchor", "--json")
    assert result.code == 3
    assert "round open" in result.error["message"]


def anchoring_bases(store):
    return {r["base"] for r in store.ledger.read() if r["type"] in ANCHOR_KINDS}


def round_bases(store):
    return {r["base"] for r in store.ledger.read() if r["type"] == ROUND_OPEN}


def test_every_anchoring_a_writer_produces_names_a_round_base(store, doc, doc_text, round_id):
    """The class, not the instance: no path through the API can make a third space.

    This is the shape of the whole fix. "One field, two spaces" was possible
    because a writer could freeze a text for the anchors without freezing it for
    the review, so the assertion worth keeping is not about any one verb — it is
    that the set of texts anchors live in and the set of texts rounds froze are
    the same set.
    """
    store.add_comment(
        round_id, author="bob", body="too short", anchor=store.anchor_in_round(round_id, QUOTE_TEXT)
    )
    store.add_suggestion(
        round_id,
        author="agent:reviewer",
        patch="-30\n+60\n",
        anchor=store.anchor_in_round(round_id, "hello frame"),
    )
    store.close_round(round_id, author="alice", allow_undisposed=True)

    doc.write_text(DRAFT + doc_text, encoding="utf-8")  # moved down the page
    store.open_round(doc, author="alice")
    store.reanchor_document(doc, author="agent:reanchor")  # the idempotent re-drive
    doc.write_text("# Gadget\n\nNothing above survives.\n", encoding="utf-8")  # and lost
    store.open_round(doc, author="alice")

    assert anchoring_bases(store)  # the sequence really did write some
    assert anchoring_bases(store) <= round_bases(store)


def test_the_measured_ledger_is_what_that_assertion_catches(
    store, doc, doc_text, round_id, anchored_comment
):
    """...and the assertion above has teeth: this is the shape it excludes."""
    pollute(store, doc, anchored_comment, DRAFT + doc_text)
    assert not anchoring_bases(store) <= round_bases(store)


# -- repairing a ledger that already holds them ---------------------------


def test_a_repair_is_a_dry_run_until_it_is_asked_for(
    store, doc, doc_text, round_id, anchored_comment
):
    pollute(store, doc, anchored_comment, DRAFT + doc_text)
    count = store.ledger.count()

    report = store.repair_document(doc, author="agent:doctor")
    assert report.applied is False
    assert report.repaired == [anchored_comment]
    assert store.ledger.count() == count  # said what it would do, did nothing


def test_a_repair_re_interprets_the_quote_in_the_base_it_is_painted_on(
    store, doc, doc_text, round_id, anchored_comment
):
    """The exact text was right all along — only the offsets came from elsewhere."""
    pollute(store, doc, anchored_comment, DRAFT + doc_text)
    store.repair_document(doc, author="agent:doctor", apply=True)

    comment = store.fold().comments[anchored_comment]
    assert comment.misplaced is False
    assert comment.current_anchor.exact == QUOTE_TEXT
    assert comment.current_anchor.matches(store.base_text(round_id))


def test_a_repair_appends_and_never_edits(store, doc, doc_text, round_id, anchored_comment):
    """Append-only holds through the repair: the bad record stays, corrected after it."""
    pollute(store, doc, anchored_comment, DRAFT + doc_text)
    before = store.ledger.read()
    store.repair_document(doc, author="agent:doctor", apply=True)

    after = store.ledger.read()
    assert after[: len(before)] == before
    assert after[-1]["type"] == ANCHOR_REANCHOR
    assert len(after) == len(before) + 1


def test_a_repair_that_cannot_find_the_text_records_an_orphan(
    store, doc, doc_text, round_id, anchored_comment
):
    """Nothing is guessed into place here either — that rule does not bend for a repair.

    The fuzzy rung re-cut the quote from the revision, so what the ledger now
    holds is the *revised* sentence: text the base does not contain at all.
    """
    pollute(store, doc, anchored_comment, doc_text.replace("30 seconds", "45 seconds"))
    report = store.repair_document(doc, author="agent:doctor", apply=True, min_similarity=0.99)

    assert report.orphaned == [anchored_comment]
    assert store.fold().comments[anchored_comment].orphaned is True


def test_a_second_repair_has_nothing_left_to_do(store, doc, doc_text, round_id, anchored_comment):
    pollute(store, doc, anchored_comment, DRAFT + doc_text)
    store.repair_document(doc, author="agent:doctor", apply=True)
    count = store.ledger.count()

    again = store.repair_document(doc, author="agent:doctor", apply=True)
    assert again.repaired == []
    assert store.ledger.count() == count


def test_a_repair_on_a_clean_ledger_is_a_no_op(store, doc, round_id, anchored_comment):
    report = store.repair_document(doc, author="agent:doctor", apply=True)
    assert report.repaired == [] and report.orphaned == []
    assert store.ledger.count() == 2


def test_a_repair_does_not_read_the_document_on_disk(
    store, doc, doc_text, round_id, anchored_comment
):
    """The ledger is what is broken, so the repair must not need the file to be anything.

    A polluted ledger got that way because the document moved on, and it may
    have moved again, or been deleted, since. Making the repair depend on the
    file's state would make it unavailable exactly when it is needed.
    """
    pollute(store, doc, anchored_comment, DRAFT + doc_text)
    doc.unlink()

    report = store.repair_document(doc, author="agent:doctor", apply=True)
    assert report.repaired == [anchored_comment]
    assert store.fold().comments[anchored_comment].misplaced is False


def test_the_doctor_verb_reports_and_then_repairs(
    run, store, doc, doc_text, round_id, anchored_comment
):
    pollute(store, doc, anchored_comment, DRAFT + doc_text)

    preview = run("doctor", doc, "--author", "agent:doctor", "--json")
    assert preview.code == 0
    assert preview.json["applied"] is False
    assert preview.json["repaired"] == [anchored_comment]

    applied = run("doctor", doc, "--author", "agent:doctor", "--apply", "--json")
    assert applied.code == 0
    assert applied.json["applied"] is True
    assert run("round", "status", doc, "--json").json["counts"]["misplaced"] == 0
