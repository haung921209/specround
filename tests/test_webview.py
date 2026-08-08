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
import socket
import subprocess
import urllib.error
import urllib.parse
import urllib.request

import pytest

from specround import markdown
from specround.anchors import anchor_for
from specround.webview import (
    DERIVED,
    EPHEMERAL,
    FALLBACK,
    PINNED,
    PORT_CEILING,
    PORT_FLOOR,
    VIEW_SCHEMA,
    PortTaken,
    WebView,
    _GETS,
    _POSTS,
    derived_port,
    page,
)
from specround.workspace import Workspace

REVISED_QUOTE = "Timeouts are 45 seconds."


@pytest.fixture
def view(store, doc):
    """A running view over the fixture document, on a port the OS picked.

    ``port=0`` on purpose: the default is the *derived* port, and eight hundred
    tests holding one predictable port each would be a suite that fights itself
    (and the machine) for addresses. The derived path is exercised where it is
    the thing under test, a few tests down.
    """
    served = WebView(store=store, path=doc, author="alice", port=0)
    served.start()
    try:
        yield served
    finally:
        served.shutdown()


@pytest.fixture
def opened(view, round_id):
    """The same view, with a round open on the document."""
    return view


def call(view, path, body=None, *, token=None, origin=None, method=None, params=None):
    """One request. Returns ``(status, payload)`` — an error is a payload too.

    ``params`` goes into the query beside the token, which is the only way to
    send one: the token is a query parameter too, so a caller that spelled its
    own ``?doc=…`` into ``path`` would push the token out of the query and get a
    403 for what it meant as a 400.
    """
    data = json.dumps(body).encode("utf-8") if body is not None else None
    headers = {"Content-Type": "application/json"}
    if origin is not None:
        headers["Origin"] = origin
    chosen = view.token if token is None else token
    query = urllib.parse.urlencode({"t": chosen, **(params or {})})
    url = f"http://{view.host}:{view.port}{path}?{query}"
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


#: The script fences off the functions that touch neither the DOM nor the page's
#: state, so that this file can lift them out and run them.
_LIFTED = re.compile(r"// -- the pure part.*?\n(.*?)\n// -- end of the pure part", re.DOTALL)


def in_node(expression, given, tmp_path):
    """Evaluate ``expression`` against the page's own pure functions.

    The page is one file with no build step and no test runner, so the only part
    of it a machine here can hold to account is the part that needs no browser to
    mean anything. Lifting that part and running it is the same best-effort
    footing as the syntax check further down, for the same reason: the engine is
    not a dependency of this package and will not become one.

    ``given`` arrives in the lifted script as ``input``.
    """
    engine = shutil.which("node") or shutil.which("bun")
    if engine is None:
        pytest.skip("no javascript engine on this machine")
    block = _LIFTED.search(page().decode("utf-8"))
    assert block is not None, "the page should still fence off its pure part"
    source = tmp_path / "lifted.js"
    source.write_text(
        f"{block.group(1)}\nconst input = {json.dumps(given)};\n"
        f"console.log(JSON.stringify({expression}));\n",
        encoding="utf-8",
    )
    finished = subprocess.run([engine, str(source)], capture_output=True, text=True, timeout=60)
    assert finished.returncode == 0, finished.stderr
    return json.loads(finished.stdout)


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
        "comments": 0, "undisposed": 0, "orphans": 0, "misplaced": 0,
        "resolved": 0, "events": 1,
    }


def test_a_document_with_no_round_is_readable_and_says_what_to_do(view):
    """Read-only is an outcome, not a refusal — but it names the command."""
    payload = state(view)
    assert payload["round"] is None
    assert payload["commentable"] is False
    assert "read only" in payload["blocked"]
    assert "specround round open" in payload["blocked"]


def test_a_document_with_no_round_reads_the_live_file(view, doc_text):
    """"Read only" has to carry a text, or it is a blank page.

    The routes already served a document with no round and blocked the two verbs
    the format ties to an open round (I4). What they did not say was *which* text
    the render and raw modes show — and both took it from the round's base, so a
    document nobody had opened a round on had nothing to draw. That is not
    read-only, it is unreadable, and the reviewer was only reading.
    """
    payload = state(view)
    assert payload["reading"] == "revision"
    assert payload["live"] == doc_text
    # No round means no base, and the payload still says so rather than passing
    # the live text off as one (I7).
    assert payload["base"] is None
    assert payload["render"].startswith("<h1>")
    assert "Timeouts are 30 seconds." in payload["render"]


