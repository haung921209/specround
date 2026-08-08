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

import pytest

from specround.events import ANCHOR_REANCHOR
from specround.reanchor import reanchor

QUOTE_TEXT = "Timeouts are 30 seconds."
DRAFT = "> Draft.\n\n"


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
