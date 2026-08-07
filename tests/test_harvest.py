"""Harvesting through the store: what lands in the ledger, and what the file becomes.

``test_critic.py`` covers the parse. What is under test here is the pair of
things the parse cannot settle on its own — **where the anchors land in the
round's base**, and **that the document is rewritten without the markers** —
plus the ordering that keeps a crash from losing a comment.

Three placement cases, and two of them are exact. The reviewer annotated the
document after the round opened (removing the markers restores the base), or
before it (the base still holds the markers, so the span is inside one). Only
genuine drift reaches the re-anchor ladder. Each has its own section.
"""

import pytest

from specround.critic import MarkupError
from specround.errors import InvariantError, SpecroundError
from specround.events import COMMENT_ADD, SUGGESTION_ADD
from specround.reanchor import FUZZY, POSITION, QUOTE
from specround.store import ReviewStore

#: The fixture document, annotated in all four forms.
ANNOTATED = """# Widget protocol

The client sends a hello frame. The server answers with a hello frame.

Timeouts are {~~30~>60~~} seconds.{>>the proxy caps at 45s<<} \
Retries are {--not --}specified yet.{++ See RFC 1.++}
"""

#: What the fixture document is, once those markers are gone. Byte-identical to
#: ``conftest.DOC_TEXT`` — that identity is the point of the ordinary case.
HARVESTED = """# Widget protocol

The client sends a hello frame. The server answers with a hello frame.

Timeouts are 30 seconds. Retries are not specified yet.
"""


def annotate(doc, text=ANNOTATED):
    doc.write_text(text, encoding="utf-8")
    return doc


def kinds(store):
    return [record["type"] for record in store.ledger.read()]


@pytest.fixture
def harvested(store, doc, round_id):
    """The ordinary case, applied: a round, then markers, then a harvest."""
    annotate(doc)
    return store.harvest_document(doc, round_id, author="bob", apply=True)


# -- the ordinary case: annotated after the round opened -----------------


def test_the_document_is_rewritten_without_the_markers(harvested, doc):
    assert doc.read_text(encoding="utf-8") == HARVESTED


def test_harvesting_restores_the_text_the_round_froze(harvested, store, round_id, doc):
    # Not a coincidence and worth its own test: the markers were typed into the
    # snapshotted document, so taking them out gives that document back. This is
    # why the ordinary case needs no matcher at all.
    assert doc.read_text(encoding="utf-8") == store.base_text(round_id)


def test_a_comment_becomes_a_comment_and_the_edits_become_suggestions(harvested, store):
    assert kinds(store) == [
        "round.open",
        SUGGESTION_ADD,  # {~~30~>60~~}
        COMMENT_ADD,  # {>>the proxy caps at 45s<<}
        SUGGESTION_ADD,  # {--not --}
        SUGGESTION_ADD,  # {++ See RFC 1.++}
    ]
    assert len(harvested.comments) == 1
    assert len(harvested.suggestions) == 3


def test_every_harvested_anchor_holds_in_the_round_base(harvested, store, round_id):
    base = store.base_text(round_id)
    for comment in store.fold().comments.values():
        assert comment.anchor is not None
        comment.anchor.verify(base)  # I7, and the reason the offsets have to be right


def test_the_anchors_name_the_text_the_markers_named(harvested):
    quoted = [p.anchor.exact for p in harvested.placements]
    # The two forms that quote existing text anchor on exactly that text; the
    # comment and the insertion are points.
    assert quoted == ["30", "", "not ", ""]


def test_placement_in_the_ordinary_case_needs_no_ladder(harvested):
    assert [p.strategy for p in harvested.placements] == [None, None, None, None]
    assert not any(p.carried for p in harvested.placements)


def test_no_provenance_is_recorded_when_nothing_was_carried(harvested, store):
    assert all("ext" not in record for record in store.ledger.read())


def test_a_suggestion_carries_a_patch_of_the_harvested_text(harvested, store):
    patches = [c.patch for c in store.fold().comments.values() if c.patch]
    assert len(patches) == 3
    # One patch per proposal, each a diff of the harvested text against that one
    # edit carried out. Same dialect the view produces — stored, never applied.
    assert all(patch.startswith("--- a/spec.md") for patch in patches)
    added = sorted(
        line for patch in patches for line in patch.splitlines() if line.startswith("+T")
    )
    assert added == [
        "+Timeouts are 30 seconds. Retries are not specified yet. See RFC 1.",
        "+Timeouts are 30 seconds. Retries are specified yet.",
        "+Timeouts are 60 seconds. Retries are not specified yet.",
    ]


def test_a_comment_keeps_its_body(harvested, store):
    (comment,) = [c for c in store.fold().comments.values() if c.kind == "comment"]
    assert comment.body == "the proxy caps at 45s"


