"""A review is an explicit namespace, including for overlapping documents."""
from pathlib import Path

import pytest

from specround.errors import SpecroundError
from specround.reviews import Review
from specround.reviews import PartialReviewError


def test_overlapping_reviews_have_independent_history(tmp_path):
    a, b, c = [tmp_path / name for name in ("a.md", "b.md", "c.md")]
    for doc in (a, b, c):
        doc.write_text("Shared sentence.\n", encoding="utf-8")
    first = Review.create([a, b], title="First", author="alice")
    second = Review.create([b, c], title="Second", author="alice")
    r1 = first.store.latest_round(b)
    cid = first.store.add_comment(r1.id, author="bob", body="first review only",
                                  anchor=first.store.anchor_in_round(r1.id, "Shared sentence."))
    assert first.id != second.id and first.directory != second.directory
    assert cid not in second.store.fold().comments
    original = second.store.latest_round(b).base
    b.write_text("New paragraph.\n\nShared sentence.\n", encoding="utf-8")
    first.refresh(author="alice")
    assert second.store.latest_round(b).base == original
    assert not second.store.fold().comments
    assert first.store.fold().comments[cid].current_anchor.start == 16
    assert second.review_status(b)["next_action"] == {
        "verb": "review.refresh", "review_id": second.id, "when": "changes_ready_for_review"}
    second.close(author="alice")
    assert second.review_status(b)["next_action"] is None
    assert "new named review" in second.review_status(b)["message"]


def test_directory_detects_new_files_but_publishes_them_only_on_refresh(tmp_path):
    a = tmp_path / "a.md"
    a.write_text("First.\n", encoding="utf-8")
    review = Review.create([tmp_path], title="Directory", author="alice")
    b = tmp_path / "b.md"
    b.write_text("Second.\n", encoding="utf-8")
    assert review.members() == ["a.md"]
    assert review.describe()["new_files"] == ["b.md"]
    with pytest.raises(SpecroundError, match="not.*review|not.*published"):
        review.resolve_key("b.md")
    review.refresh(author="alice")
    assert review.members() == ["a.md", "b.md"]
    assert review.describe()["new_files"] == []
    a.unlink()
    assert "a.md" in review.members()
    assert review.describe()["missing_files"] == ["a.md"]
    review.refresh(author="alice")
    assert review.store.base_text(review.store.latest_round(a).id) == "First.\n"


def test_explicit_file_list_does_not_grow_and_scope_keys_cannot_escape(tmp_path):
    a = tmp_path / "a.md"
    a.write_text("A\n", encoding="utf-8")
    review = Review.create([a], title="Files", author="alice")
    (tmp_path / "b.md").write_text("B\n", encoding="utf-8")
    assert review.describe()["new_files"] == []
    for key in ("b.md", "../a.md", str(a)):
        with pytest.raises(SpecroundError):
            review.resolve_key(key)
    assert Review.load(review.id).members() == ["a.md"]


def test_directory_does_not_follow_links_outside_its_root(tmp_path):
    root = tmp_path / "docs"
    root.mkdir()
    (root / "a.md").write_text("A\n", encoding="utf-8")
    outside = tmp_path / "outside.md"
    outside.write_text("Private\n", encoding="utf-8")
    (root / "escape.md").symlink_to(outside)
    review = Review.create([root], title="Safe boundary", author="alice")
    assert review.members() == ["a.md"]


def test_close_checks_every_member_before_closing_any(tmp_path):
    docs = [tmp_path / "a.md", tmp_path / "b.md"]
    for doc in docs:
        doc.write_text("Text\n", encoding="utf-8")
    review = Review.create(docs, title="Close", author="alice")
    r = review.store.latest_round(docs[1])
    review.store.add_comment(r.id, author="bob", body="unfinished")
    with pytest.raises(SpecroundError, match="undisposed|unresolved"):
        review.close(author="alice")
    assert len(review.store.fold().open_rounds) == 2


def test_partial_refresh_reports_successes_without_hiding_the_failure(tmp_path, monkeypatch):
    docs = [tmp_path / name for name in ("a.md", "b.md")]
    for doc in docs:
        doc.write_text("Before\n", encoding="utf-8")
    review = Review.create(docs, title="Partial", author="alice")
    for doc in docs:
        doc.write_text("After\n", encoding="utf-8")
    original = review.store.refresh_round
    def fail_second(round_id, doc, **kwargs):
        if doc.name == "b.md":
            raise OSError("disk failure")
        return original(round_id, doc, **kwargs)
    monkeypatch.setattr(review.store, "refresh_round", fail_second)
    with pytest.raises(PartialReviewError) as failed:
        review.refresh(author="alice")
    assert [r["status"] for r in failed.value.report["results"]] == ["updated", "error"]
    assert review.store.base_text(review.store.latest_round(docs[0]).id) == "After\n"
    assert review.store.base_text(review.store.latest_round(docs[1]).id) == "Before\n"


def test_invalid_input_does_not_publish_a_partial_review(tmp_path):
    a, b = tmp_path / "a.md", tmp_path / "b.md"
    a.write_text("Valid\n", encoding="utf-8")
    b.write_bytes(b"\xff")
    with pytest.raises(SpecroundError):
        Review.create([a, b], title="Invalid", author="alice")
    assert Review.all() == []