def test_with_no_round_the_reading_follows_the_file(view, doc, doc_text):
    """Nothing froze this text, so what is shown is the file as it is now."""
    doc.write_text(doc_text.replace("30 seconds", "45 seconds"), encoding="utf-8")
    payload = state(view)
    assert payload["reading"] == "revision"
    assert REVISED_QUOTE in payload["live"]
    assert "45 seconds" in payload["render"]


def test_with_no_round_there_is_no_diff_to_show(view):
    """The diff compares two texts, and one of them does not exist yet."""
    payload = state(view)
    assert payload["diff"]["available"] is False
    assert payload["diff"]["rows"] == []


def test_opening_a_round_moves_the_reading_onto_the_base(view, store, doc, doc_text):
    """I7 is untouched: with a round, what is shown is the base it froze.

    The read-only path adds a text where there was none. It does not make the
    view follow the file once a round exists — that would move the ground under
    the comments this round is a review of.
    """
    assert state(view)["reading"] == "revision"
    store.open_round(doc, author="alice")
    doc.write_text(doc_text.replace("30 seconds", "45 seconds"), encoding="utf-8")
    payload = state(view)
    assert payload["reading"] == "base"
    assert payload["base"] == doc_text
    assert "30 seconds" in payload["render"]
    assert "45 seconds" not in payload["render"]
    # And the revision is where the change shows, exactly as it did before.
    assert payload["diff"]["available"] is True
    assert payload["diff"]["identical"] is False


def test_no_round_and_no_file_leaves_nothing_to_read(view, doc):
    """The one case that has no text at all — and it still says why."""
    doc.unlink()
    payload = state(view)
    assert payload["reading"] is None
    assert payload["render"] == ""
    assert payload["live"] is None
    assert "specround round open" in payload["blocked"]


def test_reading_a_document_is_not_permission_to_comment_on_it(view):
    """Showing the text does not open a round (I4). The refusal is unchanged."""
    status, payload = call(view, "/api/comment", {"body": "hello", "whole": True})
    assert status == 409
    assert "specround round open" in payload["error"]["message"]


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


# -- the line gutter (G6) ------------------------------------------------
#
# Clicking a line number comments on that line. It is the gesture the first live
# round reached for before anything else, and it is cheaper than a drag for the
# comment people actually write most ("this line is wrong"). What makes it worth
# no new concepts is that it lands in the anchor space that was already there:
# the line's span is the run the gutter numbers, so the two gestures are one
# comment, and a line the revision alone has takes the exit that already exists.


def test_the_raw_gutter_numbers_the_lines_the_document_has(tmp_path, doc_text):
    """The page splits the raw text itself, so its lines must be the real ones.

    Raw mode is drawn from the base string rather than from anything the server
    laid out, which makes this the one offset the page computes instead of
    receiving. ``lines_of`` is the oracle it has to agree with.
    """
    lines = in_node("rawLines(input)", doc_text, tmp_path)
    pieces = markdown.lines_of(doc_text)
    assert [(piece.start, piece.text) for piece in pieces] == [
        (line["start"], line["text"]) for line in lines[: len(pieces)]
    ]
    # The document ends with a break, so raw mode shows the empty line after it.
    # That is not an off-by-one to trim — the gutter beside it is where a reviewer
    # clicks to say something belongs at the end of the document.
    assert lines[len(pieces) :] == [{"start": len(doc_text), "text": ""}]


def test_a_line_click_and_a_selection_of_that_line_are_the_same_anchor(tmp_path, doc_text):
    """The convergence claim from SPEC §3, at the level of the arithmetic."""
    spans = in_node(
        'rawLines(input).map((line) => spanOfRun("base", line.start, line.text, input))',
        doc_text,
        tmp_path,
    )
    for span, piece in zip(spans, markdown.lines_of(doc_text)):
        assert span == {
            "space": "base",
            "start": piece.start,
            "end": piece.start + len(piece.text),
            "quote": piece.text,
        }
        # Exactly the span a drag across that line yields, and the anchor machine
        # already takes it — no second converter, and nothing to keep in step.
        assert anchor_for(doc_text, span["start"], span["end"]).exact == piece.text
    # An empty line has nothing to quote, so it comes out zero-width: an
    # insertion point, which the ledger has a reading for (§5) and the painter
    # already draws as a caret. "Something belongs here" is the honest comment.
    empty = spans[-1]
    assert empty["start"] == empty["end"] == len(doc_text)
    assert anchor_for(doc_text, empty["start"], empty["end"]).exact == ""


