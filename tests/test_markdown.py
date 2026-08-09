"""The render mode's one promise: what you see still knows its offset.

Every test here is a form of the same assertion — a run of text in the page can
be sliced back out of the document with the offsets it carries. That is what
lets a comment made on rendered prose become the same anchor a comment made on
the raw text would (G6), so it is checked over whole real documents rather than
over the constructs someone remembered to list.
"""

from pathlib import Path

import pytest

from specround.markdown import (
    Chunk,
    Piece,
    Run,
    code_spans,
    lines_of,
    render,
    runs_of,
)

REPO = Path(__file__).resolve().parent.parent

TORTURE = """# Heading with **bold** and `code`

A paragraph that wraps
across two source lines with *emphasis* and a [link](https://example.com/x).

> A quote with **bold**
> running over two lines.

- first item with `a_b` and data_start
- second item
  continued on an indented line
  - nested item
- third

1. ordered
2. also ordered

| Field | Meaning |
|---|---:|
| `exact` | the quoted string |
| start | a \\| pipe inside |

```json
{"schema":"specround.ledger/v0"}
```

---

Trailing paragraph with ~~strike~~ and an escaped \\*star\\*.
"""


def exactness(text: str) -> list[Run]:
    """Every run in ``text``'s rendering, asserted to describe ``text``."""
    found = list(runs_of(render(text)))
    for run in found:
        assert text[run.start : run.end] == run.text, (
            f"run at {run.start} claims {run.text!r} but the source has "
            f"{text[run.start:run.end]!r}"
        )
    return found


@pytest.mark.parametrize(
    "name",
    [
        "SPEC.md",
        "README.md",
        "docs/ledger-format.md",
        "docs/import-format.md",
        "docs/research/prior-art.md",
    ],
)
def test_runs_describe_the_document_they_came_from(name):
    """The invariant, over the documents this tool is actually used on."""
    text = (REPO / name).read_text(encoding="utf-8")
    found = exactness(text)
    assert len(found) > 50, "a real document should render into many runs"


def test_runs_describe_the_torture_document():
    assert exactness(TORTURE)


def test_runs_do_not_overlap_and_keep_document_order():
    """Rendering re-shapes the page, never the sequence of the prose.

    A reader scrolling the render sees the document in its own order, and a
    highlight painted from an anchor lands in one place. Both stop being true if
    two runs claim the same character.
    """
    found = exactness(TORTURE)
    for earlier, later in zip(found, found[1:]):
        assert earlier.end <= later.start, f"{earlier} overlaps or precedes {later}"


def test_a_paragraph_reaches_the_page_whole():
    text = "one two three\n"
    found = exactness(text)
    assert [(r.start, r.text) for r in found] == [(0, "one two three")]


def test_a_wrapped_paragraph_keeps_its_newline():
    """The break is a character of the document, so the run has to carry it."""
    text = "one two\nthree four\n"
    found = exactness(text)
    assert len(found) == 1
    assert found[0].text == "one two\nthree four"


def test_markers_are_not_runs():
    """Nothing in the document is what ``**`` means, so it is not anchorable."""
    text = "a **bold** end\n"
    found = exactness(text)
    assert [r.text for r in found] == ["a ", "bold", " end"]
    assert "<strong>" in render(text)


def test_heading_text_is_offset_past_the_hashes():
    text = "## Section two\n"
    found = exactness(text)
    assert [(r.start, r.text) for r in found] == [(3, "Section two")]
    assert "<h2>" in render(text)


def test_a_closing_run_of_hashes_is_decoration():
    text = "## Section ##\n"
    found = exactness(text)
    assert [(r.start, r.text) for r in found] == [(3, "Section")]


def test_blockquote_content_is_offset_past_the_marker():
    text = "> quoted line\n"
    found = exactness(text)
    assert [(r.start, r.text) for r in found] == [(2, "quoted line")]


