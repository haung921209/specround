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

from specround import assetfiles, markdown
from specround.anchors import anchor_for
from specround.fold import Comment
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
from specround.wire import comment_json
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



def test_the_page_draws_on_having_a_text_not_on_having_a_round(tmp_path):
    """The blank screen, at the line that drew it.

    `draw` asked whether there was a *round* and put the blocked reason on the
    page in place of everything else — so every document nobody had opened a round
    on was a paragraph telling the reviewer to open one, and the text they had
    clicked to read was nowhere. The question it should have asked, and now does,
    is whether there is a text.
    """
    verdicts = in_node(
        "input.map(readable)",
        [
            {"reading": "base"},  # a round froze a text
            {"reading": "revision"},  # no round, and the file is there to read
            {"reading": None},  # neither — the server's reason is the whole page
        ],
        tmp_path,
    )
    assert verdicts == [True, True, False]
    # And the drawing does not ask the old question. `diffReason` still asks about
    # the round, which is right there — a diff needs the base a round froze. What
    # must not come back is the round deciding whether anything is drawn at all.
    html = page().decode("utf-8")
    drawing = html[html.index("function draw()") :]
    drawing = drawing[: drawing.index("\n}\n")]
    assert "readable(data)" in drawing
    assert "data.round" not in drawing


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


# -- how the page is arranged --------------------------------------------


def merged(stored, viewport, tmp_path):
    return in_node("mergeView(input.stored, input.viewport)",
                   {"stored": stored, "viewport": viewport}, tmp_path)


WIDE = 1600
NARROW = 820


def test_a_wide_viewport_gets_the_layout_the_page_has_always_had(tmp_path):
    """Nothing stored is the ordinary case, and it must not be a new layout.

    The numbers are the ones the stylesheet carried before any of this existed, so
    a reviewer who never touches a handle sees the page they already knew.
    """
    assert merged(None, WIDE, tmp_path) == {
        "nav": 260, "threads": 380, "font": 15, "navShut": False, "threadsShut": False,
    }


def test_a_narrow_viewport_starts_with_the_side_columns_folded(tmp_path):
    """The measured complaint, answered before the reviewer has to ask.

    260 and 380 of chrome in a pane that narrow leave a document no wider than the
    bar beside it. The only thing this does is choose where the page *starts*.
    """
    narrow = merged(None, NARROW, tmp_path)
    assert (narrow["navShut"], narrow["threadsShut"]) == (True, True)
    # And the widths are still there, so opening a column gives back a column
    # rather than a sliver.
    assert (narrow["nav"], narrow["threads"]) == (260, 380)


def test_a_stored_choice_outranks_the_width_guess(tmp_path):
    """`false` is an answer, and this is the whole reason the merge tests types.

    A reviewer who opened the thread column on a narrow screen stored exactly
    that. Read with a truthiness fallback it would be indistinguishable from an
    absent key, and the page would fold the column again on every single load —
    which is the setting not being remembered, in the one case it was set.
    """
    assert merged({"threadsShut": False}, NARROW, tmp_path)["threadsShut"] is False
    # The other direction too: folded on a wide screen stays folded.
    assert merged({"navShut": True}, WIDE, tmp_path)["navShut"] is True


def test_one_bad_value_does_not_cost_the_reviewer_the_rest(tmp_path):
    """Field by field, because a blob goes wrong one field at a time.

    An older page that wrote fewer keys and a hand-edited file with one nonsense
    value in it are the two real cases, and throwing the object away over either
    would drop settings that were perfectly good.
    """
    salvaged = merged({"nav": "wider please", "threads": 500, "font": 19}, WIDE, tmp_path)
    assert salvaged["nav"] == 260
    assert (salvaged["threads"], salvaged["font"]) == (500, 19)