def test_a_line_of_the_base_becomes_a_comment_on_that_line(opened, store, doc_text, tmp_path):
    piece = next(p for p in markdown.lines_of(doc_text) if "30 seconds" in p.text)
    span = in_node(
        'spanOfRun("base", input.start, input.text, input.base)',
        {"start": piece.start, "text": piece.text, "base": doc_text},
        tmp_path,
    )
    status, payload = call(
        opened,
        "/api/comment",
        {
            "space": span["space"],
            "start": span["start"],
            "end": span["end"],
            "body": "this whole line is vague",
        },
    )
    assert status == 200
    assert payload["comment"]["anchor"]["exact"] == piece.text
    anchored(store, doc_text, piece.text)


def test_the_gutter_of_a_revision_only_line_gets_the_refusal_that_already_exists(
    opened, store, doc, doc_text, tmp_path
):
    """One rejection, not two: the line takes the exit a selection takes.

    The span is not written by hand — it is what the page's arithmetic makes of
    the diff row the server sent, which is the only way this test would notice
    the two of them drifting apart.
    """
    doc.write_text(
        doc_text + "\n## Retry policy\n\nRetries are three, with jitter.\n", encoding="utf-8"
    )
    live = doc.read_text(encoding="utf-8")
    rows = state(opened)["diff"]["rows"]
    added = next(row for row in rows if row["op"] == "added" and row["text"].startswith("Retries"))
    span = in_node(
        'spanOfRun("revision", input.start, input.text, input.live)',
        {"start": added["live_start"], "text": added["text"], "live": live},
        tmp_path,
    )
    assert span == {
        "space": "revision",
        "start": added["live_start"],
        "end": added["live_start"] + len(added["text"]),
        "quote": added["text"],
    }
    status, payload = call(
        opened,
        "/api/comment",
        {
            "space": span["space"],
            "start": span["start"],
            "end": span["end"],
            "body": "jitter needs a bound",
        },
    )
    assert status == 409
    assert "whole document" in payload["error"]["message"]
    assert "new round" in payload["error"]["message"]
    assert not store.fold().comments


def test_the_gutter_looks_clickable_only_where_a_click_records_something(tmp_path):
    """The affordance and its gate are one class name, written by one function.

    Renaming either half leaves a control that works and does not look like one —
    the same failure the first live round hit, approached from the other side.
    """
    assert in_node('[modeClass("raw", true), modeClass("diff", false)]', None, tmp_path) == [
        "raw live",
        "diff",
    ]
    assert "#doc.live .line .ln" in page().decode("utf-8")



def test_the_diff_mode_says_why_it_is_not_on_offer(tmp_path):
    """A mode that would draw an empty page is a mode the page should not offer.

    The reason names the missing half, because the two ways a diff has nothing to
    compare are different situations to be in: no round yet is the beginning of a
    review, and an unreadable file is something to go and look at.
    """
    reasons = in_node(
        "input.map(diffReason)",
        [
            {"round": {"id": "r-1"}, "diff": {"available": True}},
            {"round": None, "diff": {"available": False}},
            {"round": {"id": "r-1"}, "diff": {"available": False}},
        ],
        tmp_path,
    )
    assert reasons[0] == ""
    assert "no round" in reasons[1]
    assert "not readable" in reasons[2]


def test_a_mode_the_document_cannot_show_is_not_the_mode_it_opens_in(tmp_path):
    """Browsing a tree carries the mode from document to document (H15).

    Clicking a file with no round while the diff mode is up used to leave the
    reviewer on a mode that document has nothing for. The mode falls back rather
    than the page going quiet — the same click, one document over, still reads.
    """
    assert in_node('modeFor("diff", {round: null, diff: {available: false}})', None, tmp_path) == (
        "render"
    )
    assert in_node('modeFor("diff", {round: {id: "r-1"}, diff: {available: true}})', None, tmp_path) == (
        "diff"
    )
    assert in_node('modeFor("raw", {round: null, diff: {available: false}})', None, tmp_path) == "raw"