def test_a_dedented_blockquote_splits_the_run_at_the_jump():
    """Two lines that are contiguous in the page are not in the file.

    The ``> `` between them is stripped, so one run spanning both would be
    wrong about every character after the first line. Splitting is the whole
    reason :class:`Chunk` keeps a map instead of a base offset.

    The break itself stays inside the first run, because it *is* a character of
    the document at that offset — dropping it would lose the whitespace the page
    needs between the two lines.
    """
    text = "> first\n> second\n"
    found = exactness(text)
    assert [(r.start, r.text) for r in found] == [(2, "first\n"), (10, "second")]


def test_list_item_content_is_offset_past_the_marker():
    text = "- an item\n- another\n"
    found = exactness(text)
    assert [(r.start, r.text) for r in found] == [(2, "an item"), (12, "another")]
    assert render(text).count("<li>") == 2


def test_an_indented_continuation_dedents_and_keeps_its_offset():
    text = "- an item\n  continued here\n"
    found = exactness(text)
    assert [(r.start, r.text) for r in found] == [(2, "an item\n"), (12, "continued here")]


def test_a_nested_list_becomes_a_nested_list():
    text = "- outer\n  - inner\n"
    html = render(text)
    assert html.count("<ul>") == 2
    assert exactness(text)


def test_ordered_lists_render_ordered():
    assert "<ol>" in render("1. first\n2. second\n")


def test_code_fence_keeps_its_lines_verbatim():
    text = '```json\n{"a": 1}\n```\n'
    found = exactness(text)
    assert [(r.start, r.text) for r in found] == [(8, '{"a": 1}')]
    assert 'data-lang="json"' in render(text)


def test_inline_code_is_a_run_without_its_backticks():
    text = "call `specround view` first\n"
    found = exactness(text)
    assert [r.text for r in found] == ["call ", "specround view", " first"]


def test_table_cells_carry_their_own_offsets():
    text = "| a | b |\n|---|---|\n| c | d |\n"
    found = exactness(text)
    assert [(r.start, r.text) for r in found] == [(2, "a"), (6, "b"), (22, "c"), (26, "d")]
    assert "<th" in render(text) and "<td" in render(text)


def test_table_alignment_comes_from_the_delimiter_row():
    html = render("| a | b | c |\n|:--|--:|:-:|\n| 1 | 2 | 3 |\n")
    assert "text-align:left" in html
    assert "text-align:right" in html
    assert "text-align:center" in html


def test_an_escaped_character_is_a_run_and_the_backslash_is_not():
    text = "a \\*not emphasis\\* b\n"
    found = exactness(text)
    assert "".join(r.text for r in found) == "a *not emphasis* b"
    assert "<em>" not in render(text)


def test_underscores_inside_a_word_are_not_emphasis():
    """``data_start`` names a field; italicising half of it would be a lie."""
    text = "the data_start and data_end fields\n"
    assert "<em>" not in render(text)
    assert exactness(text)


def test_an_unclosed_marker_stays_text():
    text = "a * lonely star\n"
    found = exactness(text)
    assert "".join(r.text for r in found) == "a * lonely star"


def test_a_link_anchors_on_its_label_not_its_target():
    text = "see [the spec](SPEC.md) now\n"
    found = exactness(text)
    assert [r.text for r in found] == ["see ", "the spec", " now"]
    assert 'href="SPEC.md"' in render(text)


def test_an_autolink_is_its_own_label():
    text = "<https://example.com/a>\n"
    found = exactness(text)
    assert [r.text for r in found] == ["https://example.com/a"]


def test_an_image_draws_its_reference_instead_of_linking_it():
    text = "![a shot](img/shot.png)\n"
    html = render(text)
    assert '<img src="img/shot.png"' in html
    assert 'alt="a shot"' in html


def test_the_text_around_an_image_keeps_its_offsets():
    """The invariant does not care that the image itself is not a run.

    It is soundness, not completeness: every run describes the source it claims.
    What follows an image has to still be counted from the right place, which is
    the half an image could plausibly break.
    """
    text = "before ![x](a.png) after\n"
    found = exactness(text)
    assert [r.text for r in found] == ["before ", " after"]


