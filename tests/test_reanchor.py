"""Re-anchoring: the ladder, and the properties that have to hold on every rung.

The scenario table below is the point of this file. Each revision is a thing
that actually happens to a spec between review rounds, and each one is checked
against the same properties rather than against a hand-written expected offset:
whatever the matcher returns must verify against the revised text, must be the
same on a second run, and must be an orphan when the text is genuinely gone.
"""

import random

import pytest

from specround.anchors import Anchor, anchor_for_quote
from specround.errors import AnchorError
from specround.reanchor import (
    FUZZY,
    MIN_SIMILARITY,
    NORMALIZED,
    POSITION,
    QUOTE,
    STRATEGIES,
    Rebind,
    reanchor,
)

DOC = """# Widget protocol

The client sends a "hello" frame. The server answers with a "hello" frame.

Timeouts are 30 seconds. Retries are not specified yet.

Errors are returned as a problem document.
"""

QUOTE_TEXT = "Timeouts are 30 seconds."


def anchored(text=DOC, quote=QUOTE_TEXT, occurrence=0):
    return anchor_for_quote(text, quote, occurrence=occurrence)


# -- the revision scenarios ----------------------------------------------
#
# (name, revised document, does the quoted text survive)

def _insert_above(doc):
    return "> Draft, do not circulate.\n\n" + doc


def _edit_the_paragraph(doc):
    return doc.replace(QUOTE_TEXT, "Timeouts are 45 seconds.")


def _rewrap_the_paragraph(doc):
    return doc.replace(QUOTE_TEXT, "Timeouts are\n30 seconds.")


def _typographic_pass(doc):
    """A pass that only changes glyphs: straight quotes become curly ones."""
    return doc.replace('"hello"', "“hello”")


def _move_the_paragraph(doc):
    lines = doc.splitlines(keepends=True)
    para = [line for line in lines if line.startswith("Timeouts")]
    rest = [line for line in lines if not line.startswith("Timeouts")]
    return "".join(para + ["\n"] + rest)


def _delete_the_paragraph(doc):
    return doc.replace(QUOTE_TEXT + " Retries are not specified yet.\n\n", "")


def _rewrite_everything(doc):
    return "# Gadget protocol\n\nNothing above survives this revision at all.\n"


SCENARIOS = [
    ("unchanged", lambda doc: doc, True),
    ("insert above", _insert_above, True),
    ("edit the paragraph", _edit_the_paragraph, True),
    ("rewrap the paragraph", _rewrap_the_paragraph, True),
    ("typographic pass", _typographic_pass, True),
    ("move the paragraph", _move_the_paragraph, True),
    ("delete the paragraph", _delete_the_paragraph, False),
    ("rewrite everything", _rewrite_everything, False),
]

IDS = [name for name, _, _ in SCENARIOS]


# -- properties over the whole table -------------------------------------


@pytest.mark.parametrize("name,revise,survives", SCENARIOS, ids=IDS)
def test_a_returned_anchor_always_describes_the_revised_text(name, revise, survives):
    """The safety property: the matcher never hands back an anchor that lies."""
    result = reanchor(anchored(), revise(DOC))
    if result.found:
        result.anchor.verify(revise(DOC))  # raises if it does not hold


@pytest.mark.parametrize("name,revise,survives", SCENARIOS, ids=IDS)
def test_surviving_text_is_rebound_and_deleted_text_is_orphaned(name, revise, survives):
    result = reanchor(anchored(), revise(DOC))
    assert result.found is survives
    if survives:
        assert result.strategy in STRATEGIES
        assert result.reason == ""
    else:
        assert result.strategy is None
        assert result.reason  # an orphan says why


@pytest.mark.parametrize("name,revise,survives", SCENARIOS, ids=IDS)
def test_rebinding_is_deterministic(name, revise, survives):
    revised = revise(DOC)
    anchor = anchored()
    assert reanchor(anchor, revised) == reanchor(anchor, revised)