def test_the_raw_mode_reads_whatever_the_state_says_it_is_reading():
    """One lookup, so the mode and the offsets its gutter posts cannot disagree.

    It used to take `data.base` directly, which is why a document with no round
    drew nothing: there was no base and the mode had no other text to ask for.
    Going through the same lookup every other converter uses is what keeps the
    line offsets counted in the space the server said it was serving.
    """
    html = page().decode("utf-8")
    assert "rawLines(textIn(space))" in html
    assert "rawLines(data.base" not in html


# -- focus: the highlight, and which side of the screen moves ------------


def test_the_focus_highlight_covers_both_kinds_of_anchor(tmp_path):
    """A caret is an anchor, so focusing one has to look like focusing a span.

    Both halves are asserted together because they drifted apart: `here` was put
    on carets by a selector that listed them and taken off by one that did not,
    which showed as nothing for as long as no style and no reader cared about the
    class. Now the scroll target is read off it, and a `here` that never goes out
    is a card pointing at somebody else's mark.
    """
    html = page().decode("utf-8")
    assert "mark.anch.here, .caret.here { outline" in html
    assert 'querySelectorAll("mark.anch.here, .caret.here")' in html


def test_focusing_moves_the_side_the_click_did_not_come_from(tmp_path):
    """The round trip, both ways: mark to thread, and thread back to the place.

    Highlighting has always been symmetric and scrolling was not, so a mark took
    the reviewer to its card and a card took them nowhere. The rule is one line —
    move the far side — and it is stated once here rather than at the two
    listeners, where the two directions could quietly stop being each other's
    inverse.
    """
    assert in_node('focusScroll("mark", {mark: true, card: true})', None, tmp_path) == "card"
    assert in_node('focusScroll("card", {mark: true, card: true})', None, tmp_path) == "anchor"


def test_a_focus_that_does_not_say_where_it_came_from_keeps_the_old_behaviour(tmp_path):
    """Two listeners call this today, and a third is one feature away.

    One answer for one click is what stops both panes moving at once, so the
    question is only ever which — and a caller that does not say gets what the
    page did before there was a choice, the thread card, rather than nothing.
    Silence is the hardest of the three outcomes to notice from outside, so the
    default is decided here instead of by whatever `undefined` happens to fall
    through to at the call site.
    """
    assert in_node("focusScroll(undefined, {mark: true, card: true})", None, tmp_path) == "card"


def test_a_side_that_is_not_on_screen_is_not_scrolled_to(tmp_path):
    """Absence is ordinary here, and ordinary means no-op rather than no-answer.

    A card is missing whenever the resolved filter hides it; an anchor is missing
    whenever the mode does not draw one — a diff showing changed lines only, or a
    render whose markup carries no mark for that offset. Neither is a fault to
    report, and neither is a reason to scroll somewhere arbitrary.
    """
    assert in_node('focusScroll("mark", {mark: true, card: false})', None, tmp_path) == ""
    assert in_node('focusScroll("card", {mark: false, card: true})', None, tmp_path) == ""


def test_a_comment_the_document_lost_is_told_about_rather_than_left_silent(tmp_path):
    """Two ways to have no mark, and they are not the same event.

    A mode that is not drawing this anchor is the page's own business and says
    nothing. Orphaning is the ledger's — a revision hid the text — and a click
    that gets the same silence for both teaches a reviewer that cards sometimes
    just do nothing, which is how the missing half of this round trip read for
    as long as it was missing.
    """
    orphan = 'focusScroll("card", {mark: false, card: true, orphaned: true})'
    quiet = 'focusScroll("card", {mark: false, card: true, orphaned: false})'
    assert in_node(orphan, None, tmp_path) == "orphan"
    assert in_node(quiet, None, tmp_path) == ""


def test_an_orphan_the_page_can_still_place_is_scrolled_to_like_any_other(tmp_path):
    """Orphaning keeps the last good anchor (§4), so a mark for one is real.

    Announcing the state instead of going to that mark would have the page tell a
    reviewer something the card is already telling them, in place of the one
    thing only the document can show — where the text used to sit.
    """
    both = 'focusScroll("card", {mark: true, card: true, orphaned: true})'
    assert in_node(both, None, tmp_path) == "anchor"


