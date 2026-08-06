"""What the relocated store does to the loop (G5, G10).

The location rules are tested next door; this file asks the questions a user
asks. Where did my history go, does the folder I am reviewing in stay clean, can
I find it again tomorrow, and what happens when I opt back in.
"""

import json
from pathlib import Path

import pytest

from specround.errors import SpecroundError
from specround.locations import (
    CONFIG_FILENAME,
    DIRECTORY,
    DOCUMENT,
    ORIGIN_FILENAME,
    STORE_DIRNAME,
    Origin,
    central_store_dir,
    read_origin,
    write_origin,
)
from specround.store import ReviewStore

DOC_TEXT = "# spec\n\nTimeouts are 30 seconds.\n"


@pytest.fixture
def workdir(tmp_path):
    directory = tmp_path / "work"
    directory.mkdir()
    return directory


@pytest.fixture
def document(workdir):
    doc = workdir / "spec.md"
    doc.write_text(DOC_TEXT, encoding="utf-8")
    return doc


def a_round(store: ReviewStore, doc: Path) -> str:
    round_id = store.open_round(doc, author="alice", title="first pass")
    store.add_comment(round_id, author="bob", body="too short")
    return round_id


# -- the default ---------------------------------------------------------


def test_a_full_round_leaves_the_documents_folder_untouched(document, workdir, clock):
    store = ReviewStore.for_document(document, clock=clock)
    a_round(store, document)
    # Not "no .specround" — nothing at all. A default that adds a file to the
    # folder being reviewed is the thing this decision removed.
    assert [p.name for p in workdir.iterdir()] == ["spec.md"]
    assert store.fold().count == 2


def test_the_central_store_holds_the_whole_history(document, clock):
    store = ReviewStore.for_document(document, clock=clock)
    round_id = a_round(store, document)
    root = central_store_dir(document)
    assert store.root == root
    assert (root / "ledger.jsonl").is_file()
    assert (root / "objects").is_dir()
    assert (root / ORIGIN_FILENAME).is_file()
    assert store.snapshots.get_text(store.fold().rounds[round_id].base) == DOC_TEXT


def test_history_is_found_again_from_the_documents_path(document, clock):
    original = ReviewStore.for_document(document, clock=clock)
    a_round(original, document)
    # Tomorrow, another process, nothing carried over but the document path.
    assert ReviewStore.for_document(document).fold() == original.fold()


def test_editing_the_document_does_not_move_its_history(document, clock):
    store = ReviewStore.for_document(document, clock=clock)
    a_round(store, document)
    document.write_text(DOC_TEXT + "\nRetries are not specified.\n", encoding="utf-8")
    # The key is the path, not the contents — a revision is the normal case.
    assert ReviewStore.for_document(document).fold().count == 2


def test_the_document_is_named_by_its_own_name(document, clock):
    store = ReviewStore.for_document(document, clock=clock)
    round_id = a_round(store, document)
    assert store.fold().rounds[round_id].doc == "spec.md"
    assert store.doc_path("spec.md") == document


def test_a_central_store_refuses_a_document_that_is_not_its_own(document, workdir, clock):
    store = ReviewStore.for_document(document, clock=clock)
    sibling = workdir / "other.md"
    sibling.write_text("# other\n", encoding="utf-8")
    # The sibling has its own store. Writing it here would put two documents'
    # history under a key that names one of them.
    with pytest.raises(SpecroundError, match="not the document this store holds"):
        store.open_round(sibling, author="alice")


# -- finding a store from the other end ----------------------------------


def test_a_store_can_say_what_document_it_is_for(document, clock):
    ReviewStore.for_document(document, clock=clock).open_round(document, author="alice")
    reopened = ReviewStore.open(central_store_dir(document))
    assert reopened.origin == Origin(DOCUMENT, document.resolve())
    assert reopened.fold().count == 1


def test_the_origin_is_plain_text_a_reader_can_use(document, clock):
    ReviewStore.for_document(document, clock=clock).open_round(document, author="alice")
    line = (central_store_dir(document) / ORIGIN_FILENAME).read_text(encoding="utf-8")
    assert json.loads(line)["path"] == str(document.resolve())


def test_opening_something_that_is_not_a_store_says_so(tmp_path):
    (tmp_path / "empty").mkdir()
    with pytest.raises(SpecroundError, match="not a specround store"):
        ReviewStore.open(tmp_path / "empty")


