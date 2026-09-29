import pytest

from specround.errors import InvariantError
from specround.events import ROUND_OPEN
from specround.webview import WebView


def test_refresh_keeps_round_and_original_anchor_while_publishing_new_text(store, doc, round_id, doc_text):
    original_base = store.round_base(round_id)
    anchor = store.anchor_in_round(round_id, "30 seconds")
    cid = store.add_comment(round_id, author="bob", body="check", anchor=anchor)
    store.dispose(cid, author="alice", verdict="answered", reason="explained")
    store.resolve(cid, author="alice", actor="human")
    doc.write_text("New introduction.\n\n" + doc_text, encoding="utf-8")
    report = store.refresh_round(round_id, doc, author="alice")
    state = store.fold()
    round_ = state.rounds[round_id]
    assert len(state.rounds) == 1 and round_.open
    assert round_.initial_base == original_base
    assert round_.base == report.base != original_base
    assert len(round_.revisions) == 2
    comment = state.comments[cid]
    assert comment.anchor == anchor and comment.base == original_base
    assert comment.current_anchor.start == anchor.start + 19
    assert comment.resolved and comment.verdict == "answered" and not comment.misplaced
    assert cid in report.rebound
    assert store.snapshots.get_text(original_base) == doc_text
    assert store.base_text(round_id) == doc.read_text()
    assert WebView(store=store, path=doc, author="alice").state_payload()["base"] == doc.read_text()
    count = store.ledger.count()
    store.refresh_round(round_id, doc, author="alice")
    assert store.ledger.count() == count


def test_new_comments_anchor_to_the_current_revision_and_survive_another_refresh(store, doc, round_id):
    doc.write_text("New content.\n", encoding="utf-8")
    store.refresh_round(round_id, doc, author="alice")
    base = store.round_base(round_id)
    cid = store.add_comment(round_id, author="bob", body="check new text",
                            anchor=store.anchor_in_round(round_id, "New content."))
    doc.write_text("Introduction.\n\nNew content.\n", encoding="utf-8")
    store.refresh_round(round_id, doc, author="alice")
    comment = store.fold().comments[cid]
    assert comment.base == base
    assert comment.anchor.start == 0
    assert comment.current_anchor.start == 15


def test_refresh_records_orphans_without_guessing_a_new_location(store, doc, round_id):
    cid = store.add_comment(round_id, author="bob", body="check",
                            anchor=store.anchor_in_round(round_id, "30 seconds"))
    original = store.fold().comments[cid].anchor
    doc.write_text("Entirely different material.\n", encoding="utf-8")
    report = store.refresh_round(round_id, doc, author="alice")
    assert report.orphaned == [cid]
    comment = store.fold().comments[cid]
    assert comment.orphaned and comment.anchor == original and not comment.resolved


def test_refresh_refuses_closed_round_and_concurrent_changes(store, doc, round_id, monkeypatch):
    append = store.ledger.append
    doc.write_text("Changed.\n", encoding="utf-8")
    def racing(record, **kwargs):
        if record["type"] == "round.refresh":
            append({"type": "comment.add", "round": round_id, "author": "bob", "body": "arrived during refresh"})
        return append(record, **kwargs)
    monkeypatch.setattr(store.ledger, "append", racing)
    original = store.round_base(round_id)
    with pytest.raises(InvariantError, match="changed"):
        store.refresh_round(round_id, doc, author="alice")
    assert store.round_base(round_id) == original
    monkeypatch.setattr(store.ledger, "append", append)
    store.close_round(round_id, author="alice", allow_undisposed=True, allow_unresolved=True)
    with pytest.raises(InvariantError, match="closed"):
        store.refresh_round(round_id, doc, author="alice")


def test_legacy_round_can_be_read_and_refreshed(store, doc):
    base = store.snapshots.put_file(doc)
    record = store.ledger.append({"schema": "specround.ledger/v0", "type": ROUND_OPEN,
                                  "doc": store.doc_key(doc), "base": base, "author": "old"})
    doc.write_text("Published later.\n", encoding="utf-8")
    store.refresh_round(record["id"], doc, author="alice")
    assert store.fold().rounds[record["id"]].initial_base == base


def test_comment_write_checks_revision_identity_at_append_even_after_bytes_return(store, doc, round_id, doc_text):
    before = store.fold().rounds[round_id]
    anchor = store.anchor_in_round(round_id, "30 seconds")
    doc.write_text("Between.\n" + doc_text, encoding="utf-8")
    store.refresh_round(round_id, doc, author="alice")
    doc.write_text(doc_text, encoding="utf-8")
    store.refresh_round(round_id, doc, author="alice")
    with pytest.raises(InvariantError, match="outdated"):
        store.add_comment(round_id, author="bob", body="stale", anchor=anchor,
                          expected_base=before.base, expected_review=before.revision)
    assert not store.fold().comments


def test_crlf_file_matches_its_published_snapshot(store, doc):
    doc.write_bytes(b"# Windows\r\n\r\nContent.\r\n")
    store.open_round(doc, author="alice")
    view = WebView(store=store, path=doc, author="alice")
    payload = view.state_payload()
    assert payload["review"]["matches"] is True
    assert payload["diff"]["identical"] is True


def test_refresh_rejects_an_old_publication_request(store, doc, round_id, doc_text):
    before = store.fold().rounds[round_id].revision
    doc.write_text("New.\n" + doc_text, encoding="utf-8")
    store.refresh_round(round_id, doc, author="alice")
    with pytest.raises(InvariantError, match="revision changed"):
        store.refresh_round(round_id, doc, author="bob", expected_review=before)