def test_the_orphan_emphasis_lands_on_the_badge_the_card_already_writes(tmp_path):
    """No second vocabulary for a state the card has a word for.

    Three names have to agree for the emphasis to reach anything, and each is a
    place someone could rename half of: the class the card writes, the selector
    that finds it, and the rule that animates it. A miss is a click that silently
    does nothing — the exact failure this path was added to stop.
    """
    html = page().decode("utf-8")
    assert 'tag("orphaned", "bad orphan")' in html
    assert 'querySelector(".tag.orphan")' in html
    assert ".tag.orphan.flash { animation:" in html


def test_an_anchor_from_another_space_is_not_painted(tmp_path):
    """I12: offsets that belong to some other text are not drawn on this one.

    The page cannot check the claim — it never sees a snapshot — so it takes the
    server's word and simply declines to draw. Declining is the whole point: the
    alternative is not "no mark", it is a mark on a sentence the comment was
    never about, which reads as a correct answer and is the failure that was
    measured (12 of 17 comments on one review).
    """
    given = [
        {"id": "c-ok", "current_anchor": {"exact": "a", "start": 0, "end": 1}, "misplaced": False},
        {"id": "c-bad", "current_anchor": {"exact": "b", "start": 4, "end": 5}, "misplaced": True},
        {"id": "c-whole", "current_anchor": None, "misplaced": False},
    ]
    assert in_node("paintable(input).map((c) => c.id)", given, tmp_path) == ["c-ok"]


def test_the_bar_says_a_document_has_anchors_nothing_can_draw(tmp_path):
    """Undrawable is as much a fact about a document as orphaned is."""
    badges = in_node("badges({rounds: 1, misplaced: 2}).map((b) => b.text)", None, tmp_path)
    assert "2 misplaced" in badges


def test_a_comment_the_page_will_not_draw_says_so_on_its_card(tmp_path):
    """An undrawn mark with no badge is indistinguishable from a scrolled-away one."""
    html = page().decode("utf-8")
    assert 'tag("misplaced", "bad orphan")' in html


def test_the_document_is_drawn_before_the_threads_that_point_into_it():
    """A card becomes clickable only after the marks it scrolls to exist.

    This is what lets the navigation bar and the focus round trip coexist. One
    answer from the server redraws both halves, and because the document half
    goes first there is no moment where a card for the newly opened file sits
    beside the previous file's text. Focus finds its target by reading the DOM,
    so the order in `load` is the guarantee rather than a coincidence of how it
    happens to be written today — reversed, a click straight after switching
    files would quietly find nothing and look like a dead card.
    """
    html = page().decode("utf-8")
    body = html[html.index("async function load()") :]
    document_half = re.search(r"\bdraw\(\);", body)
    thread_half = re.search(r"\bdrawThreads\(\);", body)
    assert document_half is not None and thread_half is not None
    assert document_half.start() < thread_half.start()


# -- replying in the card ------------------------------------------------
#
# The box a reply is written in moved from the top of the column into the thread
# it answers. What a machine can hold to account there is not the box but the
# bookkeeping behind it — which card is open, and what has been typed on the ones
# that are not — so that is what is lifted and run here.


def reply_after(actions, tmp_path, start=None):
    """Fold ``actions`` through the page's own reducer, from ``start``."""
    given = {"reply": start or {"open": None, "drafts": {}}, "actions": actions}
    return in_node("input.actions.reduce(replyAfter, input.reply)", given, tmp_path)


def test_only_one_reply_editor_is_open_at_a_time(tmp_path):
    """Two open boxes is one answer landing on the wrong thread.

    The card is the only thing naming the target of a reply now that the title
    at the top of the column is gone, so a second box with a cursor in it has
    nothing on screen distinguishing it from the first.
    """
    ended = reply_after([{"kind": "open", "id": "c-1"}, {"kind": "open", "id": "c-2"}], tmp_path)
    assert ended["open"] == "c-2"