def test_a_width_past_the_end_of_its_range_is_pulled_back(tmp_path):
    """A drag that reached the edge of the screen is not a layout.

    The floor is the point at which a column is folded without saying so, and
    folding is a button with a way back — so the clamp keeps the two gestures from
    meaning the same thing.
    """
    huge = merged({"nav": 9000, "threads": 9000, "font": 900}, WIDE, tmp_path)
    assert (huge["nav"], huge["threads"], huge["font"]) == (560, 760, 26)
    tiny = merged({"nav": 1, "threads": 1, "font": 1}, WIDE, tmp_path)
    assert (tiny["nav"], tiny["threads"], tiny["font"]) == (140, 240, 11)


def test_an_unreadable_blob_draws_the_default_layout(tmp_path):
    """There is nobody to report this to, and a reviewer only wanted to read."""
    for text in ["{not json", "", "null", "[1, 2]", '"a string"']:
        assert in_node("readView(input, 1600)", text, tmp_path) == {
            "nav": 260, "threads": 380, "font": 15, "navShut": False, "threadsShut": False,
        }


def test_only_the_keys_this_page_writes_go_back_into_storage(tmp_path):
    """This page is the only writer of the key, so it hands back nothing extra."""
    written = in_node("JSON.parse(writeView(input))",
                      {"nav": 300, "threads": 400, "font": 17, "navShut": True,
                       "threadsShut": False, "somethingElse": "not ours"},
                      tmp_path)
    assert set(written) == {"nav", "threads", "font", "navShut", "threadsShut"}


def test_the_arrangement_survives_a_round_trip_through_storage(tmp_path):
    """"It stays" is this, and a narrow viewport must not undo it on the way back."""
    kept = {"nav": 180, "threads": 700, "font": 21, "navShut": True, "threadsShut": False}
    assert in_node("readView(writeView(input), 820)", kept, tmp_path) == kept


def test_dragging_a_handle_opens_the_column_it_is_about(tmp_path):
    """A width says "show me this much", so it cannot leave the column folded.

    Without this, dragging a folded column's handle moves a number nobody can
    see — a handle that responds to the mouse and changes nothing on screen, which
    reads as broken rather than as folded.
    """
    folded = {"nav": 260, "threads": 380, "font": 15, "navShut": True, "threadsShut": True}
    dragged = in_node('viewAfter(input, {kind: "size", field: "nav", px: 310})', folded, tmp_path)
    assert (dragged["nav"], dragged["navShut"]) == (310, False)
    # And it says nothing about the other column.
    assert dragged["threadsShut"] is True


def test_double_clicking_a_handle_restores_that_column_and_leaves_the_other(tmp_path):
    started = {"nav": 520, "threads": 700, "font": 15, "navShut": False, "threadsShut": False}
    reset = in_node('viewAfter(input, {kind: "reset", field: "threads"})', started, tmp_path)
    assert (reset["threads"], reset["nav"]) == (380, 520)


def test_folding_keeps_the_width_so_unfolding_gives_the_same_column_back(tmp_path):
    """Two fields, not one, and this is what the second one buys.

    A fold that wrote the width to zero would have nothing to restore, and every
    unfold would hand back the default instead of the column the reviewer sized.
    """
    sized = {"nav": 180, "threads": 640, "font": 15, "navShut": False, "threadsShut": False}
    both = in_node(
        '[{kind: "fold", field: "threads"}, {kind: "fold", field: "threads"}]'
        ".reduce(viewAfter, input)",
        sized, tmp_path,
    )
    assert (both["threads"], both["threadsShut"]) == (640, False)


def test_the_reading_size_steps_and_stops_at_the_ends_of_its_range(tmp_path):
    """The buttons are held to the same bounds a stored value is."""
    at = {"nav": 260, "threads": 380, "font": 15, "navShut": False, "threadsShut": False}
    assert in_node('viewAfter(input, {kind: "font", step: 1}).font', at, tmp_path) == 16
    assert in_node('viewAfter(input, {kind: "font", step: -1}).font', at, tmp_path) == 14
    ceiling = "Array(40).fill({kind: 'font', step: 1}).reduce(viewAfter, input).font"
    floor = "Array(40).fill({kind: 'font', step: -1}).reduce(viewAfter, input).font"
    assert in_node(ceiling, at, tmp_path) == 26
    assert in_node(floor, at, tmp_path) == 11