# -- the dry run ---------------------------------------------------------


def test_a_dry_run_appends_nothing_and_touches_no_file(store, doc, round_id):
    annotate(doc)
    before = doc.read_bytes()
    report = store.harvest_document(doc, round_id, author="bob")
    assert report.applied is False
    assert report.events == []
    assert kinds(store) == ["round.open"]
    assert doc.read_bytes() == before


def test_a_dry_run_computes_the_same_placements_as_the_applied_run(store, doc, round_id):
    annotate(doc)
    preview = store.harvest_document(doc, round_id, author="bob")
    applied = store.harvest_document(doc, round_id, author="bob", apply=True)
    # A preview that is easier to pass than the real run is not a preview.
    assert [p.anchor for p in preview.placements] == [p.anchor for p in applied.placements]
    assert preview.clean == applied.clean
    assert preview.rewrite == applied.rewrite


def test_a_dry_run_reports_the_text_that_would_be_written(store, doc, round_id):
    annotate(doc)
    assert store.harvest_document(doc, round_id, author="bob").clean == HARVESTED


# -- idempotence ---------------------------------------------------------


def test_harvesting_a_document_with_no_markers_writes_nothing(store, doc, round_id):
    before = doc.read_bytes()
    report = store.harvest_document(doc, round_id, author="bob", apply=True)
    assert report.found is False
    assert report.rewrite is False
    assert doc.read_bytes() == before
    assert kinds(store) == ["round.open"]


def test_a_second_harvest_is_a_no_op(harvested, store, doc, round_id):
    bytes_after_first = doc.read_bytes()
    events_after_first = len(store.ledger.read())
    again = store.harvest_document(doc, round_id, author="bob", apply=True)
    assert again.found is False
    assert doc.read_bytes() == bytes_after_first
    assert len(store.ledger.read()) == events_after_first


def test_skipped_markers_stay_in_the_document(store, doc, round_id):
    annotate(doc, HARVESTED + "{--dangling\n{==highlight==}\n")
    report = store.harvest_document(doc, round_id, author="bob", apply=True)
    assert [s.reason for s in report.skipped] == ["unterminated", "unsupported"]
    assert doc.read_text(encoding="utf-8").endswith("{--dangling\n{==highlight==}\n")


def test_line_endings_survive_the_rewrite(store, tmp_path, clock):
    crlf = tmp_path / "crlf.md"
    crlf.write_bytes(b"# Title\r\n\r\nTimeouts are 30 seconds.\r\n")
    store = ReviewStore.for_document(crlf, clock=clock)
    round_id = store.open_round(crlf, author="alice")
    crlf.write_bytes(b"# Title\r\n\r\nTimeouts are {~~30~>60~~} seconds.\r\n")

    report = store.harvest_document(crlf, round_id, author="bob", apply=True)

    # Snapshots keep the bytes they were given, so the clean text has to be the
    # same string as the base — text-mode IO would translate the breaks on the
    # way in and out and leave every anchor one character off per line.
    assert crlf.read_bytes() == b"# Title\r\n\r\nTimeouts are 30 seconds.\r\n"
    (placement,) = report.placements
    placement.anchor.verify(store.base_text(round_id))


def test_the_temporary_file_is_not_left_behind(harvested, doc, tmp_path):
    assert sorted(p.name for p in tmp_path.iterdir()) == [doc.name]


# -- annotated before the round opened: exact, off the source offsets ----


@pytest.fixture
def preannotated(store, doc, clock):
    """A round opened on a document that already carried its markers."""
    annotate(doc)
    return store.open_round(doc, author="alice", title="on the annotated file")


def test_a_base_that_holds_the_markers_places_them_exactly(store, doc, preannotated):
    report = store.harvest_document(doc, preannotated, author="bob", apply=True)
    assert [p.anchor.exact for p in report.placements] == ["30", "", "not ", ""]
    # Exact means exact: no rung was used, so there is no provenance to record.
    assert [p.strategy for p in report.placements] == [None, None, None, None]
    assert all("ext" not in record for record in store.ledger.read())


def test_those_anchors_hold_in_a_base_full_of_markers(store, doc, preannotated):
    store.harvest_document(doc, preannotated, author="bob", apply=True)
    base = store.base_text(preannotated)
    assert "{~~30~>60~~}" in base  # the base really is the annotated text
    for comment in store.fold().comments.values():
        comment.anchor.verify(base)


def test_the_document_is_still_rewritten_clean(store, doc, preannotated):
    store.harvest_document(doc, preannotated, author="bob", apply=True)
    assert doc.read_text(encoding="utf-8") == HARVESTED