def test_turning_to_another_thread_keeps_what_the_first_one_was_told(tmp_path):
    """Closing a box is a side effect here, not the reviewer's decision.

    Clicking reply on another card says something about that card and nothing
    about this one, so it must not be the gesture that throws half a sentence
    away. The draft stays on the card it was addressed to and comes back when
    that card is opened again.
    """
    ended = reply_after(
        [
            {"kind": "open", "id": "c-1"},
            {"kind": "type", "id": "c-1", "text": "half a thought"},
            {"kind": "open", "id": "c-2"},
        ],
        tmp_path,
    )
    assert ended == {"open": "c-2", "drafts": {"c-1": "half a thought"}}


def test_cancel_is_the_only_click_that_throws_words_away(tmp_path):
    """One gesture means "I do not want this", and only it destroys anything.

    A reply that lands takes the same path, for the same reason: the words are
    on the thread now, so the box has nothing left to hold.
    """
    typed = [{"kind": "open", "id": "c-1"}, {"kind": "type", "id": "c-1", "text": "no"}]
    ended = reply_after([*typed, {"kind": "discard", "id": "c-1"}], tmp_path)
    assert ended == {"open": None, "drafts": {}}


def test_a_refused_reply_is_handed_back_rather_than_swallowed(tmp_path):
    """The resolved-thread refusal (I11), from the page's side.

    The box is closed before the request so that a reply which lands does not
    get redrawn around its own text. A refusal therefore has to undo that, and
    what it undoes to is the box open with the words still in it — otherwise the
    server naming the fix ("reopen it") would arrive beside an empty column and
    the reviewer would have to write the reply twice.
    """
    ended = reply_after(
        [
            {"kind": "open", "id": "c-1"},
            {"kind": "type", "id": "c-1", "text": "one more thing"},
            {"kind": "discard", "id": "c-1"},
            {"kind": "open", "id": "c-1"},
            {"kind": "type", "id": "c-1", "text": "one more thing"},
        ],
        tmp_path,
    )
    assert ended == {"open": "c-1", "drafts": {"c-1": "one more thing"}}


def test_an_emptied_box_is_not_a_draft(tmp_path):
    """A reviewer who deleted what they wrote did not leave a draft behind.

    Without this the badge below would offer back an empty string, which reads
    as "there is something here" about nothing.
    """
    ended = reply_after(
        [
            {"kind": "open", "id": "c-1"},
            {"kind": "type", "id": "c-1", "text": "typed"},
            {"kind": "type", "id": "c-1", "text": ""},
        ],
        tmp_path,
    )
    assert ended["drafts"] == {}


def test_a_composer_at_the_top_stows_the_open_box_without_emptying_it(tmp_path):
    """The rule is one cursor on the page, not one cursor in the column.

    Commenting on the document, disposing, and resolving all still open at the
    top, and any of them taking the cursor closes the card's box. None of them
    is a decision about the reply, so none of them drops it.
    """
    ended = reply_after(
        [
            {"kind": "open", "id": "c-1"},
            {"kind": "type", "id": "c-1", "text": "kept"},
            {"kind": "close"},
        ],
        tmp_path,
    )
    assert ended == {"open": None, "drafts": {"c-1": "kept"}}


def test_a_new_document_keeps_none_of_the_previous_ones_drafts(tmp_path):
    """Drafts are addressed to comment ids, and ids belong to one ledger.

    Carrying them across would mean offering a reply written for one document
    back on whichever card of another happened to share its id.
    """
    ended = reply_after(
        [{"kind": "open", "id": "c-1"}, {"kind": "type", "id": "c-1", "text": "gone"},
         {"kind": "reset"}],
        tmp_path,
    )
    assert ended == {"open": None, "drafts": {}}


def test_a_draft_nobody_can_see_says_that_it_is_there(tmp_path):
    """The one thing that makes stowing a draft different from losing it.

    A card whose box was closed to open another looks exactly like a card that
    threw the text away, and the reviewer cannot tell which happened by looking.
    The button says. The open card does not, because its words are on screen.
    """
    stowed = {"open": "c-2", "drafts": {"c-1": "kept"}}
    assert in_node("replyLabel(input, 'c-1')", stowed, tmp_path) == "reply · draft"
    assert in_node("replyLabel(input, 'c-2')", stowed, tmp_path) == "reply"
    open_with_words = {"open": "c-1", "drafts": {"c-1": "kept"}}
    assert in_node("replyLabel(input, 'c-1')", open_with_words, tmp_path) == "reply"


