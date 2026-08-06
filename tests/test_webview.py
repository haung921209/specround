"""The web view, over HTTP.

Everything here goes through a real socket. The routes are the contract a browser
meets — token, status codes, JSON envelope — and calling the methods behind them
would test the part that was never in doubt.

The claim being checked over and over is the one from SPEC §3: a comment made in
any of the three modes lands on the same document anchor. So most assertions end
at the ledger, read back through :meth:`~specround.store.ReviewStore.fold`, which
is also the gate — a comment whose anchor did not agree with its round's base
would make the fold itself raise (I7).
"""

import json
import re
import shutil
import subprocess
import urllib.error
import urllib.request

import pytest

from specround import markdown
from specround.webview import _GETS, _POSTS, VIEW_SCHEMA, WebView, page

REVISED_QUOTE = "Timeouts are 45 seconds."


@pytest.fixture
def view(store, doc):
    """A running view over the fixture document, on a port the OS picked."""
    served = WebView(store=store, path=doc, author="alice")
    served.start()
    try:
        yield served
    finally:
        served.shutdown()


@pytest.fixture
def opened(view, round_id):
    """The same view, with a round open on the document."""
    return view


def call(view, path, body=None, *, token=None, origin=None, method=None):
    """One request. Returns ``(status, payload)`` — an error is a payload too."""
    data = json.dumps(body).encode("utf-8") if body is not None else None
    headers = {"Content-Type": "application/json"}
    if origin is not None:
        headers["Origin"] = origin
    chosen = view.token if token is None else token
    url = f"http://{view.host}:{view.port}{path}?t={chosen}"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            raw = response.read().decode("utf-8")
            status = response.status
    except urllib.error.HTTPError as error:
        raw = error.read().decode("utf-8")
        status = error.code
    try:
        return status, json.loads(raw)
    except json.JSONDecodeError:
        return status, raw


def state(view):
    status, payload = call(view, "/api/state")
    assert status == 200
    return payload


# -- the door ------------------------------------------------------------


def test_the_page_is_one_self_contained_file(view):
    status, body = call(view, "/")
    assert status == 200
    assert "<title>specround</title>" in body
    # No build step and no CDN: a view that needed the network would stop being
    # a local tool the moment it was offline.
    assert "http://" not in body.replace("http://127.0.0.1", "")
    assert "<script src=" not in body and "<link rel=\"stylesheet\"" not in body


def test_a_request_without_the_token_is_refused(view):
    status, _ = call(view, "/api/state", token="")
    assert status == 403


def test_a_request_with_the_wrong_token_is_refused(view):
    status, _ = call(view, "/api/state", token="not-the-token")
    assert status == 403


def test_a_request_from_another_origin_is_refused(view):
    """A page in another tab must not be able to write to this ledger.

    The token is unguessable, so this is the second half of the same answer
    rather than the only one — but a local server that appends to a review is
    reachable from every tab, and one check that costs nothing is worth having.
    """
    status, _ = call(view, "/api/state", origin="https://evil.example")
    assert status == 403


def test_our_own_origin_is_accepted(view):
    status, _ = call(view, "/api/state", origin=f"http://{view.host}:{view.port}")
    assert status == 200


def test_an_unknown_route_is_a_404(view):
    status, _ = call(view, "/api/nope")
    assert status == 404


def test_the_url_carries_the_token(view):
    assert view.url == f"http://{view.host}:{view.port}/?t={view.token}"


# -- reading -------------------------------------------------------------


def test_state_reports_the_round_the_three_modes_and_the_counts(opened, doc_text):
    payload = state(opened)
    assert payload["schema"] == VIEW_SCHEMA
    assert payload["round"]["status"] == "open"
    assert payload["commentable"] is True
    assert payload["blocked"] is None
    assert payload["base"] == doc_text
    assert payload["live"] == doc_text
    assert payload["render"].startswith("<h1>")
    assert payload["diff"]["identical"] is True
    assert payload["counts"] == {
        "comments": 0, "unresolved": 0, "orphans": 0, "resolved": 0, "events": 1
    }


def test_a_document_with_no_round_is_readable_and_says_what_to_do(view):
    """Read-only is an outcome, not a refusal — but it names the command."""
    payload = state(view)
    assert payload["round"] is None
    assert payload["commentable"] is False
    assert "specround round open" in payload["blocked"]