def test_an_image_label_is_not_a_run_and_the_raw_mode_is_where_it_anchors():
    """An `alt` is an attribute, so nothing on the page claims to be that text.

    Inventing a run for it would put a caption on the page the document does not
    have — and the same characters are ordinary anchorable text in the raw mode,
    which shares this anchor space.
    """
    assert [r.text for r in exactness("![a shot](img/shot.png)\n")] == []


def test_an_image_with_an_unsafe_source_falls_back_to_text_and_a_dead_link():
    """A broken `src` is worse than no image: some browsers re-request the page.

    So the construct degrades to what it renders as without image support at
    all — the `!` as text, the label as a link that goes nowhere.
    """
    html = render("![x](javascript:alert(1))\n")
    assert "<img" not in html
    assert 'href=""' in html


def test_an_escaped_bang_is_still_not_an_image():
    html = render("\\![x](a.png)\n")
    assert "<img" not in html
    assert 'href="a.png"' in html


def test_a_bang_that_is_not_followed_by_a_label_is_just_a_bang():
    assert [r.text for r in exactness("cost! and more\n")] == ["cost! and more"]


def test_a_thematic_break_renders_and_anchors_nothing():
    assert "<hr>" in render("a\n\n---\n\nb\n")


def test_html_is_escaped_without_changing_the_text():
    """Entities are longer as markup and identical as text — offsets survive."""
    text = "a <b> & c\n"
    html = render(text)
    assert "&lt;b&gt;" in html and "&amp;" in html
    assert exactness(text)


def test_crlf_line_endings_keep_their_offsets():
    """Snapshots preserve the bytes they were given, breaks included (§5)."""
    text = "one\r\ntwo\r\n"
    found = exactness(text)
    assert [(r.start, r.text) for r in found] == [(0, "one\r\ntwo")]


def test_chunk_maps_every_character_back():
    chunk = Chunk([Piece(10, "abc", "\n"), Piece(30, "de")])
    assert chunk.text == "abc\nde"
    assert [chunk.origin(i) for i in range(len(chunk))] == [10, 11, 12, 13, 30, 31]


def test_chunk_runs_split_only_where_the_source_jumps():
    chunk = Chunk([Piece(0, "ab", "\n"), Piece(3, "cd")])
    # Adjacent lines stay one run: the break sits exactly where the file has it,
    # so there is no jump to split at.
    assert chunk.runs(0, len(chunk)) == [Run(0, "ab\ncd")]


def test_chunk_joins_with_the_break_the_file_has():
    """A CRLF document is where a stand-in ``\\n`` stops being cosmetic."""
    chunk = Chunk([Piece(0, "ab", "\r\n"), Piece(4, "cd")])
    assert chunk.text == "ab\r\ncd"
    assert chunk.runs(0, len(chunk)) == [Run(0, "ab\r\ncd")]


def test_chunk_without_a_break_claims_nothing_between_pieces():
    """A piece with no tail is a separator, not a character of the document."""
    chunk = Chunk([Piece(0, "ab"), Piece(9, "cd")])
    assert chunk.text == "abcd"
    assert chunk.runs(0, len(chunk)) == [Run(0, "ab"), Run(9, "cd")]


def test_lines_of_counts_every_byte_of_a_break():
    assert lines_of("a\r\nb\n") == [Piece(0, "a", "\r\n"), Piece(3, "b", "\n")]


def test_empty_document_renders_to_nothing():
    assert render("") == ""
    assert list(runs_of(render(""))) == []


def test_a_javascript_link_loses_its_target_and_keeps_its_text():
    """A reviewed document is input, and the page it renders into holds a token."""
    text = "click [here](javascript:danger) now\n"
    html = render(text)
    assert 'href=""' in html
    assert "javascript" not in html
    found = exactness(text)
    assert [r.text for r in found] == ["click ", "here", " now"]


def test_a_data_url_is_refused_too():
    assert 'href=""' in render("[x](data:text/html;base64,PHNjcmlwdD4=)\n")


def test_relative_and_http_links_are_kept():
    assert 'href="SPEC.md"' in render("[spec](SPEC.md)\n")
    assert 'href="#section"' in render("[here](#section)\n")
    assert 'href="https://example.com"' in render("[site](https://example.com)\n")
    assert 'href="mailto:a@b.c"' in render("<mailto:a@b.c>\n")


