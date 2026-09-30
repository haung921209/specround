import pytest

from specround.reviews import Review
from specround.webview import WebView, derived_port
from specround.workspace import Workspace
from specround.viewtokens import token_for
from test_webview import call, state


@pytest.fixture
def scoped_views(tmp_path):
    for name in ("a.md", "b.md"):
        (tmp_path / name).write_text("Shared text.\n", encoding="utf-8")
    reviews = [Review.create([tmp_path], title=name, author="alice") for name in ("A", "B")]
    views = []
    for review in reviews:
        view = WebView(store=review.store, path=tmp_path / "a.md", author="alice", port=0,
                       token=token_for(review.directory)[0], review=review,
                       workspace=Workspace(tmp_path, review=review), doc="a.md")
        view.start()
        views.append(view)
    try:
        yield reviews, views
    finally:
        for view in views:
            view.shutdown()


def test_review_views_have_distinct_identity_and_isolated_reads_and_writes(scoped_views):
    reviews, (a, b) = scoped_views
    assert a.port_path != b.port_path and a.token != b.token
    _, result = call(a, "/api/comment", {"whole": True, "body": "only A"})
    cid = result["comment"]["id"]
    assert state(a)["scope"]["id"] == reviews[0].id
    assert state(b)["comments"] == []
    before = reviews[0].store.ledger.count()
    assert call(a, "/api/dispose", {"doc": "b.md", "target": cid, "verdict": "answered", "reason": "wrong document"})[0] == 409
    assert reviews[0].store.ledger.count() == before
    assert call(b, "/api/reply", {"target": cid, "body": "wrong review"})[0] == 409
    assert call(b, "/api/state", token=a.token)[0] == 403
    assert call(a, "/api/state", params={"doc": "outside.md"})[0] == 400


def test_new_directory_members_are_visible_only_after_scope_refresh(scoped_views, tmp_path):
    reviews, (a, b) = scoped_views
    (tmp_path / "new.md").write_text("New file.\n", encoding="utf-8")
    assert call(a, "/api/review")[1]["scope"]["new_files"] == ["new.md"]
    assert call(a, "/api/state", params={"doc": "new.md"})[0] == 400
    assert call(a, "/api/scope-refresh", {})[0] == 200
    assert call(a, "/api/state", params={"doc": "new.md"})[1]["base"] == "New file.\n"
    assert call(b, "/api/state", params={"doc": "new.md"})[0] == 400
    (tmp_path / "b.md").unlink()
    historical = call(a, "/api/state", params={"doc": "b.md"})[1]
    assert historical["live"] is None and historical["base"] == "Shared text.\n"


def test_selected_member_cannot_be_replaced_with_an_outside_symlink(scoped_views, tmp_path):
    _, (a, _) = scoped_views
    outside = tmp_path.parent / (tmp_path.name + "-private.md")
    outside.write_text("PRIVATE\n", encoding="utf-8")
    (tmp_path / "a.md").unlink()
    (tmp_path / "a.md").symlink_to(outside)
    status, payload = call(a, "/api/state")
    assert status == 400
    assert "PRIVATE" not in str(payload)