def test_an_arrangement_action_the_reducer_does_not_know_is_a_typo_and_says_so(tmp_path):
    """Same rule as the reply reducer: a dead button must not be a silent one."""
    caught = in_node(
        "(() => { try { viewAfter(input, {kind: 'shrink', field: 'nav'}); return 'no error'; }"
        " catch (error) { return error.message; } })()",
        {"nav": 260, "threads": 380, "font": 15, "navShut": False, "threadsShut": False},
        tmp_path,
    )
    assert caught == "unknown view action: shrink"


def test_the_arrangement_is_kept_in_the_browser_and_never_in_the_ledger(opened):
    """The boundary G5 is about, drawn where it is easy to cross by accident.

    "The state is in the ledger" is about *review* state — rounds, comments,
    verdicts, resolutions: the things another reviewer, another machine, or a
    later reader has to agree with. How wide a column is on this screen is none of
    those. Recording it would put an event in a history whose whole value is that
    everything in it is a claim about the document, and it would travel to a
    teammate whose screen is a different size.
    """
    html = page().decode("utf-8")
    assert "window.localStorage.setItem(VIEW_KEY, writeView(state.view))" in html
    assert 'const VIEW_KEY = "specround.view";' in html
    # Not on the wire either, in the direction the route gate cannot see: the
    # server's projection says nothing about the layout, so there is no field for
    # a later change to start reading and no reason for one to start sending.
    assert not {"nav", "threads", "font", "navShut", "threadsShut"} & set(state(opened))


def test_a_folded_column_leaves_no_gap_where_it_was():
    """Two halves, and the layout is wrong with either one missing.

    A fold is a zero-width track *and* a hidden panel: the width alone leaves the
    panel's border and padding holding a 25px stripe open, and hiding the panel
    alone leaves the track it used to fill sitting there as a gap. Hiding it also
    means the columns cannot be placed by source order — auto-placement slides
    everything after a `display: none` child one track to the left, which puts the
    document where the bar was.
    """
    html = page().decode("utf-8")
    assert "#stage { grid-column: 3; min-width: 0; }" in html
    assert "aside { grid-column: 5; }" in html
    assert "main.navshut nav#files { display: none; }" in html
    assert "main.thrshut aside { display: none; }" in html


def test_the_handles_go_away_where_there_are_no_columns_to_resize():
    """The stacked layout, and a specificity trap that was measured, not guessed.

    Below the breakpoint the columns stack and there is nothing horizontal left to
    divide. The rule that shows the bar's handle is `main.tree #splitnav`, so a
    bare `#splitnav` in the media query loses to it — which left a zero-width
    `col-resize` target in the stacked layout: a handle that answers the mouse and
    moves nothing, the same dead-control failure the disabled diff button exists to
    avoid.
    """
    html = page().decode("utf-8")
    stacked = html[html.index("@media (max-width: 900px)") :]
    stacked = stacked[: stacked.index("\n  }")]
    assert "main.tree #splitnav, #splitthreads { display: none; }" in stacked


def test_redrawing_the_file_list_does_not_unfold_the_columns():
    """`className` on the layout element took the folds off with it.

    The tree flag and the two folds are classes on the same element, so assigning
    the whole attribute cleared the folds — on every reload, which is the one moment
    a reviewer is watching for their settings to come back.
    """
    html = page().decode("utf-8")
    assert 'classList.toggle("tree", Boolean(workspace))' in html
    assert '$("layout").className =' not in html