# -- genuine drift: the ladder, and the refusal --------------------------


def test_a_moved_paragraph_is_carried_by_the_ladder(store, doc, round_id):
    annotate(doc, "> Draft.\n\n" + ANNOTATED)
    report = store.harvest_document(doc, round_id, author="bob", apply=True)
    # The prose moved as well, so neither exact case applies and the anchors are
    # where the ladder put them.
    strategies = {p.strategy for p in report.placements}
    assert strategies <= {POSITION, QUOTE, "normalized", FUZZY}
    assert any(p.carried for p in report.placements)
    for comment in store.fold().comments.values():
        comment.anchor.verify(store.base_text(round_id))


def test_a_carried_anchor_records_which_rung_placed_it(store, doc, round_id):
    annotate(doc, "> Draft.\n\n" + ANNOTATED)
    store.harvest_document(doc, round_id, author="bob", apply=True)
    notes = [record["ext"]["harvest"] for record in store.ledger.read() if "ext" in record]
    assert notes  # otherwise a fuzzy landing reads like an exact cut (§2)
    for note in notes:
        assert note["space"] == "clean"
        assert note["strategy"] in (QUOTE, "normalized", FUZZY)
        assert note["ambiguous"] in (True, False)


def test_a_marker_the_ladder_cannot_place_refuses_the_whole_harvest(store, doc, round_id):
    annotate(doc, "# Widget protocol\n\nThis page was replaced {--wholesale --}entirely.\n")
    before = doc.read_bytes()
    with pytest.raises(InvariantError) as caught:
        store.harvest_document(doc, round_id, author="bob", apply=True)
    # Atomic: the clean text is both the anchor basis and the text written to
    # disk, so leaving one marker behind would shift every offset after it.
    assert doc.read_bytes() == before
    assert kinds(store) == ["round.open"]
    assert "close this round and open a new one" in str(caught.value)


def test_the_dry_run_refuses_it_too(store, doc, round_id):
    annotate(doc, "# Widget protocol\n\nThis page was replaced {--wholesale --}entirely.\n")
    with pytest.raises(InvariantError):
        store.harvest_document(doc, round_id, author="bob")


def test_the_named_exit_actually_works(store, doc, round_id):
    """Re-opening the round on the annotated document lands in the exact case."""
    annotate(doc, "# Widget protocol\n\nThis page was replaced {--wholesale --}entirely.\n")
    with pytest.raises(InvariantError):
        store.harvest_document(doc, round_id, author="bob")

    store.close_round(round_id, author="alice", note="re-opening on the annotated file")
    reopened = store.open_round(doc, author="alice")
    report = store.harvest_document(doc, reopened, author="bob", apply=True)

    (placement,) = report.placements
    assert placement.anchor.exact == "wholesale "
    assert placement.strategy is None
    # The marker is gone and the word is not: harvesting records the proposal to
    # remove it, and applying that is a disposition somebody makes.
    assert doc.read_text(encoding="utf-8") == (
        "# Widget protocol\n\nThis page was replaced wholesale entirely.\n"
    )


# -- refusals that are not about placement -------------------------------


def test_a_malformed_marker_refuses_before_anything_is_written(store, doc, round_id):
    annotate(doc, HARVESTED + "\nAnd {>><<} nothing.\n")
    before = doc.read_bytes()
    with pytest.raises(MarkupError):
        store.harvest_document(doc, round_id, author="bob", apply=True)
    assert doc.read_bytes() == before
    assert kinds(store) == ["round.open"]


def test_harvesting_needs_a_round_on_this_document(store, doc, round_id):
    with pytest.raises(InvariantError, match="no round"):
        store.harvest_document(doc, "r-000000000000", author="bob")


def test_harvesting_something_that_is_not_a_file_is_an_error(store, tmp_path, round_id):
    with pytest.raises(SpecroundError):
        store.harvest_document(tmp_path / "gone.md", round_id, author="bob")


def test_a_document_that_is_not_utf8_is_an_error(store, doc, round_id):
    doc.write_bytes(b"# Title\n\n\xff\xfe not text\n")
    with pytest.raises(SpecroundError):
        store.harvest_document(doc, round_id, author="bob")


# -- the report ----------------------------------------------------------


def test_the_report_names_the_round_and_its_base(harvested, store, round_id):
    assert harvested.round == round_id
    assert harvested.base == store.round_base(round_id)


def test_the_report_carries_the_event_ids_only_once_applied(store, doc, round_id):
    annotate(doc)
    assert store.harvest_document(doc, round_id, author="bob").events == []
    applied = store.harvest_document(doc, round_id, author="bob", apply=True)
    assert len(applied.events) == 4
    assert all(event in store.fold().comments for event in applied.events)
