"""A directory served as one workspace (H15).

Two halves, and they check different claims.

**Discovery is a contract.** What the walk lists, what it steps over, and what
it holds back are answers a reviewer acts on — a document missing from the bar
is a document nobody reviews, so "skipped it quietly" is the failure mode this
half exists to catch.

**Navigation is only navigation.** The rest goes over a real socket, and every
assertion ends at a *per-document* ledger. That is the claim the workspace layer
has to keep: switching documents in the bar changes which document answers, and
nothing else — no workspace round, no shared anchor space, no second copy of a
rule. A comment posted while looking at one document must land in that
document's store and nowhere near its neighbour's.
"""

import os

import pytest

from specround.locations import canonical_path
from specround.store import ReviewStore
from specround.webview import WebView
from specround.workspace import DEFAULT_LIMIT, Workspace
from test_webview import call, in_node, state

ALPHA = "# Alpha\n\nThe first document. It says a thing.\n"
BETA = "# Beta\n\nThe second document. It says another.\n"
GAMMA = "# Gamma\n\nThe third document, quiet so far.\n"


@pytest.fixture
def tree(tmp_path):
    """A small tree: two documents at the top, one in a folder, noise around it."""
    root = tmp_path / "specs"
    (root / "sub").mkdir(parents=True)
    (root / "alpha.md").write_text(ALPHA, encoding="utf-8")
    (root / "gamma.md").write_text(GAMMA, encoding="utf-8")
    (root / "sub" / "beta.md").write_text(BETA, encoding="utf-8")
    (root / "notes.txt").write_text("not a document\n", encoding="utf-8")
    hidden = root / ".hidden"
    hidden.mkdir()
    (hidden / "secret.md").write_text("# Secret\n", encoding="utf-8")
    return root


@pytest.fixture
def space(tree, clock):
    return Workspace(root=tree, clock=clock)


@pytest.fixture
def reviewed(tree, clock):
    """``sub/beta.md`` with a round open on it and one undisposed comment."""
    path = tree / "sub" / "beta.md"
    store = ReviewStore.for_document(path, clock=clock)
    round_id = store.open_round(path, author="alice", title="beta pass")
    store.add_comment(
        round_id,
        author="bob",
        body="which one?",
        anchor=store.anchor_in_round(round_id, "another"),
    )
    return store


@pytest.fixture
def view(tree, space):
    """A running workspace view, opened on the first document in path order."""
    opening = space.list().documents[0]
    served = WebView(
        store=space.store_for(opening.path),
        path=opening.path,
        author="alice",
        workspace=space,
        doc=opening.key,
    )
    served.start()
    try:
        yield served
    finally:
        served.shutdown()


def keys(listing):
    return [document.key for document in listing.documents]


def entry(payload, key):
    for document in payload["workspace"]["documents"]:
        if document["key"] == key:
            return document
    raise AssertionError(f"{key} is not in the listing: {keys}")


# -- what the walk finds -------------------------------------------------


def test_the_listing_is_the_markdown_under_the_root_in_path_order(space):
    """Sorted, so the bar does not depend on the order a filesystem answers in."""
    assert keys(space.list()) == ["alpha.md", "gamma.md", "sub/beta.md"]


def test_a_file_that_is_not_markdown_is_not_a_document(space):
    """The renderer's scope is markdown (H11). Listing the rest is listing dead ends."""
    assert not [key for key in keys(space.list()) if key.endswith(".txt")]


def test_dotted_names_are_skipped_whole(space, tree):
    """One rule instead of a list of names that goes stale.

    ``.git`` is the obvious one and ``.specround`` is the pointed one: an
    in-tree store is full of markdown-adjacent noise, and a bar that offered to
    review the review would be its own kind of wrong.
    """
    (tree / ".specround").mkdir()
    (tree / ".specround" / "notes.md").write_text("# inside the store\n", encoding="utf-8")
    assert keys(space.list()) == ["alpha.md", "gamma.md", "sub/beta.md"]


def test_a_directory_link_is_followed_once_and_the_second_pass_is_reported(space, tree):
    """A link back up the tree ends a branch instead of the process.

    And it is counted: a reviewer looking for a file the bar decided not to
    list deserves to know a decision was made.
    """
    os.symlink(tree, tree / "loop")
    listing = space.list()
    assert keys(listing) == ["alpha.md", "gamma.md", "sub/beta.md"]
    assert listing.revisits == 1
    assert "already visited" in listing.note


