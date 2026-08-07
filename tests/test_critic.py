"""Reading CriticMarkup markers off a document (G6).

The unit here is the parse, and the property that matters most is not "the
markers were found" — it is that **the spans point at the right characters of
the text the markers are gone from**. Every offset an anchor will ever hold
comes out of this module, so a test that checks the kind and the body and not
``clean[start:end]`` would pass while every anchor landed one marker to the
left.

The other axis is the two-class split for input that cannot be harvested:
something complete but empty is a refusal, something that may not be an
annotation at all is reported and left alone. Those are opposite behaviours on
purpose and each has cases here.
"""

import pytest

from specround.critic import (
    COMMENT,
    DELETE,
    INSERT,
    SUBSTITUTE,
    UNSUPPORTED,
    UNTERMINATED,
    MarkupError,
    parse,
)

PROSE = """# Widget protocol

Timeouts are 30 seconds. Retries are not specified yet.
"""


def spans(harvest):
    """What each annotation's span actually holds in the clean text."""
    return [harvest.clean[a.start : a.end] for a in harvest.annotations]


# -- the four forms ------------------------------------------------------


def test_a_comment_leaves_no_text_and_anchors_at_its_point():
    harvest = parse("Timeouts are 30 seconds.{>>too short<<}\n")
    assert harvest.clean == "Timeouts are 30 seconds.\n"
    (annotation,) = harvest.annotations
    assert annotation.kind == COMMENT
    assert annotation.body == "too short"
    # A point, not a span: the marker named no text, and 32 characters of prefix
    # are the text it follows.
    assert (annotation.start, annotation.end) == (24, 24)
    assert annotation.anchored is False


def test_an_insertion_is_not_in_the_harvested_text():
    harvest = parse("Timeouts are 30 seconds.{++ Retries are not.++}\n")
    # The reviewer's text is a proposal, so harvesting records it and does not
    # write it. Keeping it would make reading the file apply the suggestion.
    assert harvest.clean == "Timeouts are 30 seconds.\n"
    (annotation,) = harvest.annotations
    assert annotation.kind == INSERT
    assert annotation.added == " Retries are not."
    assert (annotation.start, annotation.end) == (24, 24)


def test_a_deletion_keeps_its_text_and_anchors_on_it():
    harvest = parse("Retries are {--not --}specified.\n")
    # The document still says it; the suggestion proposes taking it out.
    assert harvest.clean == "Retries are not specified.\n"
    (annotation,) = harvest.annotations
    assert annotation.kind == DELETE
    assert annotation.removed == "not "
    assert harvest.clean[annotation.start : annotation.end] == "not "
    assert annotation.anchored is True


def test_a_substitution_keeps_the_old_text_and_carries_the_new():
    harvest = parse("Timeouts are {~~30~>60~~} seconds.\n")
    assert harvest.clean == "Timeouts are 30 seconds.\n"
    (annotation,) = harvest.annotations
    assert annotation.kind == SUBSTITUTE
    assert (annotation.removed, annotation.added) == ("30", "60")
    assert harvest.clean[annotation.start : annotation.end] == "30"


@pytest.mark.parametrize(
    "text, removed, added",
    [
        ("a {~~old~>~~}b", "old", ""),      # same as a deletion, spelled the long way
        ("a {~~~>new~~}b", "", "new"),      # same as an insertion
    ],
)
def test_a_substitution_may_be_empty_on_one_side(text, removed, added):
    (annotation,) = parse(text).annotations
    assert (annotation.removed, annotation.added) == (removed, added)


# -- offsets across several markers --------------------------------------


def test_offsets_count_in_the_clean_text_not_the_annotated_one():
    harvest = parse(
        "Timeouts are {~~30~>60~~} seconds.{>>why<<} "
        "Retries are {--not --}specified.{++ See RFC 1.++}\n"
    )
    assert harvest.clean == "Timeouts are 30 seconds. Retries are not specified.\n"
    # Each span holds what it names *after* the markers ahead of it were removed.
    # This is the assertion the whole module exists to keep true.
    assert spans(harvest) == ["30", "", "not ", ""]
    kinds = [a.kind for a in harvest.annotations]
    assert kinds == [SUBSTITUTE, COMMENT, DELETE, INSERT]
    points = [(a.start, a.end) for a in harvest.annotations]
    assert points == [(13, 15), (24, 24), (37, 41), (51, 51)]