def test_an_unsafe_autolink_keeps_its_text_and_goes_nowhere():
    """The label is document text and stays anchorable; only the target dies."""
    text = "<javascript:danger>\n"
    html = render(text)
    assert 'href=""' in html
    assert 'href="javascript' not in html
    assert [r.text for r in exactness(text)] == ["javascript:danger"]


# -- code, for the harvester ---------------------------------------------
#
# ``code_spans`` is not about rendering. It answers the harvester's question —
# "is this marker a specimen or an instruction" — and the assertion form that
# matters is the same one the rest of this file uses: the returned offsets have
# to slice the document back out.


def sliced(text):
    return [text[low:high] for low, high in code_spans(text)]


def test_an_inline_span_is_named_by_its_offsets():
    text = "Write `{--this--}` to propose a deletion.\n"
    assert sliced(text) == ["`{--this--}`"]


def test_a_fenced_block_includes_its_fences():
    text = "before\n\n```bash\nspecround harvest {--x--}\n```\n\nafter\n"
    assert sliced(text) == ["```bash", "specround harvest {--x--}", "```"]


def test_a_tilde_fence_is_a_fence_too():
    text = "~~~\n{>>inside<<}\n~~~\n"
    assert sliced(text) == ["~~~", "{>>inside<<}", "~~~"]


def test_a_fence_is_not_closed_by_one_carrying_an_info_string():
    text = "```\none\n``` python\ntwo\n```\n"
    # The middle line is content, not a close — otherwise "two" would read as
    # prose and a marker on it would be harvested.
    assert sliced(text) == ["```", "one", "``` python", "two", "```"]


def test_an_unclosed_fence_runs_to_the_end():
    assert sliced("```\n{--x--}\n") == ["```", "{--x--}"]


def test_several_spans_on_one_line():
    text = "`{++a++}` and `{--b--}` both.\n"
    assert sliced(text) == ["`{++a++}`", "`{--b--}`"]


def test_a_double_backtick_span_may_contain_a_single_one():
    text = "``a ` b`` tail\n"
    assert sliced(text) == ["``a ` b``"]


def test_a_backtick_pairs_with_the_next_run_of_its_own_length():
    text = "a ` b and `c` d\n"
    # CommonMark's rule, and worth a test because the result surprises: the first
    # tick pairs with the *next* single tick, so "c" ends up outside the span
    # rather than inside it. A reader of the rendered page sees the same thing.
    assert sliced(text) == ["` b and `"]


def test_a_run_that_never_closes_is_not_a_span():
    text = "propose it with {--x--} or ` alone\n"
    # The stray tick opens nothing, so the marker before it stays prose and is
    # harvested. Over-reaching here would silently swallow a real annotation,
    # and nothing surfaces that.
    assert sliced(text) == []


def test_prose_with_no_code_has_no_spans():
    assert code_spans("Just a sentence.\n\nAnd another.\n") == []


def test_spans_are_ordered_and_disjoint():
    text = "`a` b `c`\n\n```\nd\n```\n\n`e`\n"
    spans = code_spans(text)
    assert spans == sorted(spans)
    assert all(spans[i][1] <= spans[i + 1][0] for i in range(len(spans) - 1))


@pytest.mark.parametrize("name", ["SPEC.md", "README.md", "docs/ledger-format.md"])
def test_every_marker_in_this_repo_is_inside_code(name):
    """The documents this tool reviews quote the syntax it harvests.

    Not a style check. Before ``code_spans`` existed, harvesting SPEC.md read
    eight of its specimens as review comments and deleted them from the prose,
    and docs/research/prior-art.md could not be harvested at all. The rule is
    what lets this feature be documented in the documents it operates on.
    """
    text = (REPO / name).read_text(encoding="utf-8")
    spans = code_spans(text)
    for opener in ("{>>", "{++", "{--", "{~~", "{=="):
        at = text.find(opener)
        while at != -1:
            assert any(low <= at < high for low, high in spans), (
                f"{name}: {opener} at offset {at} is in prose — the harvester would eat it"
            )
            at = text.find(opener, at + 1)