def test_a_link_to_another_folder_is_followed(space, tree, tmp_path):
    """Following links is worth doing — a docs tree pointing at a shared folder is ordinary."""
    shared = tmp_path / "shared"
    shared.mkdir()
    (shared / "delta.md").write_text("# Delta\n", encoding="utf-8")
    os.symlink(shared, tree / "linked")
    assert "linked/delta.md" in keys(space.list())


def test_an_unreadable_directory_is_stepped_over_rather_than_fatal(space, tree):
    """A listing that dies on one bad permission is a listing nobody can use."""
    walled = tree / "walled"
    walled.mkdir()
    (walled / "inside.md").write_text("# Inside\n", encoding="utf-8")
    walled.chmod(0o000)
    try:
        assert keys(space.list()) == ["alpha.md", "gamma.md", "sub/beta.md"]
    finally:
        walled.chmod(0o755)


# -- the badges ----------------------------------------------------------


def test_a_document_nobody_has_reviewed_has_no_activity_and_no_error(space):
    """The common case in a real tree, and it is not an error."""
    quiet = {document.key: document for document in space.list().documents}["alpha.md"]
    assert not quiet.active
    assert quiet.error is None
    assert quiet.summary["rounds"] == 0
    assert quiet.summary["undisposed"] == 0
    assert quiet.summary["last_activity"] is None


def test_the_badge_counts_come_from_that_documents_own_store(space, reviewed):
    """The numbers a bar shows are the fold's, not a second count kept here."""
    listed = {document.key: document for document in space.list().documents}
    assert listed["sub/beta.md"].active
    assert listed["sub/beta.md"].summary["open_rounds"] == 1
    assert listed["sub/beta.md"].summary["undisposed"] == 1
    assert listed["sub/beta.md"].summary["comments"] == 1
    assert listed["sub/beta.md"].store == reviewed.root
    # And the neighbours are untouched: one store per document is the default.
    assert not listed["alpha.md"].active
    assert listed["alpha.md"].store != reviewed.root


def test_a_document_whose_history_will_not_fold_is_listed_with_its_reason(space, tree, reviewed):
    """One broken ledger must not blank the bar — nor be hidden by it."""
    (reviewed.root / "ledger.jsonl").write_text("{not json\n", encoding="utf-8")
    listed = {document.key: document for document in space.list().documents}
    assert set(listed) == {"alpha.md", "gamma.md", "sub/beta.md"}
    assert listed["sub/beta.md"].error
    assert listed["alpha.md"].error is None


def test_activity_is_any_round_not_just_a_comment(space, tree, clock):
    """Somebody opened a review here. That is what the filter is asked to find."""
    path = tree / "alpha.md"
    ReviewStore.for_document(path, clock=clock).open_round(path, author="alice", title="a look")
    listed = {document.key: document for document in space.list().documents}
    assert listed["alpha.md"].active
    assert listed["alpha.md"].summary["comments"] == 0


# -- the limit -----------------------------------------------------------


def test_the_limit_holds_documents_back_and_says_how_many(tree, clock):
    """No silent truncation: what is not shown is counted and named."""
    for index in range(6):
        (tree / f"extra-{index}.md").write_text(f"# Extra {index}\n", encoding="utf-8")
    listing = Workspace(root=tree, clock=clock, limit=4).list()
    assert listing.found == 9
    assert len(listing.documents) == 4
    assert listing.hidden == 5
    assert "5 document(s) not listed" in listing.note


def test_the_limit_never_hides_a_document_with_review_activity(tree, clock, reviewed):
    """A cap that could hide a reviewed document would make the filter lie."""
    for index in range(20):
        (tree / f"extra-{index}.md").write_text(f"# Extra {index}\n", encoding="utf-8")
    listing = Workspace(root=tree, clock=clock, limit=2).list()
    assert "sub/beta.md" in keys(listing)
    assert listing.hidden == listing.found - len(listing.documents)


def test_the_default_limit_is_not_in_the_way_of_an_ordinary_tree(space):
    assert space.limit == DEFAULT_LIMIT
    assert space.list().hidden == 0


# -- when the documents share one store ----------------------------------
#
# The default is a store per document, which is why most of the above reads like
# it. A config that puts one store over the folder is the other layout the
# resolution rules allow, and the listing has to be right on both — the badges
# come from one fold indexed by each document's own key, not from a second way
# of counting kept for this case.