def test_the_mode_group_is_the_only_one_the_mode_setter_reaches():
    """The arrangement controls borrow the mode group's look, not its meaning.

    A loop over `.modes button` would set `aria-pressed="false"` on every fold
    switch every time a mode was applied — a switch reporting the opposite of what
    its column is doing, and a screen reader repeating it.
    """
    html = page().decode("utf-8")
    assert 'querySelectorAll("#modes button")' in html
    assert '.modes button"' not in html
    # And the accent stays the mode's word: a fold says its state some other way.
    assert '#columns button[aria-pressed="true"] { background: transparent;' in html


def test_the_reading_size_reaches_the_document_and_the_threads():
    """One size for the two things that are read together.

    A comment is read against the sentence it is about, so scaling one and not the
    other makes the pair harder to read together rather than easier. Everything
    inside them is relative from there — an absolute `px` further down would be a
    bigger paragraph with the same small code and the same small quote in it.
    """
    html = page().decode("utf-8")
    assert "#doc { padding: 18px 22px 60vh; min-width: 0; font-size: var(--read); }" in html
    assert "font-size: var(--read);\n  }" in html  # aside, at the end of its block
    assert "#doc.raw, #doc.diff { font-family: var(--mono); font-size: calc(var(--read) * .87)" in html
    # Nothing inside either one may pin a size in px, or it would not scale.
    inner = re.findall(r"\n  (?:#doc|aside|\.card|\.tag|\.empty)[^\n{]*\{[^}]*font-size: (\d+)px", html)
    assert inner == [], inner
    # The bar keeps its own size on purpose: a list of names to scan is not prose.
    assert "  .file .name { display: block; font-size: 13px;" in html


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
    assert payload["comment"]["verdict"] == "applied"
    assert payload["comment"]["settled"] is True


def test_deferring_leaves_the_comment_outstanding(opened, comment_id):
    _, payload = call(
        opened,
        "/api/dispose",
        {"target": comment_id, "verdict": "deferred", "reason": "waiting on the retry spec"},
    )
    assert payload["comment"]["verdict"] == "deferred"
    assert payload["comment"]["settled"] is False


def test_a_settled_comment_cannot_be_disposed_again(opened, comment_id):
    call(opened, "/api/dispose", {"target": comment_id, "verdict": "applied", "reason": "done"})
    status, payload = call(
        opened, "/api/dispose", {"target": comment_id, "verdict": "rejected", "reason": "no"}
    )
    assert status == 409
    assert "already settled" in payload["error"]["message"]


def test_the_page_can_overturn_a_verdict_it_recorded(opened, comment_id):
    """The view is a write surface of equal standing, so it carries the flag.

    Without it, a verdict recorded here by a slip could only be undone from a
    shell — and a gate you can only pass somewhere else is a gate people route
    around.
    """
    call(opened, "/api/dispose", {"target": comment_id, "verdict": "applied", "reason": "done"})
    status, payload = call(
        opened,
        "/api/dispose",
        {
            "target": comment_id,
            "verdict": "rejected",
            "reason": "backed out after the reply",
            "supersede": True,
        },
    )
    assert status == 200
    assert payload["comment"]["verdict"] == "rejected"
    assert [d["verdict"] for d in payload["comment"]["dispositions"]] == [
        "applied",
        "rejected",
    ]
    assert [d["supersede"] for d in payload["comment"]["dispositions"]] == [False, True]


def test_the_page_cannot_supersede_a_comment_with_nothing_settled(opened, comment_id):
    status, payload = call(
        opened,
        "/api/dispose",
        {"target": comment_id, "verdict": "applied", "reason": "done", "supersede": True},
    )
    assert status == 409
    assert "no settled verdict to overturn" in payload["error"]["message"]


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


# -- the files a document points at --------------------------------------
#
# Over the socket, like everything else here: the browser is what asks for a
# picture, and what it meets is a status, a content type, and a body. Asserting
# on `assetfiles.resolve` alone would leave the token in front of it, the header
# that stops sniffing, and the four refusals staying four untested.