@pytest.mark.parametrize("name,revise,survives", SCENARIOS, ids=IDS)
def test_rebinding_is_idempotent_once_it_has_landed(name, revise, survives):
    """A second pass over the same text is a no-op — rung 1 catches it."""
    revised = revise(DOC)
    first = reanchor(anchored(), revised)
    if not first.found:
        return
    again = reanchor(first.anchor, revised)
    assert again.strategy == POSITION
    assert again.anchor == first.anchor


# -- which rung answers ---------------------------------------------------


def test_unchanged_text_matches_on_position():
    result = reanchor(anchored(), DOC)
    assert result.strategy == POSITION
    assert result.anchor == anchored()


def test_text_pushed_down_is_found_verbatim():
    result = reanchor(anchored(), _insert_above(DOC))
    assert result.strategy == QUOTE
    assert result.anchor.exact == QUOTE_TEXT
    assert result.anchor.start > anchored().start


def test_a_reflowed_paragraph_is_found_after_normalising_whitespace():
    revised = _rewrap_the_paragraph(DOC)
    result = reanchor(anchored(), revised)
    assert result.strategy == NORMALIZED
    assert result.anchor.exact == "Timeouts are\n30 seconds."


def test_a_typographic_pass_is_absorbed_by_the_fold():
    revised = _typographic_pass(DOC)
    anchor = anchored(quote='The server answers with a "hello" frame.')
    result = reanchor(anchor, revised)
    assert result.strategy == NORMALIZED
    assert result.anchor.exact == "The server answers with a “hello” frame."


def test_unicode_renormalisation_is_absorbed_by_the_fold():
    """NFC to NFD is what a macOS filesystem does on its own.

    Not one glyph changed, so calling it ``fuzzy`` tells a reviewer to go and
    look at a sentence nobody touched. ``fuzzy`` is the word for "the quoted text
    was rewritten", and diluting it costs the signal it exists to carry.
    """
    import unicodedata

    quote = "Le délai est de 30 secondes."
    doc = unicodedata.normalize("NFC", f"# Protocole\n\n{quote} Rien d'autre.\n")
    anchor = anchor_for_quote(doc, unicodedata.normalize("NFC", quote))
    revised = unicodedata.normalize("NFD", doc)
    assert revised != doc  # the file really did change on disk

    result = reanchor(anchor, revised)

    assert result.strategy == NORMALIZED
    assert result.anchor.exact == unicodedata.normalize("NFD", quote)
    result.anchor.verify(revised)


def test_an_edited_quote_is_found_by_fuzzy_alignment():
    result = reanchor(anchored(), _edit_the_paragraph(DOC))
    assert result.strategy == FUZZY
    assert "45 seconds" in result.anchor.exact


def test_a_fuzzy_span_is_not_allowed_to_cut_a_word_in_half():
    """The characters that match are not the same thing as the span to show.

    "30 seconds" against "60 seconds" matches on "0 seconds" — the 6 is not in
    the quote. That is the correct alignment and the wrong anchor, so the span
    grows back out to the word it was sitting inside.
    """
    revised = DOC.replace(QUOTE_TEXT, "Timeouts are 60 seconds.")
    result = reanchor(anchored(quote="30 seconds"), revised)
    assert result.strategy == FUZZY
    assert result.anchor.exact == "60 seconds"


def test_snapping_is_bounded_and_never_breaks_the_floor():
    from specround.reanchor import SNAP_CHARS, _snap

    text = "aaaa" + "b" * 200 + "cccc"
    start, end = _snap(text, 10, 20)
    assert 10 - start <= SNAP_CHARS
    assert end - 20 <= SNAP_CHARS
    # Punctuation on both sides is already a boundary: nothing to grow into.
    assert _snap("one. two. three.", 5, 8) == (5, 8)


def test_a_deleted_quote_is_an_orphan_not_a_wrong_guess():
    result = reanchor(anchored(), _delete_the_paragraph(DOC))
    assert result.orphaned
    assert result.anchor is None
    assert "not in the revised text" in result.reason