@pytest.fixture
def shared(tree):
    """One in-tree store for the whole tree, the way a team opts into sharing.

    ``path`` rather than ``beside``: ``beside`` puts a store in each *document's*
    folder, so a tree with subfolders gets several of them and ``sub/beta.md``
    would be keyed ``beta.md`` under its own. ``path`` anchors at the config
    file, which is what makes one store hold the whole tree under the same keys
    the workspace uses.
    """
    (tree / ".specround.json").write_text(
        '{"store": {"mode": "path", "path": ".specround"}}\n', encoding="utf-8"
    )
    return ReviewStore.at(tree)


def test_documents_sharing_a_store_are_told_apart_by_their_own_keys(tree, shared, clock):
    """A shared ledger holds several documents. Each row must be about its own."""
    alpha, beta = tree / "alpha.md", tree / "sub" / "beta.md"
    store = ReviewStore.for_document(alpha, clock=clock)
    assert store.root == shared.root  # the config is in force
    round_id = store.open_round(alpha, author="alice", title="alpha pass")
    store.add_comment(round_id, author="bob", body="on alpha", anchor=None)
    store.open_round(beta, author="alice", title="beta pass")
    listed = {document.key: document for document in Workspace(root=tree, clock=clock).list().documents}
    assert listed["alpha.md"].summary["comments"] == 1
    assert listed["sub/beta.md"].summary["comments"] == 0
    assert listed["gamma.md"].summary["rounds"] == 0
    assert {d.store for d in listed.values()} == {shared.root}


def test_a_shared_ledger_is_folded_once_for_the_whole_listing(tree, shared, clock, monkeypatch):
    """Not once per document: a bar over a folder would re-read the same file N times."""
    folds = []
    original = ReviewStore.fold
    monkeypatch.setattr(
        ReviewStore, "fold", lambda self: (folds.append(self.root), original(self))[1]
    )
    path = tree / "alpha.md"
    ReviewStore.for_document(path, clock=clock).open_round(path, author="alice", title="a look")
    folds.clear()
    assert len(Workspace(root=tree, clock=clock).list().documents) == 3
    assert folds == [shared.root]


def test_an_explicit_store_applies_to_every_document_in_the_tree(tree, tmp_path, clock):
    """``--store`` means the same thing here as it does for every other verb."""
    elsewhere = tmp_path / "history"
    path = tree / "alpha.md"
    store = ReviewStore.for_document(path, store=elsewhere, base=tree, clock=clock)
    store.open_round(path, author="alice", title="a look")
    listed = {
        document.key: document
        for document in Workspace(root=tree, store=elsewhere, clock=clock).list().documents
    }
    assert {d.store for d in listed.values()} == {store.root}
    assert listed["alpha.md"].active
    assert not listed["sub/beta.md"].active


# -- turning a key back into a document ----------------------------------


def test_a_key_resolves_to_the_document_under_the_root(space, tree):
    assert space.resolve("sub/beta.md") == canonical_path(tree / "sub" / "beta.md")


@pytest.mark.parametrize(
    "key, because",
    [
        ("../outside.md", "steps outside"),
        ("sub/../../outside.md", "steps outside"),
        ("/etc/passwd.md", "absolute path"),
        (".hidden/secret.md", "hidden entry"),
        ("notes.txt", "not a markdown document"),
        ("nowhere.md", "no document"),
        ("", "must not be empty"),
    ],
)
def test_a_key_that_would_address_another_file_is_refused(space, key, because):
    """A mistyped or stale key must not quietly open some other history."""
    with pytest.raises(Exception) as raised:
        space.resolve(key)
    assert because in str(raised.value)


def test_a_held_back_document_is_still_addressable(tree, clock):
    """The limit is a display decision, not a shorter list of what exists."""
    for index in range(6):
        (tree / f"extra-{index}.md").write_text(f"# Extra {index}\n", encoding="utf-8")
    space = Workspace(root=tree, clock=clock, limit=1)
    listing = space.list()
    missing = next(key for key in ("extra-5.md", "gamma.md") if key not in keys(listing))
    assert space.resolve(missing).is_file()


# -- the bar over HTTP ---------------------------------------------------


def test_the_state_payload_carries_the_bar_and_the_document_together(view, reviewed):
    """One answer, one fold: a bar fetched apart from its panel can disagree with it."""
    payload = state(view)
    assert payload["workspace"]["selected"] == "alpha.md"
    assert payload["doc"] == "alpha.md"
    assert keys_of(payload) == ["alpha.md", "gamma.md", "sub/beta.md"]
    assert entry(payload, "sub/beta.md")["undisposed"] == 1
    assert payload["workspace"]["counts"] == {
        "documents": 3,
        "active": 1,
        "open_rounds": 1,
        "undisposed": 1,
    }


def keys_of(payload):
    return [document["key"] for document in payload["workspace"]["documents"]]


