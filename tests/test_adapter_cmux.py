"""The reference converter, and the boundary it is on the far side of (H9).

The adapter is a standalone script that imports nothing from this package, so
these tests load it by path — the way a user runs it. What they are really
asserting is that the boundary holds in both directions: the converter produces
a file the core accepts without the core knowing what cmux is, and the core
places those comments without the converter knowing what an anchor is.

The cases that matter are the ones where a converter could quietly lose or
misplace something: a store holding two repositories with the same relative
path, a line the document no longer has where it was, a comment with nothing to
quote.
"""

import importlib.util
import json
from pathlib import Path

import pytest

from specround.imports import BY_QUOTE, BY_SPAN, parse_batch, plan_import

ADAPTER = Path(__file__).parents[1] / "adapters" / "cmux-diff-comments.py"

DOC_TEXT = """# Widget protocol

The client sends a hello frame.

Timeouts are 30 seconds. Retries are not specified yet.
"""


@pytest.fixture(scope="module")
def adapter():
    if not ADAPTER.is_file():
        pytest.skip(f"{ADAPTER} is not present in this layout")
    spec = importlib.util.spec_from_file_location("cmux_diff_comments", ADAPTER)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def a_comment(identifier="F3EF", *, path="spec.md", line=5, text="Timeouts are 30 seconds. Retries are not specified yet.",
              message="too short for the proxy", side="additions"):
    return {
        "id": identifier,
        "filePath": path,
        "startLine": line,
        "endLine": line,
        "lineText": text,
        "message": message,
        "side": side,
        "createdAt": "2026-08-06T06:07:16Z",
        "updatedAt": "2026-08-06T06:07:16Z",
    }


@pytest.fixture
def cmux_store(tmp_path):
    """A cmux diff-comments directory, written the way cmux writes one."""
    store = tmp_path / "diff-comments"
    store.mkdir()

    def add(repo_root, *comments, name="a.json"):
        (store / name).write_text(
            json.dumps({"repoRoot": str(repo_root), "comments": list(comments)}),
            encoding="utf-8",
        )
        return store

    return store, add


def run(adapter, *argv) -> dict:
    """Invoke the converter's main and hand back the JSON it wrote."""
    code = adapter.main([str(arg) for arg in argv])
    assert code == 0
    return json.loads(Path(argv[argv.index("--output") + 1]).read_text(encoding="utf-8"))


# -- collecting ----------------------------------------------------------


def test_it_emits_the_import_format(adapter, cmux_store, doc, tmp_path):
    store, add = cmux_store
    add(doc.parent, a_comment(path=doc.name))
    out = tmp_path / "out.json"
    payload = run(adapter, "--doc", doc, "--store", store, "--output", out)

    assert payload["schema"] == "specround.import/v0"
    assert payload["source"] == "cmux"
    # The core parses it without knowing what produced it — that is the boundary.
    parsed = parse_batch(payload)
    assert len(parsed.items) == 1
    assert parsed.items[0].id == "F3EF"
    assert parsed.items[0].ts == "2026-08-06T06:07:16Z"


def test_the_quote_is_the_line_cmux_captured(adapter, cmux_store, doc, tmp_path):
    store, add = cmux_store
    add(doc.parent, a_comment(path=doc.name, text="Timeouts are 30 seconds."))
    # The document says something else on that line now. The converter still
    # quotes what the reviewer saw — reconstructing from the file would silently
    # succeed against text nobody commented on.
    doc.write_text("# Widget protocol\n\nrewritten\n", encoding="utf-8")
    out = tmp_path / "out.json"
    payload = run(adapter, "--doc", doc, "--store", store, "--output", out)
    assert payload["comments"][0]["quote"] == "Timeouts are 30 seconds."


def test_documents_are_matched_by_path_not_by_name(adapter, cmux_store, tmp_path):
    # One store holds several repositories, and two of them having a docs/spec.md
    # is the normal case. Matching on the name alone would import a stranger's
    # comments onto this document.
    mine, theirs = tmp_path / "mine", tmp_path / "theirs"
    for root in (mine, theirs):
        (root / "docs").mkdir(parents=True)
        (root / "docs" / "spec.md").write_text(DOC_TEXT, encoding="utf-8")
    store, add = cmux_store
    add(mine, a_comment("mine", path="docs/spec.md"), name="a.json")
    add(theirs, a_comment("theirs", path="docs/spec.md"), name="b.json")

    out = tmp_path / "out.json"
    payload = run(adapter, "--doc", mine / "docs" / "spec.md", "--store", store, "--output", out)
    assert [c["id"] for c in payload["comments"]] == ["mine"]


def test_a_missing_store_is_an_error_not_an_empty_file(adapter, doc, tmp_path):
    with pytest.raises(SystemExit, match="not a directory"):
        adapter.main(["--doc", str(doc), "--store", str(tmp_path / "nope")])


def test_an_unreadable_file_does_not_hide_the_others(adapter, cmux_store, doc, tmp_path, capsys):
    store, add = cmux_store
    add(doc.parent, a_comment(path=doc.name), name="good.json")
    (store / "broken.json").write_text("{not json", encoding="utf-8")
    out = tmp_path / "out.json"
    payload = run(adapter, "--doc", doc, "--store", store, "--output", out)
    assert len(payload["comments"]) == 1
    assert "skipping broken.json" in capsys.readouterr().err


# -- what it will not represent ------------------------------------------