def test_an_action_the_reducer_does_not_know_is_a_typo_and_says_so(tmp_path):
    """Every caller passes a literal, so there is no such thing at runtime.

    Returning the state unchanged would make a misspelled kind look exactly like
    a button that was wired to nothing — the failure the route and field gates
    further down exist to keep out of this page.
    """
    caught = in_node(
        "(() => { try { replyAfter(input, {kind: 'stow', id: 'c-1'}); return 'no error'; }"
        " catch (error) { return error.message; } })()",
        {"open": None, "drafts": {}},
        tmp_path,
    )
    assert caught == "unknown reply action: stow"


def test_the_reply_box_is_written_into_the_chain_it_joins():
    """Where the box is, and that nothing reopened the old door beside it.

    Three names have to agree for the box to land in the card: the container the
    replies are drawn into, the selector that finds it, and the rule that indents
    it. The last assertion is the regression itself — a reply going back through
    `panel` would put the cursor at the top of the column again, which is what
    took the reviewer's eye off the thread they were answering.
    """
    html = page().decode("utf-8")
    assert '<div class="replies">' in html
    assert 'querySelector(".replies").appendChild(replyEditor(comment))' in html
    assert ".card .replybox {" in html
    assert 'box.className = "replybox";' in html
    assert 'panel("reply' not in html


def test_opening_a_reply_box_does_not_take_the_page_where_it_was_reading():
    """The same restraint the focus round trip is built on, one click earlier.

    `preventScroll` keeps the focus itself from moving anything, and `nearest`
    then asks for the least that puts the box on screen — nothing at all when it
    already is. A box that scrolled its column on open would fight the document
    pane for the viewport, which is the ping-pong `focusScroll` exists to stop.
    """
    html = page().decode("utf-8")
    assert "area.focus({ preventScroll: true })" in html
    assert 'area.scrollIntoView({ block: "nearest" })' in html


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
    assert payload["comment"]["undisposed"] is False


def test_deferring_leaves_the_comment_outstanding(opened, comment_id):
    _, payload = call(
        opened,
        "/api/dispose",
        {"target": comment_id, "verdict": "deferred", "reason": "waiting on the retry spec"},
    )
    assert payload["comment"]["undisposed"] is True


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
    store.close_round(round_id, author="alice", allow_undisposed=True)
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


# -- the port a document comes back on -----------------------------------
#
# A view that moves every restart takes its embedder's tab with it: the browser
# pane holding the URL goes dead the moment the server is restarted to pick up a
# code change, and the review loop starts looking like it lives in the process
# rather than in the ledger. So the port is a function of the document, on the
# same normalization the store keys by, and every departure from it is said out
# loud rather than discovered.


def free_port() -> int:
    """A port nothing is listening on, as of a moment ago."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def squat(port: int) -> socket.socket:
    """Hold ``port`` the way another process would, or skip.

    The suite does not own the machine's ports. When the one under test is
    already held from outside, there is nothing to arrange and asserting about
    it would be asserting about somebody else's process.
    """
    holder = socket.socket()
    holder.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        holder.bind(("127.0.0.1", port))
        holder.listen(1)
    except OSError as exc:
        holder.close()
        pytest.skip(f"port {port} is held from outside the suite ({exc})")
    return holder


@pytest.fixture
def derived_free(doc):
    """This document's derived port, confirmed free — or the test is skipped."""
    port = derived_port(doc)
    with socket.socket() as probe:
        try:
            probe.bind(("127.0.0.1", port))
        except OSError as exc:
            pytest.skip(f"port {port} is held from outside the suite ({exc})")
    return port


def test_one_document_derives_one_port(doc):
    assert derived_port(doc) == derived_port(doc)


def test_the_derived_port_sits_in_the_dynamic_range(tmp_path):
    """Nothing registered, nothing privileged — the range meant for exactly this."""
    ports = [derived_port(tmp_path / f"doc-{n}.md") for n in range(500)]
    assert all(PORT_FLOOR <= port <= PORT_CEILING for port in ports)