#: A one-pixel PNG — small, real, and not a fixture anybody has to keep.
PIXEL = bytes.fromhex(
    "89504e470d0a1a0a0000000d494844520000000100000001080600000"
    "01f15c4890000000a49444154789c6300010000050001"
    "0d0a2db40000000049454e44ae426082"
)


def asset(view, ref, *, token=None, doc=None):
    """One asset request. Returns ``(status, headers, body-bytes)``.

    Not through ``call``: that decodes utf-8 and parses JSON, and the answer
    here is a PNG on the way through.
    """
    query = {"t": view.token if token is None else token, "path": ref}
    if doc is not None:
        query["doc"] = doc
    url = f"http://{view.host}:{view.port}/api/asset?" + urllib.parse.urlencode(query)
    try:
        with urllib.request.urlopen(urllib.request.Request(url), timeout=10) as response:
            return response.status, dict(response.headers), response.read()
    except urllib.error.HTTPError as error:
        return error.code, dict(error.headers), error.read()


def refusal(view, ref, **kwargs):
    """The reason code behind a refused asset, with the status asserted."""
    status, _, body = asset(view, ref, **kwargs)
    assert status == 404, body
    return json.loads(body)["error"]


def test_a_picture_beside_the_document_is_served(view, doc):
    (doc.parent / "shot.png").write_bytes(PIXEL)
    status, headers, body = asset(view, "shot.png")
    assert status == 200
    assert body == PIXEL
    assert headers["Content-Type"] == "image/png"


def test_a_picture_in_a_subdirectory_is_served(view, doc):
    (doc.parent / "img").mkdir()
    (doc.parent / "img" / "shot.png").write_bytes(PIXEL)
    status, _, body = asset(view, "img/shot.png")
    assert status == 200
    assert body == PIXEL


def test_a_served_file_may_not_be_sniffed_into_something_else(view, doc):
    """The whitelist decides the type; the browser does not get a second opinion.

    A file named ``.png`` whose bytes look like markup is the case — without
    ``nosniff`` a browser may decide it knows better and run it, on the origin
    that holds this view's token.
    """
    (doc.parent / "shot.png").write_bytes(b"<html><script>alert(1)</script>")
    status, headers, _ = asset(view, "shot.png")
    assert status == 200
    assert headers["Content-Type"] == "image/png"
    assert headers["X-Content-Type-Options"] == "nosniff"
    assert "default-src 'none'" in headers["Content-Security-Policy"]


def test_an_asset_needs_the_token_like_everything_else(view, doc):
    """A local server that reads files off this disk is not an open one.

    Every tab in the browser can reach this port. The picture route is a read of
    a named file, which is exactly the thing a token is in front of.
    """
    (doc.parent / "shot.png").write_bytes(PIXEL)
    status, _, _ = asset(view, "shot.png", token="not-the-token")
    assert status == 403


def test_a_reference_that_climbs_out_of_the_directory_is_refused(view, doc, tmp_path):
    outside = tmp_path.parent / "outside.png"
    outside.write_bytes(PIXEL)
    error = refusal(view, f"../{outside.name}")
    assert error["reason"] == "outside"


def test_an_absolute_reference_is_refused_as_outside(view, doc, tmp_path):
    (doc.parent / "shot.png").write_bytes(PIXEL)
    error = refusal(view, str(doc.parent / "shot.png"))
    assert error["reason"] == "outside"
    assert "absolute" in error["message"]


def test_a_symlink_is_followed_when_it_lands_inside(view, doc):
    """Following is the rule; where it lands is the question.

    Refusing every link would be the easy boundary and the wrong one — a tree
    that keeps its captures in a linked folder is an ordinary tree.
    """
    real = doc.parent / "real.png"
    real.write_bytes(PIXEL)
    (doc.parent / "linked.png").symlink_to(real)
    status, _, body = asset(view, "linked.png")
    assert status == 200
    assert body == PIXEL