def test_two_open_rounds_ask_which_one_rather_than_picking(view, store, doc):
    store.open_round(doc, author="alice")
    store.open_round(doc, author="alice")
    payload = state(view)
    assert payload["commentable"] is False
    assert "--round" in payload["blocked"]


def test_a_closed_round_still_shows_its_review(opened, store, round_id):
    store.close_round(round_id, author="alice")
    payload = state(opened)
    assert payload["round"]["id"] == round_id
    assert payload["commentable"] is False
    assert "reading only" in payload["blocked"]


def test_the_diff_reports_the_revision_against_the_frozen_base(opened, doc, doc_text):
    doc.write_text(doc_text.replace("30 seconds", "45 seconds"), encoding="utf-8")
    payload = state(opened)
    assert payload["diff"]["identical"] is False
    assert payload["diff"]["added"] == 1 and payload["diff"]["removed"] == 1
    added = [row for row in payload["diff"]["rows"] if row["op"] == "added"]
    assert added[0]["base_start"] is None and added[0]["live_start"] is not None


def test_a_deleted_document_leaves_the_history_readable(opened, doc):
    doc.unlink()
    payload = state(opened)
    assert payload["live"] is None
    assert payload["base"] is not None
    assert payload["diff"]["available"] is False


# -- commenting, from every mode ----------------------------------------


def anchored(store, doc_text, quote):
    """The one comment in the ledger, and the text its anchor names."""
    comments = list(store.fold().comments.values())
    assert len(comments) == 1
    comment = comments[0]
    assert comment.anchor is not None
    assert doc_text[comment.anchor.start : comment.anchor.end] == quote
    return comment


def test_a_span_selected_on_the_base_becomes_the_anchor(opened, store, doc_text):
    at = doc_text.index("30 seconds")
    status, payload = call(
        opened,
        "/api/comment",
        {"space": "base", "start": at, "end": at + 10, "body": "too short for the proxy"},
    )
    assert status == 200
    assert payload["comment"]["anchor"]["exact"] == "30 seconds"
    comment = anchored(store, doc_text, "30 seconds")
    assert comment.body == "too short for the proxy"
    assert comment.ext is None


def test_a_run_from_the_render_anchors_the_text_it_shows(opened, store, doc_text):
    """G6, end to end: the render's offsets are the document's offsets.

    The page reads these off the DOM; the test reads them out of the HTML. Either
    way the comment lands on the same span a raw-mode selection would produce,
    which is what makes the two modes one review.
    """
    runs = [run for run in markdown.runs_of(state(opened)["render"]) if "30 seconds" in run.text]
    assert runs, "the fixture paragraph should be one run"
    run = runs[0]
    at = run.start + run.text.index("30 seconds")
    status, _ = call(
        opened, "/api/comment", {"space": "base", "start": at, "end": at + 10, "body": "from the render"}
    )
    assert status == 200
    anchored(store, doc_text, "30 seconds")


def test_a_comment_on_the_whole_document_has_no_anchor(opened, store):
    status, payload = call(
        opened, "/api/comment", {"whole": True, "body": "the retry section is missing"}
    )
    assert status == 200
    assert payload["comment"]["anchor"] is None
    assert payload["comment"]["current_anchor"] is None


def test_an_empty_body_is_refused_before_anything_is_written(opened, store):
    status, payload = call(opened, "/api/comment", {"whole": True, "body": "   "})
    assert status == 400
    assert payload["error"]["kind"] == "usage"
    assert not store.fold().comments


def test_a_span_past_the_end_of_the_base_is_a_usage_error(opened):
    status, payload = call(
        opened, "/api/comment", {"space": "base", "start": 0, "end": 10_000, "body": "x"}
    )
    assert status == 400
    assert payload["error"]["kind"] == "usage"


def test_an_unknown_space_is_refused(opened):
    status, payload = call(
        opened, "/api/comment", {"space": "sideways", "start": 0, "end": 1, "body": "x"}
    )
    assert status == 400
    assert "sideways" in payload["error"]["message"]


def test_a_selection_on_an_unrevised_revision_needs_no_carrying(opened, store, doc_text):
    """The diff's unchanged lines are the same text in both spaces."""
    at = doc_text.index("30 seconds")
    status, payload = call(
        opened,
        "/api/comment",
        {"space": "revision", "start": at, "end": at + 10, "body": "same text either way"},
    )
    assert status == 200
    assert payload["carried"] == {"strategy": "position", "ambiguous": False}
    comment = anchored(store, doc_text, "30 seconds")
    # Nothing to report, so nothing is recorded: rung 1 means the text did not
    # move, and provenance for a non-event is noise.
    assert comment.ext is None