def test_adjacent_markers_become_two_annotations_at_the_same_place():
    # A comment sitting beside an edit is the CriticMarkup way to say why. The
    # tool records both and does not associate them — the offsets show the
    # adjacency, and pairing them would be a guess at intent.
    harvest = parse("Retries are {--not --}{>>redundant<<}specified.\n")
    assert harvest.clean == "Retries are not specified.\n"
    delete, comment = harvest.annotations
    assert (delete.start, delete.end) == (12, 16)
    assert (comment.start, comment.end) == (16, 16)


def test_everything_that_is_not_a_marker_survives_verbatim():
    text = "# Title\r\n\r\nTabs\tand  spaces.{>>note<<}\r\nTrailing, no break"
    harvest = parse(text)
    assert harvest.clean == "# Title\r\n\r\nTabs\tand  spaces.\r\nTrailing, no break"


def test_a_document_with_no_markers_is_returned_unchanged():
    harvest = parse(PROSE)
    assert harvest.clean == PROSE
    assert harvest.annotations == []
    assert harvest.skipped == []
    assert harvest.found is False


def test_the_parse_is_deterministic():
    text = "a{>>one<<}b{--c--}d{~~e~>f~~}g{++h++}i"
    assert parse(text) == parse(text)


# -- whitespace: bodies are prose, payloads are content ------------------


def test_a_comment_body_is_stripped():
    (annotation,) = parse("x{>>   too short\n  <<}").annotations
    assert annotation.body == "too short"


def test_an_edit_payload_keeps_its_whitespace():
    # A space is a character of the document here, not padding around prose.
    (annotation,) = parse("Retries are{-- not--} specified.").annotations
    assert annotation.removed == " not"


# -- markers left in the document ----------------------------------------


def test_an_unterminated_opener_is_reported_and_left_alone():
    text = "Retries are {--not specified.\n"
    harvest = parse(text)
    # Not a refusal: a spec that merely mentions ``{--`` has to stay
    # harvestable. Not silence either — the reviewer sees it in the report and
    # in the file.
    assert harvest.clean == text
    assert harvest.annotations == []
    (skipped,) = harvest.skipped
    assert (skipped.reason, skipped.opener, skipped.line) == (UNTERMINATED, "{--", 1)
    assert skipped.text == "Retries are {--not specified."[12:]


def test_a_highlight_is_recognised_and_not_harvested():
    text = "Timeouts are {==30 seconds==}.\n"
    harvest = parse(text)
    assert harvest.clean == text
    (skipped,) = harvest.skipped
    assert (skipped.reason, skipped.opener) == (UNSUPPORTED, "{==")


def test_an_unterminated_highlight_reads_as_unterminated():
    (skipped,) = parse("Timeouts are {==30 seconds.\n").skipped
    assert skipped.reason == UNTERMINATED


def test_a_skipped_marker_does_not_stop_the_scan():
    harvest = parse("{--dangling\nTimeouts are {~~30~>60~~} seconds.{>>why<<}\n")
    # The stray opener would otherwise swallow the two real markers after it,
    # and losing those is the failure the report-and-leave rule avoids.
    assert harvest.clean == "{--dangling\nTimeouts are 30 seconds.\n"
    assert [a.kind for a in harvest.annotations] == [SUBSTITUTE, COMMENT]
    assert spans(harvest) == ["30", ""]
    assert [s.reason for s in harvest.skipped] == [UNTERMINATED]


def test_lines_are_reported_one_based():
    harvest = parse("one\ntwo\nthree {>>note<<}\nfour {--x\n")
    (annotation,) = harvest.annotations
    assert annotation.source_line == 3
    (skipped,) = harvest.skipped
    assert skipped.line == 4