def test_a_symlink_that_lands_outside_is_refused(view, doc, tmp_path):
    """The real path is what is judged, so a link is not a way around the edge."""
    outside = tmp_path.parent / "elsewhere.png"
    outside.write_bytes(PIXEL)
    (doc.parent / "sneaky.png").symlink_to(outside)
    error = refusal(view, "sneaky.png")
    assert error["reason"] == "outside"
    assert str(outside) in error["message"]


def test_a_type_that_is_not_an_image_is_refused(view, doc):
    (doc.parent / "notes.txt").write_bytes(b"hello")
    error = refusal(view, "notes.txt")
    assert error["reason"] == "unsupported"


def test_an_svg_is_refused_and_says_why_that_one_is_different(view, doc):
    """v1 leaves SVG out, and an author who tried learns why in one line.

    It is not "unknown type": an SVG opened directly is a document that can
    script on this origin, and this origin holds the token.
    """
    (doc.parent / "diagram.svg").write_bytes(b"<svg xmlns='http://www.w3.org/2000/svg'/>")
    error = refusal(view, "diagram.svg")
    assert error["reason"] == "unsupported"
    assert "script" in error["message"]


def test_a_file_over_the_cap_is_refused_by_size_and_not_by_silence(view, doc, monkeypatch):
    monkeypatch.setattr(assetfiles, "MAX_BYTES", 16)
    (doc.parent / "huge.png").write_bytes(PIXEL)
    error = refusal(view, "huge.png")
    assert error["reason"] == "too-large"
    assert str(len(PIXEL)) in error["message"]


def test_a_reference_to_nothing_is_missing_and_says_so(view):
    error = refusal(view, "absent.png")
    assert error["reason"] == "missing"


def test_the_four_refusals_are_four_different_reasons(view, doc, tmp_path, monkeypatch):
    """The whole point of item 3: one status, never one reason.

    A silent 404 for all four is a debugging session spent guessing which of the
    four mistakes was made.
    """
    (doc.parent / "notes.txt").write_bytes(b"hello")
    (doc.parent / "big.png").write_bytes(PIXEL)
    outside = tmp_path.parent / "outside.png"
    outside.write_bytes(PIXEL)
    monkeypatch.setattr(assetfiles, "MAX_BYTES", 16)
    reasons = {
        refusal(view, "absent.png")["reason"],
        refusal(view, f"../{outside.name}")["reason"],
        refusal(view, "notes.txt")["reason"],
        refusal(view, "big.png")["reason"],
    }
    assert reasons == set(assetfiles.REASONS)


def test_an_empty_reference_is_refused_rather_than_serving_the_directory(view):
    assert refusal(view, "")["reason"] == "missing"


def test_a_capture_in_a_document_reaches_the_page_as_a_picture(view, doc):
    """The whole thing, from the markdown to the bytes.

    The three halves have to meet or none of them is worth anything: the render
    has to produce an ``<img>``, the page has to point it at the route, and the
    route has to hand back the file. This is the assertion that they do.
    """
    doc.write_text("# Spec\n\n![the screen](img/shot.png)\n", encoding="utf-8")
    (doc.parent / "img").mkdir()
    (doc.parent / "img" / "shot.png").write_bytes(PIXEL)

    rendered = state(view)["render"]
    assert '<img src="img/shot.png"' in rendered

    src = re.search(r'<img src="([^"]+)"', rendered).group(1)
    status, headers, body = asset(view, src)
    assert status == 200
    assert body == PIXEL
    assert headers["Content-Type"] == "image/png"


def test_the_page_points_a_relative_image_at_the_route_with_the_token(tmp_path):
    assert (
        in_node('assetUrl("img/shot.png", null, "tok")', None, tmp_path)
        == "/api/asset?t=tok&path=img%2Fshot.png"
    )