def test_a_reworded_line_is_carried_back_into_the_base_and_says_how(opened, store, doc, doc_text):
    """The diff-line-to-anchor conversion is the re-anchor ladder, run backwards."""
    revised = doc_text.replace("Timeouts are 30 seconds.", REVISED_QUOTE)
    doc.write_text(revised, encoding="utf-8")
    at = revised.index(REVISED_QUOTE)
    status, payload = call(
        opened,
        "/api/comment",
        {"space": "revision", "start": at, "end": at + len(REVISED_QUOTE), "body": "still too short"},
    )
    assert status == 200
    assert payload["carried"]["strategy"] in ("quote", "normalized", "fuzzy")
    comment = list(store.fold().comments.values())[0]
    # The anchor holds in the *base*, which is what the round is a review of —
    # and the fold would have raised on the way here if it did not (I7).
    assert store.base_text(comment.round)[comment.anchor.start : comment.anchor.end]
    assert comment.ext["view"]["space"] == "revision"
    assert comment.ext["view"]["strategy"] == payload["carried"]["strategy"]


def test_text_only_the_revision_has_is_refused_with_the_two_ways_out(opened, store, doc, doc_text):
    """An added line has no home in the base, and nothing is guessed for it."""
    doc.write_text(doc_text + "\n## Retry policy\n\nRetries are three, with jitter.\n", encoding="utf-8")
    live = doc.read_text(encoding="utf-8")
    at = live.index("Retries are three, with jitter.")
    status, payload = call(
        opened,
        "/api/comment",
        {"space": "revision", "start": at, "end": at + 31, "body": "jitter needs a bound"},
    )
    assert status == 409
    assert payload["error"]["kind"] == "state"
    assert "whole document" in payload["error"]["message"]
    assert "new round" in payload["error"]["message"]
    assert not store.fold().comments


# -- suggestions (G8) ----------------------------------------------------


def test_an_edit_of_the_raw_text_becomes_a_suggestion_diff(opened, store, doc_text):
    proposed = doc_text.replace("30 seconds", "60 seconds")
    status, payload = call(
        opened, "/api/suggestion", {"text": proposed, "body": "the proxy caps at 60"}
    )
    assert status == 200
    suggestion = payload["comment"]
    assert suggestion["kind"] == "suggestion"
    assert "-Timeouts are 30 seconds" in suggestion["patch"]
    assert "+Timeouts are 60 seconds" in suggestion["patch"]
    assert suggestion["body"] == "the proxy caps at 60"
    # Anchored on the line it rewrites, in the base.
    assert suggestion["anchor"]["exact"] == "Timeouts are 30 seconds. Retries are not specified yet."
    assert store.fold().comments[suggestion["id"]].kind == "suggestion"


def test_a_suggestion_that_only_adds_lines_anchors_at_the_insertion_point(opened, doc_text):
    at = doc_text.index("Timeouts")
    proposed = doc_text[:at] + "Retries are three.\n\n" + doc_text[at:]
    status, payload = call(opened, "/api/suggestion", {"text": proposed})
    assert status == 200
    anchor = payload["comment"]["anchor"]
    assert anchor["exact"] == "" and anchor["start"] == anchor["end"] == at


def test_an_unchanged_edit_proposes_nothing(opened, store, doc_text):
    status, payload = call(opened, "/api/suggestion", {"text": doc_text})
    assert status == 400
    assert "unchanged" in payload["error"]["message"]
    assert not store.fold().comments


def test_a_suggestion_needs_the_text(opened):
    status, payload = call(opened, "/api/suggestion", {"body": "no text"})
    assert status == 400
    assert payload["error"]["kind"] == "usage"


# -- threads (G11) -------------------------------------------------------


@pytest.fixture
def comment_id(opened, doc_text):
    at = doc_text.index("30 seconds")
    _, payload = call(
        opened, "/api/comment", {"space": "base", "start": at, "end": at + 10, "body": "too short"}
    )
    return payload["comment"]["id"]


def test_a_reply_joins_the_thread(opened, comment_id):
    status, payload = call(
        opened, "/api/reply", {"target": comment_id, "body": "the proxy caps at 45s"}
    )
    assert status == 200
    assert [r["body"] for r in payload["comment"]["replies"]] == ["the proxy caps at 45s"]


