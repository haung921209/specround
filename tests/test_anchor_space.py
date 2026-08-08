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
from specround.events import ANCHOR_REANCHOR
from specround.reanchor import reanchor
from specround.wire import comment_json

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
