"""The round diff, and the span an edit touched.

Both are pure functions of two texts, and both carry offsets that a comment or a
suggestion is about to be anchored by — so the assertions here are mostly "the
offsets name the text they claim to name".
"""

from specround.diffs import ADDED, REMOVED, SAME, changed_span, diff, unified_patch

BASE = "# Title\n\nfirst line\nsecond line\nthird line\n"


def ops(text_a, text_b):
    return [(row.op, row.text) for row in diff(text_a, text_b).rows]


def test_an_untouched_document_is_identical_and_all_same():
    computed = diff(BASE, BASE)
    assert computed.identical
    assert not computed.lines_changed
    assert {row.op for row in computed.rows} == {SAME}
    assert computed.added == computed.removed == 0


def test_same_rows_carry_offsets_in_both_texts():
    computed = diff(BASE, BASE)
    for row in computed.rows:
        assert row.base_start is not None and row.live_start is not None
        assert BASE[row.base_start : row.base_start + len(row.text)] == row.text


def test_an_inserted_line_is_added_and_has_no_base_offset():
    live = BASE.replace("second line\n", "second line\ninserted line\n")
    rows = [row for row in diff(BASE, live).rows if row.changed]
    assert [(row.op, row.text) for row in rows] == [(ADDED, "inserted line")]
    row = rows[0]
    assert row.base_start is None and row.base_line is None
    assert live[row.live_start : row.live_start + len(row.text)] == row.text


def test_a_deleted_line_is_removed_and_has_no_revision_offset():
    live = BASE.replace("second line\n", "")
    rows = [row for row in diff(BASE, live).rows if row.changed]
    assert [(row.op, row.text) for row in rows] == [(REMOVED, "second line")]
    row = rows[0]
    assert row.live_start is None and row.live_line is None
    assert BASE[row.base_start : row.base_start + len(row.text)] == row.text


def test_a_rewritten_line_reads_as_a_removal_and_an_addition():
    """Two texts, two rows: a reviewer may comment on either side of a rewrite."""
    live = BASE.replace("second line", "second line, revised")
    assert [(row.op, row.text) for row in diff(BASE, live).rows if row.changed] == [
        (REMOVED, "second line"),
        (ADDED, "second line, revised"),
    ]


def test_line_numbers_count_from_one_on_each_side():
    live = BASE.replace("first line\n", "")
    rows = diff(BASE, live).rows
    assert [(r.base_line, r.live_line) for r in rows] == [
        (1, 1), (2, 2), (3, None), (4, 3), (5, 4)
    ]


def test_a_lost_trailing_break_is_a_difference_no_line_can_show():
    """Snapshots keep the bytes they were given (§5), so this is a real change."""
    computed = diff(BASE, BASE.rstrip("\n"))
    assert not computed.identical
    assert not computed.lines_changed
    assert computed.only_terminator


def test_only_terminator_is_false_when_a_line_changed():
    computed = diff(BASE, BASE.replace("third", "fourth"))
    assert computed.lines_changed and not computed.only_terminator


def test_an_empty_document_against_a_full_one_is_all_additions():
    assert {op for op, _ in ops("", BASE)} == {ADDED}


def test_changed_span_is_none_when_nothing_changed():
    assert changed_span(BASE, BASE) is None


def test_changed_span_names_the_edited_region():
    proposed = BASE.replace("second line", "second row")
    span = changed_span(BASE, proposed)
    assert span is not None
    start, end = span
    # Snapped to the line, which is what a line-oriented patch is about.
    assert BASE[start:end] == "second line"


def test_changed_span_snaps_outward_to_whole_lines():
    """An anchor cutting a word in half reads as a bug in the view."""
    proposed = BASE.replace("first line", "first LINE")
    start, end = changed_span(BASE, proposed)
    assert BASE[start:end] == "first line"
    assert start == 0 or BASE[start - 1] == "\n"
    assert end == len(BASE) or BASE[end] == "\n"


def test_changed_span_without_snapping_is_the_minimal_difference():
    proposed = BASE.replace("first line", "first LINE")
    start, end = changed_span(BASE, proposed, snap=False)
    assert BASE[start:end] == "line"


def test_a_pure_insertion_is_an_empty_span_at_a_point():
    """An insertion point is a zero-length anchor (§5) — widening it would lie.

    "Add a line here" and "rewrite the line above" are different claims, and only
    one of them is what the reviewer typed.
    """
    at = BASE.index("second line")
    proposed = BASE[:at] + "inserted line\n" + BASE[at:]
    start, end = changed_span(BASE, proposed)
    assert start == end == at


def test_a_whole_rewrite_spans_the_whole_document():
    """The shared final newline is not part of the edit, and is not claimed."""
    start, end = changed_span(BASE, "completely different\n")
    assert (start, end) == (0, len(BASE) - 1)
    assert BASE[start:end] == BASE.rstrip("\n")


def test_unified_patch_reads_as_a_patch():
    proposed = BASE.replace("third line", "third line, revised")
    patch = unified_patch(BASE, proposed, label="spec.md")
    assert patch.startswith("--- a/spec.md")
    assert "+++ b/spec.md" in patch
    assert "-third line" in patch
    assert "+third line, revised" in patch


def test_unified_patch_is_empty_when_nothing_changed():
    assert unified_patch(BASE, BASE) == ""