def test_a_reply_carries_the_viewer_name_it_was_sent_with(opened, comment_id):
    _, payload = call(
        opened, "/api/reply", {"target": comment_id, "body": "from the agent", "author": "agent:reviewer"}
    )
    assert payload["comment"]["replies"][0]["author"] == "agent:reviewer"


def test_a_reply_to_an_unknown_comment_is_the_ledgers_refusal(opened):
    status, payload = call(opened, "/api/reply", {"target": "c-000000000000", "body": "x"})
    assert status == 409
    assert payload["error"]["kind"] == "state"
    assert "unknown comment" in payload["error"]["message"]


def test_resolving_hides_nothing_from_the_ledger(opened, comment_id):
    status, payload = call(
        opened, "/api/thread", {"target": comment_id, "resolved": True, "note": "settled"}
    )
    assert status == 200
    assert payload["comment"]["resolved"] is True
    assert payload["changed"] is True
    listed = state(opened)
    # Hiding is the page's job; the payload carries every thread and the count.
    assert [c["id"] for c in listed["comments"]] == [comment_id]
    assert listed["counts"]["resolved"] == 1


def test_resolving_twice_records_nothing_and_is_not_an_error(opened, comment_id):
    """I10 reaching the browser: a button clicked twice is a retry."""
    call(opened, "/api/thread", {"target": comment_id, "resolved": True})
    status, payload = call(opened, "/api/thread", {"target": comment_id, "resolved": True})
    assert status == 200
    assert payload["changed"] is False
    assert payload["event"] is None


def test_a_reply_to_a_resolved_thread_is_refused_by_the_ledger(opened, comment_id):
    """I11, unchanged and unparaphrased — the message names the way out."""
    call(opened, "/api/thread", {"target": comment_id, "resolved": True})
    status, payload = call(opened, "/api/reply", {"target": comment_id, "body": "one more thing"})
    assert status == 409
    assert "reopen" in payload["error"]["message"]


def test_reopening_needs_a_reason(opened, comment_id):
    call(opened, "/api/thread", {"target": comment_id, "resolved": True})
    status, payload = call(opened, "/api/thread", {"target": comment_id, "resolved": False})
    assert status == 400
    assert "reason" in payload["error"]["message"]


def test_reopening_puts_the_conversation_back(opened, comment_id):
    call(opened, "/api/thread", {"target": comment_id, "resolved": True})
    status, payload = call(
        opened, "/api/thread", {"target": comment_id, "resolved": False, "reason": "it came back"}
    )
    assert status == 200
    assert payload["comment"]["resolved"] is False
    status, _ = call(opened, "/api/reply", {"target": comment_id, "body": "now it lands"})
    assert status == 200


def test_an_unknown_actor_is_refused(opened, comment_id):
    status, payload = call(
        opened, "/api/thread", {"target": comment_id, "resolved": True, "actor": "robot"}
    )
    assert status == 400
    assert "robot" in payload["error"]["message"]


def test_the_actor_is_recorded_as_sent(opened, comment_id, store):
    call(opened, "/api/thread", {"target": comment_id, "resolved": True, "actor": "agent"})
    assert store.fold().comments[comment_id].resolution.actor == "agent"


# -- dispositions (G3) ---------------------------------------------------


def test_a_disposition_settles_the_comment(opened, comment_id):
    status, payload = call(
        opened,
        "/api/dispose",
        {"target": comment_id, "verdict": "applied", "reason": "raised to 60"},
    )
    assert status == 200
    assert payload["comment"]["state"] == "applied"
    assert payload["comment"]["unresolved"] is False


def test_deferring_leaves_the_comment_outstanding(opened, comment_id):
    _, payload = call(
        opened,
        "/api/dispose",
        {"target": comment_id, "verdict": "deferred", "reason": "waiting on the retry spec"},
    )
    assert payload["comment"]["unresolved"] is True


def test_a_settled_comment_cannot_be_disposed_again(opened, comment_id):
    call(opened, "/api/dispose", {"target": comment_id, "verdict": "applied", "reason": "done"})
    status, payload = call(
        opened, "/api/dispose", {"target": comment_id, "verdict": "rejected", "reason": "no"}
    )
    assert status == 409
    assert "already settled" in payload["error"]["message"]


def test_an_unknown_verdict_is_refused(opened, comment_id):
    status, payload = call(
        opened, "/api/dispose", {"target": comment_id, "verdict": "wontfix", "reason": "no"}
    )
    assert status == 400
    assert "wontfix" in payload["error"]["message"]