def test_the_similarity_floor_is_what_separates_a_match_from_an_orphan():
    revised = DOC.replace(QUOTE_TEXT, "Deadlines are 30 minutes.")
    assert reanchor(anchored(), revised, min_similarity=0.5).found
    assert reanchor(anchored(), revised, min_similarity=0.99).orphaned


# -- repeated text and ambiguity -----------------------------------------


def test_context_picks_between_repeated_quotes():
    text = "alpha\n\nsee section two\n\nbeta\n\nsee section two\n\ngamma\n"
    second = anchor_for_quote(text, "see section two", occurrence=1)
    revised = "prelude\n\n" + text
    result = reanchor(second, revised)
    assert result.strategy == QUOTE
    assert result.ambiguous is False
    # It landed on the second occurrence, not the first one it walked past.
    assert revised[: result.anchor.start].count("see section two") == 1


def test_a_true_tie_is_flagged_rather_than_silently_resolved():
    """Identical quote, identical surroundings: the hint decides, and says so."""
    text = "x\n\nrepeat me\n\nx\n\nrepeat me\n\nx\n"
    anchor = Anchor(exact="repeat me", start=text.index("repeat me"), end=text.index("repeat me") + 9)
    result = reanchor(anchor, "pad\n" + text)
    assert result.found
    assert result.ambiguous is True


def test_an_unambiguous_single_hit_is_not_flagged():
    result = reanchor(anchored(), _insert_above(DOC))
    assert result.ambiguous is False


# -- the context floor: a twin is not the same span ----------------------
#
# The two tests below are the same failure from opposite sides. A verbatim hit
# is the most trusted rung there is, which is exactly why it needs a veto: an
# identical sentence somewhere else in the document scores a perfect quote match
# and is still the wrong place to put the comment.


TWIN_DOC = """# Protocol

## Section A — connection
The timeout is 30 seconds for the handshake phase of the connection.

## Section B — streaming
The timeout is 30 seconds for the streaming phase, which is under review.
"""

TWIN_QUOTE = "The timeout is 30 seconds"


def test_a_comment_whose_section_was_deleted_does_not_move_to_its_twin():
    """The author answered the comment by deleting the section it was on.

    The quoted sentence still exists — in the *other* section, which nobody was
    talking about. Landing there puts an objection on a sentence its author
    never objected to, and records it on the most trusted rung there is. An
    orphan is the honest answer: the passage this comment named is gone.
    """
    revised = TWIN_DOC[: TWIN_DOC.index("## Section B")]
    anchor = anchor_for_quote(TWIN_DOC, TWIN_QUOTE, occurrence=1)

    result = reanchor(anchor, revised)

    assert result.orphaned
    assert result.anchor is None
    assert "context" in result.reason


def test_the_context_floor_does_not_cost_a_quote_that_only_lost_one_side():
    """The other side of the same veto: an edit above must stay survivable.

    Deleting the paragraph before the quote wipes the anchor's prefix, but the
    suffix is untouched and the quote is unique. That is a move, not a twin, and
    it has to keep rebinding — a floor that orphaned this would trade a rare
    wrong answer for a common lost comment.
    """
    doc = (
        "# Protocol\n\nA long opening paragraph that carries the whole prefix.\n\n"
        "The timeout is 30 seconds for the streaming phase.\n"
    )
    anchor = anchor_for_quote(doc, TWIN_QUOTE)
    revised = "# Protocol\n\nThe timeout is 30 seconds for the streaming phase.\n"

    result = reanchor(anchor, revised)

    assert result.found
    assert result.strategy == QUOTE
    assert result.anchor.exact == TWIN_QUOTE


def test_a_twin_is_not_readmitted_by_the_fuzzy_rung():
    """A veto on one rung has to hold on the ones below it.

    Rung 4 scores the quote, and a twin matches the quote perfectly — so a floor
    that only lived on rungs 2 and 3 would hand the same wrong span back one rung
    later, wearing ``fuzzy`` instead of ``quote``.
    """
    revised = TWIN_DOC[: TWIN_DOC.index("## Section B")]
    anchor = anchor_for_quote(TWIN_DOC, TWIN_QUOTE, occurrence=1)

    result = reanchor(anchor, revised, min_similarity=0.0)

    assert result.orphaned