def test_a_file_view_says_there_is_no_workspace(store, doc):
    """The single-document view is unchanged, and says so in one field."""
    served = WebView(store=store, path=doc, author="alice")
    served.start()
    try:
        assert state(served)["workspace"] is None
    finally:
        served.shutdown()


def test_naming_a_document_swaps_the_panel_and_nothing_else(view, reviewed):
    """The whole of "clicking a file": the same per-document projection answers."""
    status, payload = call(view, "/api/state", params={"doc": "sub/beta.md"})
    assert status == 200
    assert payload["workspace"]["selected"] == "sub/beta.md"
    assert payload["doc"] == "beta.md"  # the store's key for it, not the bar's
    assert payload["round"]["id"] == reviewed.open_rounds()[0].id
    assert payload["commentable"]
    assert [comment["body"] for comment in payload["comments"]] == ["which one?"]
    assert "The second document" in payload["base"]
    # The bar came along, unchanged, so one answer repaints both halves.
    assert keys_of(payload) == ["alpha.md", "gamma.md", "sub/beta.md"]


def test_clicking_a_document_nobody_has_reviewed_reads_it(view, reviewed):
    """The bar lists every document, so every row in it has to open onto one.

    A tree is mostly documents no round has been opened on — that is what a first
    browse *is* — and those rows carried no badge and, until this, no text
    either. The row and the panel now say the same thing: nothing has happened
    here yet, and here is the document.
    """
    status, payload = call(view, "/api/state", params={"doc": "gamma.md"})
    assert status == 200
    assert payload["workspace"]["selected"] == "gamma.md"
    assert payload["round"] is None
    assert payload["commentable"] is False
    assert payload["reading"] == "revision"
    assert "The third document" in payload["live"]
    assert "The third document" in payload["render"]
    # The unreviewed row is the one with no badges, and it is still navigable.
    quiet = [entry for entry in payload["workspace"]["documents"] if entry["key"] == "gamma.md"]
    assert quiet[0]["rounds"] == 0 and not quiet[0]["error"]


def test_the_round_hint_does_not_travel_to_another_document(tree, space, reviewed):
    """``--round`` names a round, and a round belongs to one document.

    Carried onto a sibling it would turn every other document in the tree into
    "no round X on Y" — a whole workspace broken by a flag about one file.
    """
    opening = space.list().documents[0]
    served = WebView(
        store=space.store_for(opening.path),
        path=opening.path,
        author="alice",
        workspace=space,
        doc=opening.key,
        round_hint=reviewed.open_rounds()[0].id,
    )
    served.start()
    try:
        status, payload = call(served, "/api/state", params={"doc": "sub/beta.md"})
        assert status == 200
        assert payload["round"]["id"] == reviewed.open_rounds()[0].id
        assert payload["blocked"] is None
    finally:
        served.shutdown()


def test_a_comment_lands_in_the_document_it_was_made_on(view, reviewed, tree, clock):
    """The claim the whole layer rests on, checked at the ledger."""
    status, payload = call(
        view,
        "/api/comment",
        {"doc": "sub/beta.md", "body": "and this one?", "whole": True},
    )
    assert status == 200
    assert payload["comment"]["body"] == "and this one?"
    landed = ReviewStore.for_document(tree / "sub" / "beta.md", clock=clock).fold()
    assert sorted(c.body for c in landed.comments.values()) == ["and this one?", "which one?"]
    # Nothing was written under the document the view started on.
    alpha = ReviewStore.for_document(tree / "alpha.md", clock=clock)
    assert not alpha.ledger.exists()


def test_the_badges_move_with_the_write_that_changed_them(view, reviewed):
    """The reason the bar rides in the state payload rather than a route of its own."""
    before = entry(state(view), "sub/beta.md")["undisposed"]
    call(view, "/api/comment", {"doc": "sub/beta.md", "body": "one more", "whole": True})
    assert entry(state(view), "sub/beta.md")["undisposed"] == before + 1


def test_a_write_without_a_named_document_goes_to_the_open_one(view, tree, clock):
    """Naming a document is how a request chooses; not naming one is not a trap."""
    path = tree / "alpha.md"
    store = ReviewStore.for_document(path, clock=clock)
    store.open_round(path, author="alice", title="alpha pass")
    status, _ = call(view, "/api/comment", {"body": "on the one that was open", "whole": True})
    assert status == 200
    assert [c.body for c in store.fold().comments.values()] == ["on the one that was open"]


