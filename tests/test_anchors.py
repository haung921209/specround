"""Anchors: the write-time invariant, and what it refuses."""

import pytest

from specround.anchors import Anchor, anchor_for, anchor_for_quote
from specround.errors import AnchorError

TEXT = "The ledger is the contract.\nComments outlive revisions.\n"


def test_anchor_for_carries_quote_position_and_context():
    start = TEXT.index("contract")
    anchor = anchor_for(TEXT, start, start + len("contract"))
    assert anchor.exact == "contract"
    assert (anchor.start, anchor.end) == (start, start + len("contract"))
    assert anchor.prefix.endswith("is the ")
    assert anchor.suffix.startswith(".")
    anchor.verify(TEXT)


def test_context_is_bounded_and_clipped_at_the_edges():
    anchor = anchor_for(TEXT, 0, 3, context=8)
    assert anchor.prefix == ""  # nothing precedes offset 0
    assert anchor.suffix == TEXT[3:11]
    assert len(anchor.suffix) == 8


def test_quote_and_span_must_agree_at_construction():
    with pytest.raises(AnchorError, match="span and quote disagree"):
        Anchor(exact="ledger", start=4, end=99)


def test_verify_rejects_a_quote_that_moved():
    anchor = anchor_for(TEXT, 4, 10)
    revised = "A revised document. " + TEXT
    # H4 (re-anchoring) is out of scope: a stale anchor is reported, not repaired.
    with pytest.raises(AnchorError, match="quote mismatch"):
        anchor.verify(revised)
    assert anchor.matches(revised) is False
    assert anchor.matches(TEXT) is True


def test_verify_rejects_a_span_past_the_end_of_the_text():
    anchor = anchor_for(TEXT, len(TEXT) - 5, len(TEXT))
    with pytest.raises(AnchorError, match="the text is"):
        anchor.verify(TEXT[:-3])


def test_verify_rejects_a_matching_quote_with_wrong_context():
    # Same quote, same offsets, but the surrounding text changed: still stale.
    anchor = anchor_for(TEXT, TEXT.index("Comments"), TEXT.index("Comments") + 8)
    forged = TEXT.replace("revisions", "revisionX")
    anchor.verify(TEXT)
    with pytest.raises(AnchorError, match="suffix mismatch"):
        anchor.verify(forged)


def test_zero_length_anchor_is_an_insertion_point():
    anchor = anchor_for(TEXT, 4, 4)
    assert anchor.exact == ""
    anchor.verify(TEXT)


def test_json_round_trip_omits_empty_context():
    anchor = anchor_for(TEXT, 0, 3, context=0)
    payload = anchor.to_json()
    assert payload == {"exact": "The", "start": 0, "end": 3}
    assert Anchor.from_json(payload) == anchor


def test_json_round_trip_keeps_context():
    anchor = anchor_for(TEXT, 4, 10)
    assert Anchor.from_json(anchor.to_json()) == anchor


def test_from_json_rejects_unknown_and_missing_fields():
    with pytest.raises(AnchorError, match="unknown anchor field"):
        Anchor.from_json({"exact": "The", "start": 0, "end": 3, "line": 1})
    with pytest.raises(AnchorError, match="missing required field 'end'"):
        Anchor.from_json({"exact": "The", "start": 0})


def test_anchor_for_quote_picks_the_requested_occurrence():
    text = "alpha beta alpha beta alpha"
    first = anchor_for_quote(text, "alpha")
    third = anchor_for_quote(text, "alpha", occurrence=2)
    assert first.start == 0
    assert third.start == text.rindex("alpha")
    first.verify(text)
    third.verify(text)


def test_anchor_for_quote_reports_a_missing_occurrence():
    with pytest.raises(AnchorError, match="appears 2 time"):
        anchor_for_quote("alpha beta alpha", "alpha", occurrence=5)
    with pytest.raises(AnchorError, match="appears 0 time"):
        anchor_for_quote("alpha", "gamma")


def test_anchor_for_rejects_a_span_outside_the_text():
    with pytest.raises(AnchorError, match="past the end"):
        anchor_for(TEXT, 0, len(TEXT) + 1)
    with pytest.raises(AnchorError, match="invalid span"):
        anchor_for(TEXT, 10, 4)