def test_a_disposition_needs_a_reason(opened, comment_id):
    status, payload = call(opened, "/api/dispose", {"target": comment_id, "verdict": "applied"})
    assert status == 400
    assert "reason" in payload["error"]["message"]


# -- writing with no open round -----------------------------------------


def test_commenting_with_no_open_round_is_the_state_error_the_cli_gives(view):
    status, payload = call(view, "/api/comment", {"whole": True, "body": "x"})
    assert status == 409
    assert payload["error"]["kind"] == "state"
    assert "specround round open" in payload["error"]["message"]


def test_disposing_still_works_after_the_round_closes(opened, store, comment_id, round_id):
    """A comment outlives its round, and so does everything decided about it."""
    store.close_round(round_id, author="alice", allow_unresolved=True)
    status, _ = call(
        opened, "/api/dispose", {"target": comment_id, "verdict": "answered", "reason": "explained"}
    )
    assert status == 200
    status, _ = call(opened, "/api/comment", {"whole": True, "body": "too late"})
    assert status == 409


# -- request shapes ------------------------------------------------------


def test_a_body_that_is_not_json_is_a_usage_error(view):
    url = f"http://{view.host}:{view.port}/api/comment?t={view.token}"
    request = urllib.request.Request(
        url, data=b"not json", headers={"Content-Type": "application/json"}
    )
    try:
        urllib.request.urlopen(request, timeout=10)
        raise AssertionError("expected a refusal")
    except urllib.error.HTTPError as error:
        assert error.code == 400
        assert json.loads(error.read())["error"]["kind"] == "usage"


def test_a_json_array_is_not_a_request(view):
    status, payload = call(view, "/api/comment", ["not", "an", "object"])
    assert status == 400
    assert "JSON object" in payload["error"]["message"]


# -- lifecycle -----------------------------------------------------------


def test_a_bound_view_that_never_served_still_lets_go(store, doc):
    """Closing must not wait for a serving loop that was never started.

    The socket server's shutdown waits for the loop to acknowledge, and only the
    loop ever does — so this used to hang forever. It is a real path: ``--open``
    raising, or an interrupt between binding and serving, leaves a view holding a
    port with nothing running.
    """
    bound = WebView(store=store, path=doc, author="alice").bind()
    port = bound.port
    bound.shutdown()
    # The port is free again, which is the only observable half of "let go".
    again = WebView(store=store, path=doc, author="alice", port=port).bind()
    assert again.port == port
    again.shutdown()


def test_shutting_down_twice_is_harmless(view):
    view.shutdown()
    view.shutdown()


# -- the page and the routes stay one thing ------------------------------


def test_the_page_calls_exactly_the_routes_the_server_serves():
    """Neither half may drift: a dead button and a dead route look the same.

    A page calling a route that is gone is a control that silently does nothing;
    a route no page calls is either dead code or an undocumented surface. Both
    show up here as an inequality.
    """
    html = page().decode("utf-8")
    called = set(re.findall(r'"(/api/[a-z]+)"', html))
    served = set(_GETS) | set(_POSTS)
    assert called == served - {"/"}


def test_the_pages_script_parses(tmp_path):
    """Nothing else here would notice a syntax error in the one script.

    Best effort by nature — it needs a JavaScript engine, which this package
    does not depend on and will not grow a dependency for. When one is around,
    a broken page fails a test instead of failing in a browser.
    """
    engine = shutil.which("node") or shutil.which("bun")
    if engine is None:
        pytest.skip("no javascript engine on this machine")
    html = page().decode("utf-8")
    script = re.search(r"<script>(.*)</script>", html, re.DOTALL)
    assert script is not None, "the page should carry exactly one inline script"
    source = tmp_path / "app.js"
    source.write_text(script.group(1), encoding="utf-8")
    finished = subprocess.run(
        [engine, "--check", str(source)], capture_output=True, text=True, timeout=60
    )
    assert finished.returncode == 0, finished.stderr


def test_every_state_field_the_page_reads_is_a_field_the_server_sends(opened):
    """The other half of the drift the route test catches.

    A page reading ``data.rows`` when the server sends ``data.diff.rows`` fails
    silently — undefined renders as nothing, and nothing looks like an empty
    review. Comparing the names is cheap and catches the whole class.
    """
    html = page().decode("utf-8")
    read = set(re.findall(r"\bdata\.([a-z_]+)\b", html))
    assert read, "the page should read the state payload"
    assert read <= set(state(opened)), sorted(read - set(state(opened)))