@pytest.mark.parametrize(
    "key, because",
    [
        ("../../etc/passwd.md", "steps outside"),
        (".hidden/secret.md", "hidden entry"),
        ("nowhere.md", "no document"),
    ],
)
def test_a_bad_document_name_is_a_usage_refusal_not_a_guess(view, key, because):
    """Serving something else under the name asked for is the quiet wrong answer."""
    status, payload = call(view, "/api/state", params={"doc": key})
    assert status == 400
    assert payload["error"]["kind"] == "usage"
    assert because in payload["error"]["message"]


def test_a_file_view_refuses_a_document_that_is_not_its_own(store, doc):
    """Ignoring the name would let a caller read one review believing it was another."""
    served = WebView(store=store, path=doc, author="alice")
    served.start()
    try:
        status, payload = call(served, "/api/state", params={"doc": "other.md"})
        assert status == 400
        assert "serves 'spec.md'" in payload["error"]["message"]
    finally:
        served.shutdown()


def test_the_token_still_guards_every_document(view):
    """The workspace adds navigation, not a way around the door."""
    status, _ = call(view, "/api/state", params={"doc": "sub/beta.md"}, token="not-the-token")
    assert status == 403
    status, _ = call(
        view, "/api/comment", {"doc": "sub/beta.md", "body": "x", "whole": True}, token="wrong"
    )
    assert status == 403


def test_another_origin_cannot_post_to_a_document_either(view):
    status, _ = call(
        view,
        "/api/comment",
        {"doc": "sub/beta.md", "body": "x", "whole": True},
        origin="http://evil.example",
    )
    assert status == 403


def test_a_document_named_by_something_other_than_a_string_is_refused(view):
    status, payload = call(view, "/api/comment", {"doc": 7, "body": "x", "whole": True})
    assert status == 400
    assert "must be a string" in payload["error"]["message"]


def test_a_write_can_name_its_document_in_the_query_too(view, reviewed, tree, clock):
    """Which is how the page does it — one rule for every request it sends.

    The body form is for a caller writing JSON by hand. Both have to work, or
    the page and the API would be two surfaces with two conventions.
    """
    status, _ = call(
        view, "/api/comment", {"body": "from the query", "whole": True},
        params={"doc": "sub/beta.md"},
    )
    assert status == 200
    landed = ReviewStore.for_document(tree / "sub" / "beta.md", clock=clock).fold()
    assert "from the query" in [comment.body for comment in landed.comments.values()]


# -- the bar's own logic -------------------------------------------------
#
# The filter lives in the page, so this is where it can be held to account: the
# server always sends the whole listing, and hiding rows is a decision the
# browser makes. Lifted and run like the rest of the page's pure part.

LISTED = [
    {"key": "quiet.md", "active": False, "rounds": 0, "open_rounds": 0,
     "undisposed": 0, "orphans": 0, "error": None},
    {"key": "busy.md", "active": True, "rounds": 2, "open_rounds": 1,
     "undisposed": 3, "orphans": 1, "error": None},
    {"key": "done.md", "active": True, "rounds": 1, "open_rounds": 0,
     "undisposed": 0, "orphans": 0, "error": None},
    {"key": "broken.md", "active": False, "rounds": 0, "open_rounds": 0,
     "undisposed": 0, "orphans": 0, "error": "ledger.jsonl:1: not valid JSON"},
]


def test_the_filter_keeps_only_the_documents_with_review_activity(tmp_path):
    kept = in_node("visibleDocuments(input, true).map((d) => d.key)", LISTED, tmp_path)
    assert kept == ["busy.md", "done.md"]


def test_the_filter_off_is_the_whole_listing(tmp_path):
    kept = in_node("visibleDocuments(input, false).map((d) => d.key)", LISTED, tmp_path)
    assert kept == ["quiet.md", "busy.md", "done.md", "broken.md"]


def test_the_badges_say_what_is_outstanding(tmp_path):
    marks = in_node("badges(input[1]).map((b) => b.text)", LISTED, tmp_path)
    assert marks == ["1 open", "3 undisposed", "1 orphaned"]


def test_a_document_that_was_reviewed_and_owes_nothing_still_says_so(tmp_path):
    """A blank row would give "reviewed, nothing owed" and "never looked at" the same answer."""
    assert in_node("badges(input[2]).map((b) => b.text)", LISTED, tmp_path) == ["reviewed"]
    assert in_node("badges(input[0])", LISTED, tmp_path) == []


def test_a_document_whose_history_will_not_fold_says_that_first(tmp_path):
    marks = in_node("badges(input[3])", LISTED, tmp_path)
    assert marks == [{"text": "unreadable", "kind": "bad"}]