def test_a_comment_on_a_blank_line_is_dropped_and_said_so(adapter, cmux_store, doc, tmp_path, capsys):
    # A blank line gives nothing to anchor to. Promoting it to a comment on the
    # whole document would move it somewhere its author did not put it.
    store, add = cmux_store
    add(doc.parent, a_comment(path=doc.name, text="   "))
    out = tmp_path / "out.json"
    payload = run(adapter, "--doc", doc, "--store", store, "--output", out)
    assert payload["comments"] == []
    assert "the commented line is blank" in capsys.readouterr().err


def test_a_comment_with_no_message_is_dropped_and_said_so(adapter, cmux_store, doc, tmp_path, capsys):
    store, add = cmux_store
    add(doc.parent, a_comment(path=doc.name, message="  "))
    out = tmp_path / "out.json"
    assert run(adapter, "--doc", doc, "--store", store, "--output", out)["comments"] == []
    assert "no message" in capsys.readouterr().err


def test_a_multi_line_range_is_reported_because_only_one_line_was_stored(
    adapter, cmux_store, doc, tmp_path, capsys
):
    store, add = cmux_store
    comment = a_comment(path=doc.name)
    comment["endLine"] = comment["startLine"] + 3
    add(doc.parent, comment)
    out = tmp_path / "out.json"
    assert len(run(adapter, "--doc", doc, "--store", store, "--output", out)["comments"]) == 1
    assert "recorded lines 5-8" in capsys.readouterr().err


# -- offsets, which are an optional refinement ---------------------------


def test_spans_are_off_by_default(adapter, cmux_store, doc, tmp_path):
    store, add = cmux_store
    add(doc.parent, a_comment(path=doc.name))
    out = tmp_path / "out.json"
    assert "span" not in run(adapter, "--doc", doc, "--store", store, "--output", out)["comments"][0]


def test_with_span_emits_offsets_that_the_document_still_holds(adapter, cmux_store, doc, tmp_path, doc_text):
    store, add = cmux_store
    line = "Timeouts are 30 seconds. Retries are not specified yet."
    add(doc.parent, a_comment(path=doc.name, line=doc_text.splitlines().index(line) + 1, text=line))
    out = tmp_path / "out.json"
    payload = run(adapter, "--doc", doc, "--store", store, "--with-span", "--output", out)
    span = payload["comments"][0]["span"]
    assert doc_text[span["start"] : span["end"]] == line


def test_with_span_falls_back_to_the_quote_when_the_line_moved(adapter, cmux_store, doc, tmp_path):
    store, add = cmux_store
    line = "Timeouts are 30 seconds. Retries are not specified yet."
    add(doc.parent, a_comment(path=doc.name, line=1, text=line))  # line 1 is the heading
    out = tmp_path / "out.json"
    payload = run(adapter, "--doc", doc, "--store", store, "--with-span", "--output", out)
    # No guess: the offsets would have pointed at the heading.
    assert "span" not in payload["comments"][0]
    assert payload["comments"][0]["quote"] == line


def test_a_deletion_side_comment_never_gets_a_span(adapter, cmux_store, doc, tmp_path, capsys):
    # Its line number counts in the old version of the file, so looking it up in
    # the document as it is now would be arithmetic on two different texts.
    store, add = cmux_store
    add(doc.parent, a_comment(path=doc.name, side="deletions"))
    out = tmp_path / "out.json"
    payload = run(adapter, "--doc", doc, "--store", store, "--with-span", "--output", out)
    assert "span" not in payload["comments"][0]
    assert "counts in the old version" in capsys.readouterr().err


# -- the whole way through -----------------------------------------------


def test_converted_comments_anchor_in_a_round(adapter, cmux_store, doc, tmp_path, store, round_id, doc_text):
    cmux, add = cmux_store
    line = "Timeouts are 30 seconds. Retries are not specified yet."
    add(doc.parent, a_comment(path=doc.name, line=doc_text.splitlines().index(line) + 1, text=line))
    out = tmp_path / "out.json"
    payload = run(adapter, "--doc", doc, "--store", cmux, "--with-span", "--output", out)

    plan = plan_import(store, round_id, store.doc_key(doc), parse_batch(payload))
    assert plan.rejected == []
    assert [entry.how for entry in plan.planned] == [BY_SPAN]
    assert plan.planned[0].anchor.exact == line


def test_a_converted_comment_whose_text_is_gone_is_refused_not_placed(
    adapter, cmux_store, doc, tmp_path, store, round_id
):
    cmux, add = cmux_store
    add(doc.parent, a_comment(path=doc.name, text="a line this document never had"))
    out = tmp_path / "out.json"
    payload = run(adapter, "--doc", doc, "--store", cmux, "--output", out)

    plan = plan_import(store, round_id, store.doc_key(doc), parse_batch(payload))
    assert plan.planned == []
    assert "is not in the base" in plan.rejected[0].reason


def test_the_author_flag_stamps_every_comment(adapter, cmux_store, doc, tmp_path):
    cmux, add = cmux_store
    add(doc.parent, a_comment(path=doc.name))
    out = tmp_path / "out.json"
    payload = run(adapter, "--doc", doc, "--store", cmux, "--author", "bob", "--output", out)
    assert parse_batch(payload).items[0].author == "bob"


def test_without_an_author_the_importing_caller_owns_the_comment(
    adapter, cmux_store, doc, tmp_path, store, round_id
):
    # cmux stores no author, so an unstamped file leaves the field out and the
    # import records whoever ran it — rather than inventing a name.
    cmux, add = cmux_store
    add(doc.parent, a_comment(path=doc.name))
    out = tmp_path / "out.json"
    payload = run(adapter, "--doc", doc, "--store", cmux, "--output", out)
    assert parse_batch(payload).items[0].author is None

    plan = plan_import(store, round_id, store.doc_key(doc), parse_batch(payload))
    assert [entry.how for entry in plan.planned] == [BY_QUOTE]