# -- the occurrence cap cuts around the old position ---------------------


def _repetitive(marker: str, count: int = 2000, target: int = 1700) -> str:
    """A document where one phrase repeats far past the occurrence cap."""
    lines = []
    for index in range(count):
        if index == target:
            lines.append(f"ratelimit section heading {marker} end of the ratelimit part")
        else:
            lines.append(f"line {index:04d} filler {marker} trailing filler {index:04d}")
    return "\n".join(lines) + "\n"


def test_the_occurrence_cap_does_not_cut_away_the_true_span():
    """The cap has to bound the work without deciding the answer.

    Cutting the scan at the top of the document throws away every occurrence
    past the cap, so a comment on the 1700th one never gets scored at all and
    loses to a stranger near the top — silently, on the ``quote`` rung. The cap
    stays; where it cuts moves to the old position.
    """
    from specround.reanchor import MAX_OCCURRENCES

    marker = "the retry policy applies"
    doc = _repetitive(marker)
    anchor = anchor_for_quote(doc, marker, occurrence=1700)
    assert doc.count(marker) > MAX_OCCURRENCES  # the cap is genuinely in play

    revised = "x" + doc  # one character above everything

    result = reanchor(anchor, revised)

    assert result.strategy == QUOTE
    assert result.anchor.start == anchor.start + 1
    result.anchor.verify(revised)


def test_the_occurrence_scan_stays_bounded_when_it_is_centred():
    from specround.reanchor import MAX_OCCURRENCES, _occurrences

    text = "ab" * 5000
    assert len(_occurrences(text, "ab")) == MAX_OCCURRENCES
    near = len(text) // 2
    around = _occurrences(text, "ab", near=near)
    assert len(around) == MAX_OCCURRENCES
    assert min(around) < near < max(around)  # it scanned both ways


# -- insertion points -----------------------------------------------------


def test_an_insertion_point_travels_on_its_context():
    from specround.anchors import anchor_for

    at = DOC.index("Timeouts")
    anchor = anchor_for(DOC, at, at)
    assert anchor.exact == ""
    result = reanchor(anchor, _insert_above(DOC))
    assert result.found
    assert result.anchor.exact == ""
    revised = _insert_above(DOC)
    assert revised[result.anchor.start :].startswith("Timeouts")


def test_an_insertion_point_with_no_context_cannot_be_looked_for():
    anchor = Anchor(exact="", start=500, end=500)
    result = reanchor(anchor, DOC)
    assert result.orphaned
    assert "neither quote nor context" in result.reason


def test_an_insertion_point_survives_at_its_old_offset_when_nothing_moved():
    anchor = Anchor(exact="", start=4, end=4)
    result = reanchor(anchor, DOC)
    assert result.strategy == POSITION


def test_an_insertion_point_orphans_when_its_context_is_gone():
    from specround.anchors import anchor_for

    at = DOC.index("Timeouts")
    anchor = anchor_for(DOC, at, at)
    result = reanchor(anchor, _rewrite_everything(DOC))
    assert result.orphaned


# -- the ladder is ordered, and every rung verifies ----------------------


def test_a_verbatim_hit_beats_a_fuzzy_one_even_when_it_moved_further():
    revised = "Timeouts are 31 seconds.\n\n" + DOC  # a near-miss sits above the real one
    result = reanchor(anchored(), revised)
    assert result.strategy == QUOTE
    assert result.anchor.exact == QUOTE_TEXT


def test_context_is_refreshed_from_the_revised_text():
    """The new anchor carries the new neighbourhood, not the one it came from."""
    revised = DOC.replace("The server answers", "The server replies")
    result = reanchor(anchored(), revised)
    assert result.anchor.prefix != anchored().prefix
    start, prefix = result.anchor.start, result.anchor.prefix
    assert revised[start - len(prefix) : start] == prefix