# -- refusals ------------------------------------------------------------


@pytest.mark.parametrize(
    "text, expected",
    [
        ("a{>><<}b", "nothing in it"),
        ("a{>>   <<}b", "nothing in it"),
        ("a{++++}b", "nothing to insert"),
        ("a{----}b", "nothing to delete"),
        ("a{~~old~~}b", "needs '~>'"),
        ("a{~~~~}b", "needs '~>'"),
        ("a{~~~>~~}b", "nothing on either side"),
    ],
)
def test_a_complete_but_empty_marker_is_refused(text, expected):
    # The reviewer typed both ends of it, so this is unambiguously an
    # annotation. Declining it quietly would drop something somebody meant.
    with pytest.raises(MarkupError) as caught:
        parse(text)
    assert expected in str(caught.value)


def test_every_refusal_is_named_at_once():
    with pytest.raises(MarkupError) as caught:
        parse("a{>><<}b{----}c{~~x~~}d")
    message = str(caught.value)
    assert message.startswith("3 marker(s)")
    assert message.count("offset ") == 3


def test_a_refusal_names_the_line():
    with pytest.raises(MarkupError) as caught:
        parse("one\ntwo\nthree {>><<}\n")
    assert "line 3" in str(caught.value)


def test_parse_refuses_something_that_is_not_text():
    with pytest.raises(MarkupError):
        parse(b"{>>bytes<<}")  # type: ignore[arg-type]


# -- nesting is a hole, not a behaviour ----------------------------------


def test_a_marker_inside_a_comment_body_stays_literal():
    # Markers do not nest in this subset: the comment closes at the first
    # ``<<}``, so what is inside reads as body text.
    (annotation,) = parse("x{>>see {--this--} line<<}y").annotations
    assert annotation.body == "see {--this--} line"


# -- what a suggestion proposes ------------------------------------------


def test_proposed_carries_out_one_edit_against_the_clean_text():
    harvest = parse("Timeouts are {~~30~>60~~} seconds.\n")
    (annotation,) = harvest.annotations
    assert annotation.proposed(harvest.clean) == "Timeouts are 60 seconds.\n"


def test_proposed_for_a_deletion_removes_the_span():
    harvest = parse("Retries are {--not --}specified.\n")
    (annotation,) = harvest.annotations
    assert annotation.proposed(harvest.clean) == "Retries are specified.\n"


def test_proposed_for_an_insertion_opens_the_point():
    harvest = parse("Timeouts are 30 seconds.{++ Retries are not.++}\n")
    (annotation,) = harvest.annotations
    assert annotation.proposed(harvest.clean) == (
        "Timeouts are 30 seconds. Retries are not.\n"
    )


def test_a_comment_proposes_nothing():
    harvest = parse("x{>>why<<}y")
    (annotation,) = harvest.annotations
    with pytest.raises(MarkupError):
        annotation.proposed(harvest.clean)


# -- the span in the annotated text --------------------------------------


def test_source_span_names_the_payload_inside_the_marker():
    text = "Timeouts are {~~30~>60~~} seconds.\n"
    (annotation,) = parse(text).annotations
    start, end = annotation.source_span
    # Exact arithmetic, not a search: this is what lets a document annotated
    # before its round was opened be harvested against a base that still holds
    # the markers.
    assert text[start:end] == "30"


def test_source_span_of_a_point_is_where_the_marker_begins():
    text = "Timeouts are 30 seconds.{>>why<<}\n"
    (annotation,) = parse(text).annotations
    assert annotation.source_span == (24, 24)
    assert text[:24] == "Timeouts are 30 seconds."


def test_source_span_holds_for_every_anchored_form():
    text = "a{--one--}b{~~two~>three~~}c{++four++}d{>>five<<}e"
    for annotation in parse(text).annotations:
        start, end = annotation.source_span
        assert text[start:end] == annotation.removed