def test_the_origin_is_written_once(document, clock):
    ReviewStore.for_document(document, clock=clock).open_round(document, author="alice")
    root = central_store_dir(document)
    before = (root / ORIGIN_FILENAME).read_text(encoding="utf-8")
    write_origin(root, Origin(DIRECTORY, Path("/somewhere/else")))
    # The record of where a store came from is the one thing a later re-binding
    # (H10) has to start from, so nothing overwrites it.
    assert (root / ORIGIN_FILENAME).read_text(encoding="utf-8") == before


def test_a_store_written_before_origins_existed_still_reads(document, workdir, clock):
    store = ReviewStore.for_document(document, store=workdir / STORE_DIRNAME, clock=clock)
    a_round(store, document)
    (store.root / ORIGIN_FILENAME).unlink()
    # An in-tree store with no breadcrumb is what v0 wrote: it serves its parent.
    reopened = ReviewStore.at(workdir)
    assert reopened.origin == Origin(DIRECTORY, workdir.resolve())
    assert reopened.fold().count == 2


# -- opting back into the tree -------------------------------------------


def test_the_beside_opt_in_puts_everything_back_in_the_folder(document, workdir, clock):
    (workdir / CONFIG_FILENAME).write_text('{"store": {"mode": "beside"}}', encoding="utf-8")
    store = ReviewStore.for_document(document, clock=clock)
    round_id = a_round(store, document)
    assert store.root == workdir / STORE_DIRNAME
    assert (workdir / STORE_DIRNAME / "ledger.jsonl").is_file()
    assert store.fold().rounds[round_id].doc == "spec.md"
    assert not central_store_dir(document).exists()


def test_a_shared_store_serves_every_document_under_the_config(tmp_path, clock):
    repo = tmp_path / "repo"
    (repo / "docs").mkdir(parents=True)
    (repo / "notes").mkdir()
    spec = repo / "docs" / "spec.md"
    spec.write_text(DOC_TEXT, encoding="utf-8")
    note = repo / "notes" / "design.md"
    note.write_text("# design\n", encoding="utf-8")
    (repo / CONFIG_FILENAME).write_text(
        '{"store": {"mode": "path", "path": "review"}}', encoding="utf-8"
    )

    store = ReviewStore.for_document(spec, clock=clock)
    first = store.open_round(spec, author="alice")
    second = ReviewStore.for_document(note, clock=clock).open_round(note, author="alice")

    assert store.root == repo / "review"
    # One ledger, and keys that count from the config file — so the same file
    # reads the same way in every clone.
    state = ReviewStore.for_document(note).fold()
    assert {state.rounds[first].doc, state.rounds[second].doc} == {
        "docs/spec.md",
        "notes/design.md",
    }


def test_moving_an_in_tree_store_with_its_folder_keeps_the_ledger_valid(tmp_path, clock):
    source = tmp_path / "before"
    source.mkdir()
    doc = source / "spec.md"
    doc.write_text(DOC_TEXT, encoding="utf-8")
    (source / CONFIG_FILENAME).write_text('{"store": {"mode": "beside"}}', encoding="utf-8")
    a_round(ReviewStore.for_document(doc, clock=clock), doc)

    destination = tmp_path / "after"
    source.rename(destination)

    # Relative document keys are what buys this: the store travelled with the
    # folder and the ledger did not have to be rewritten.
    moved = ReviewStore.for_document(destination / "spec.md")
    assert moved.root == destination / STORE_DIRNAME
    assert next(iter(moved.fold().rounds.values())).doc == "spec.md"


def test_an_explicit_store_overrides_the_config(document, workdir, tmp_path, clock):
    (workdir / CONFIG_FILENAME).write_text('{"store": {"mode": "beside"}}', encoding="utf-8")
    chosen = workdir / "elsewhere"
    store = ReviewStore.for_document(document, store=chosen, clock=clock)
    a_round(store, document)
    assert store.root == chosen
    assert not (workdir / STORE_DIRNAME).exists()
    assert read_origin(chosen) == Origin(DIRECTORY, workdir.resolve())


# -- what stays open -----------------------------------------------------


def test_a_renamed_document_starts_fresh_and_the_old_store_still_names_it(document, clock):
    a_round(ReviewStore.for_document(document, clock=clock), document)
    old_root = central_store_dir(document)

    renamed = document.parent / "protocol.md"
    document.rename(renamed)

    # H10, stated as a test rather than a promise: the history does not follow a
    # rename yet. What the breadcrumb buys is that it is not lost — the old store
    # still says, in plain text, which path it was opened for.
    assert ReviewStore.for_document(renamed).fold().count == 0
    assert read_origin(old_root) == Origin(DOCUMENT, document.resolve())
    assert ReviewStore.open(old_root).fold().count == 2