def test_different_documents_land_on_different_ports(tmp_path):
    """"Different" is the honest claim, not "distinct": 16k ports, more documents.

    Collisions are arithmetic, not a defect — the fallback below is what makes
    them harmless. What would be a defect is a derivation that clumps, so the
    margin here is wide enough to pass on chance and narrow enough to fail on a
    hash that stopped spreading.
    """
    ports = {derived_port(tmp_path / f"doc-{n}.md") for n in range(100)}
    assert len(ports) >= 95


def test_the_derived_port_follows_the_store_key_not_the_spelling(tmp_path, doc):
    """One document, one history, one port — the store's normalization, reused.

    A symlink and the file behind it are the same document to the store (§1.2),
    so a view opened through either has to be the same view. Deriving the port
    off the raw string instead would put one document on two ports, and the
    reviewer with the second URL would be reading the same ledger through an
    address nothing else agrees on.
    """
    link = tmp_path / "link-to-spec.md"
    link.symlink_to(doc)
    assert derived_port(link) == derived_port(doc)


def test_the_default_port_is_the_derived_one_and_a_restart_returns_to_it(store, doc, derived_free):
    """The whole point: same document, same URL host and port, across restarts."""
    first = WebView(store=store, path=doc, author="alice").bind()
    assert first.port == derived_free
    assert first.port_source == DERIVED
    assert first.wanted_port is None
    first.shutdown()

    second = WebView(store=store, path=doc, author="alice").bind()
    assert second.port == derived_free
    assert second.port_source == DERIVED
    second.shutdown()


def test_the_token_still_changes_every_restart(store, doc, derived_free):
    """A stable port is not a stable URL, and that is the design.

    The port is addressing; the token is authorisation. Restarting is a new
    grant, so the token is new — an embedder re-reads the printed line either
    way, and a token that outlived the process would be one a stale tab could
    still post through.
    """
    first = WebView(store=store, path=doc, author="alice").bind()
    first.shutdown()
    second = WebView(store=store, path=doc, author="alice").bind()
    second.shutdown()
    assert first.port == second.port
    assert first.token != second.token
    assert first.url != second.url


def test_a_taken_port_falls_back_to_a_free_one_and_records_why(store, doc):
    """Never a silent move: the reason the URL differs is on the view."""
    wanted = derived_port(doc)
    holder = squat(wanted)
    try:
        served = WebView(store=store, path=doc, author="alice")
        served.start()
        try:
            assert served.port != wanted
            assert served.port_source == FALLBACK
            assert served.wanted_port == wanted
            assert served.port_reason
            # And it is a working view, not a degraded one.
            assert state(served)["schema"] == VIEW_SCHEMA
        finally:
            served.shutdown()
    finally:
        holder.close()


def test_an_explicit_port_outranks_the_derived_one(store, doc, derived_free):
    port = free_port()
    view = WebView(store=store, path=doc, author="alice", port=port).bind()
    try:
        assert view.port == port != derived_free
        assert view.port_source == PINNED
    finally:
        view.shutdown()


def test_a_pinned_port_that_is_taken_is_refused_rather_than_moved(store, doc):
    """The caller named this port. Serving a different one would answer past them."""
    holder = squat(free_port())
    taken = holder.getsockname()[1]
    try:
        with pytest.raises(PortTaken) as caught:
            WebView(store=store, path=doc, author="alice", port=taken).bind()
    finally:
        holder.close()
    assert str(taken) in str(caught.value)


def test_port_zero_is_how_you_ask_for_a_free_one(store, doc):
    """The old default, kept as an opt-in for a caller that wants no stability."""
    view = WebView(store=store, path=doc, author="alice", port=0).bind()
    try:
        assert view.port > 0
        assert view.port_source == EPHEMERAL
    finally:
        view.shutdown()


def test_a_directory_view_derives_from_the_tree_not_the_document_it_opens_on(store, doc, tmp_path):
    """H15 serves a tree, so the tree is what the caller named and what decides.

    Deriving from the opening document would move the whole workspace's port the
    day someone adds a file that sorts before it — a port that depends on the
    contents of a folder is not one anybody can rely on.
    """
    space = Workspace(root=tmp_path)
    view = WebView(store=store, path=doc, author="alice", workspace=space, doc="spec.md")
    assert view.port_path == space.root
    assert derived_port(space.root) != derived_port(doc)


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