def test_a_stale_context_alone_does_not_orphan_a_quote_that_stayed_put():
    """Editing the line above must not cost the comment below its anchor.

    The replacement is the same length, so the quote has not moved by a single
    character — only the context around it changed. Rung 1 refuses that (an
    anchor whose context disagrees is stale by definition), and rung 2 has to
    catch it.
    """
    revised = DOC.replace("The server answers", "The server replies")
    anchor = anchored()
    assert anchor.matches(revised) is False
    result = reanchor(anchor, revised)
    assert result.strategy == QUOTE
    assert result.anchor.exact == QUOTE_TEXT
    assert (result.anchor.start, result.anchor.end) == (anchor.start, anchor.end)


# -- input handling -------------------------------------------------------


def test_reanchor_refuses_inputs_it_cannot_reason_about():
    with pytest.raises(AnchorError, match="expects an Anchor"):
        reanchor({"exact": "x", "start": 0, "end": 1}, DOC)
    with pytest.raises(AnchorError, match="text must be a string"):
        reanchor(anchored(), None)
    with pytest.raises(AnchorError, match="min_similarity"):
        reanchor(anchored(), DOC, min_similarity=1.5)


def test_rebind_reports_found_and_orphaned_as_opposites():
    hit = Rebind(anchored(), QUOTE)
    miss = Rebind(None, reason="gone")
    assert (hit.found, hit.orphaned) == (True, False)
    assert (miss.found, miss.orphaned) == (False, True)


def test_an_empty_document_orphans_everything():
    result = reanchor(anchored(), "")
    assert result.orphaned


# -- cost is bounded by constants, not by the document -------------------


def test_a_short_quote_in_a_long_document_stays_bounded():
    """The Hypothesis failure mode: a common short quote in a big file."""
    text = ("the server retries the request\n" * 4000) + "the server retries the frame\n"
    anchor = Anchor(exact="the frame", start=0, end=9)
    result = reanchor(anchor, text)
    assert result.found
    assert result.anchor.verify(text) is None


def test_candidate_generation_is_capped():
    from specround.reanchor import MAX_CANDIDATES, _candidates

    text = "repeat " * 5000
    assert len(_candidates(text, "repeat", 0)) <= MAX_CANDIDATES


def test_occurrence_scanning_is_capped():
    from specround.reanchor import MAX_OCCURRENCES, _occurrences

    assert len(_occurrences("ab" * 5000, "ab")) == MAX_OCCURRENCES
    assert _occurrences("abc", "") == []


# -- a randomised sweep over the same properties -------------------------


def _random_revision(rng, doc):
    """Apply a few random edits — the kind a reviewer's revision makes."""
    edits = rng.randint(1, 4)
    for _ in range(edits):
        choice = rng.randrange(4)
        if choice == 0:
            at = rng.randrange(len(doc) + 1)
            doc = doc[:at] + rng.choice(["\n", " ", "a note. ", "\n\n## Heading\n\n"]) + doc[at:]
        elif choice == 1 and len(doc) > 40:
            at = rng.randrange(len(doc) - 20)
            doc = doc[:at] + doc[at + rng.randint(1, 20) :]
        elif choice == 2:
            doc = doc.replace("frame", rng.choice(["frame", "packet", "message"]), 1)
        else:
            doc = doc.replace("  ", " ").replace("30", str(rng.randrange(10, 99)), 1)
    return doc


@pytest.mark.parametrize("seed", range(40))
def test_random_revisions_never_produce_an_anchor_that_does_not_verify(seed):
    """The one property that must hold no matter what the revision did."""
    rng = random.Random(seed)
    revised = _random_revision(rng, DOC)
    result = reanchor(anchored(), revised)
    assert isinstance(result, Rebind)
    if result.found:
        result.anchor.verify(revised)
        assert result.strategy in STRATEGIES
    else:
        assert result.reason


@pytest.mark.parametrize("seed", range(10))
def test_random_revisions_are_deterministic(seed):
    revised = _random_revision(random.Random(seed), DOC)
    anchor = anchored()
    assert reanchor(anchor, revised) == reanchor(anchor, revised)


def test_the_similarity_floor_is_a_documented_constant():
    assert 0.0 < MIN_SIMILARITY < 1.0