def test_the_page_names_the_document_a_workspace_image_counts_from(tmp_path):
    url = in_node('assetUrl("shot.png", "sub/spec.md", "tok")', None, tmp_path)
    assert "doc=sub%2Fspec.md" in url


def test_the_page_leaves_an_address_it_does_not_serve_alone(tmp_path):
    """A remote image, a data URI, a protocol-relative host — none are ours."""
    left = in_node(
        '["https://example.com/x.png", "data:image/png;base64,AAA", "//host/x.png", ""]'
        '.map((ref) => assetUrl(ref, null, "tok"))',
        None,
        tmp_path,
    )
    assert left == [None, None, None, None]


# -- ... and the same, over a tree ---------------------------------------


@pytest.fixture
def tree(tmp_path, clock):
    """A workspace view over a two-level tree, with a picture in each place."""
    root = tmp_path / "tree"
    (root / "sub").mkdir(parents=True)
    (root / "shared").mkdir()
    document = root / "sub" / "spec.md"
    document.write_text("# Sub\n\nProse.\n", encoding="utf-8")
    (root / "sub" / "beside.png").write_bytes(PIXEL)
    (root / "shared" / "common.png").write_bytes(PIXEL)
    space = Workspace(root=root, clock=clock)
    served = WebView(
        store=space.store_for(document),
        path=document,
        author="alice",
        port=0,
        workspace=space,
        doc="sub/spec.md",
    )
    served.start()
    try:
        yield served
    finally:
        served.shutdown()


def test_in_a_tree_a_reference_still_counts_from_the_document(tree):
    """Not from the root. `beside.png` in ``sub/spec.md`` means the one in ``sub``."""
    status, _, body = asset(tree, "beside.png", doc="sub/spec.md")
    assert status == 200
    assert body == PIXEL


def test_in_a_tree_a_sibling_directory_is_inside_the_edge(tree):
    """The tree is the thing under review, so ``../shared/x.png`` is in it."""
    status, _, body = asset(tree, "../shared/common.png", doc="sub/spec.md")
    assert status == 200
    assert body == PIXEL


def test_a_tree_still_has_an_edge(tree, tmp_path):
    outside = tmp_path / "outside.png"
    outside.write_bytes(PIXEL)
    error = refusal(tree, "../../outside.png", doc="sub/spec.md")
    assert error["reason"] == "outside"


def test_a_file_view_serves_only_its_own_directory(view, doc, tmp_path):
    """The narrower edge, and the reason the two are separate properties.

    A view started on one file was handed one directory. A sibling folder in the
    same parent is not something the caller pointed at.
    """
    sibling = doc.parent.parent / "sibling"
    sibling.mkdir(exist_ok=True)
    (sibling / "x.png").write_bytes(PIXEL)
    assert refusal(view, "../sibling/x.png")["reason"] == "outside"


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


def test_every_comment_field_the_page_reads_is_a_field_the_wire_sends():
    """The same check one level down, where the failure is worse.

    A key that left :func:`comment_json` does not fail loudly in JavaScript — it
    reads ``undefined``, which is falsy. When ``state`` and ``undisposed`` were
    removed from the comment payload, a page still reading them would have drawn
    every comment as decided, dropped the accent on the ones nobody had ruled
    on, and hidden the dispose button behind a condition that was never true.
    Nothing else here would notice: the functions that read a comment touch the
    DOM, so they sit outside the block a JavaScript engine can lift and run.
    """
    html = page().decode("utf-8")
    read = set(re.findall(r"\bcomment\.([a-z][A-Za-z_]*)", html))
    assert read, "the page should read comments"
    sent = set(
        comment_json(Comment(id="c-x", round="r-x", kind="comment", author="a", ts="t"))
    )
    assert read <= sent, sorted(read - sent)
