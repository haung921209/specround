"""An HTML document reviewed as it renders, anchored in its own source text.

The anchor space of an ``.html`` document is its source, exactly as for
markdown: the CLI, raw mode and the diff all count in it. What changes is the
render mode, which shows the page itself — so every text run it emits has to
hold ``source[start:start + len(text)] == text`` just like the markdown render.
"""

import json
import re
import urllib.request

import pytest

from specround.store import ReviewStore
from specround.webview import WebView

HTML = """<!doctype html>
<html><head><title>Title text</title>
<style>p { color: red }</style></head>
<body>
<h1>Failover &amp; recovery</h1>
<p>The proxy waits 30 seconds. Retries are capped.</p>
<table><tr><th>Item</th><th>Value</th></tr>
<tr><td>timeout</td><td class="n">45s</td></tr></table>
<pre>
line one
</pre>
<svg viewBox="0 0 10 10"><text>svg label</text></svg>
<div class="quiz"><button data-ok="1">Answer</button><p class="fb"></p></div>
<script>document.querySelector('.fb').textContent = 'script ran';</script>
</body></html>
"""

_RUN = re.compile(r'<span class="sr-t" data-s="(\d+)">(.*?)</span>', re.DOTALL)


def runs(rendered):
    return [(int(start), text) for start, text in _RUN.findall(rendered)]


def test_every_run_is_a_slice_of_the_source():
    from specround import htmlview

    rendered = htmlview.render(HTML)
    found = runs(rendered)
    assert found
    for start, text in found:
        assert HTML[start:start + len(text)] == text
    texts = [text for _, text in found]
    assert "timeout" in texts and "45s" in texts
    assert any("30 seconds" in text for text in texts)


def test_markup_that_is_not_prose_is_left_alone():
    from specround import htmlview

    rendered = htmlview.render(HTML)
    texts = " ".join(text for _, text in runs(rendered))
    for hidden in ("Title text", "color: red", "svg label", "script ran"):
        assert hidden not in texts
    # Removing the spans gives back the document byte for byte.
    assert _strip(rendered) == HTML


def test_a_pre_keeps_its_leading_newline_outside_the_run():
    from specround import htmlview

    rendered = htmlview.render(HTML)
    assert "<pre>\n<span" in rendered


def test_crlf_never_enters_a_run():
    from specround import htmlview

    text = "<p>one\r\ntwo</p>"
    found = runs(htmlview.render(text))
    assert [t for _, t in found] == ["one", "\ntwo"]
    for start, run in found:
        assert text[start:start + len(run)] == run


def test_is_html_names_the_suffixes():
    from specround import htmlview

    assert htmlview.is_html("a/b.HTML") and htmlview.is_html("x.htm")
    assert not htmlview.is_html("spec.md")


def _strip(rendered):
    out, at = [], 0
    for match in _RUN.finditer(rendered):
        out.append(rendered[at:match.start()])
        out.append(match.group(2))
        at = match.end()
    out.append(rendered[at:])
    return "".join(out)


# -- through the view -------------------------------------------------------


@pytest.fixture
def page_doc(tmp_path):
    path = tmp_path / "share.html"
    path.write_text(HTML, encoding="utf-8")
    return path


@pytest.fixture
def html_store(page_doc, clock):
    return ReviewStore.for_document(page_doc, clock=clock)


@pytest.fixture
def served(html_store, page_doc):
    html_store.open_round(page_doc, author="alice", title="html pass")
    view = WebView(store=html_store, path=page_doc, author="alice", port=0)
    view.start()
    try:
        yield view
    finally:
        view.shutdown()


def call(view, path, body=None):
    if body is not None:
        shown = call(view, "/api/state")
        round_ = shown["round"]
        body = {"expected_round": round_["id"], "expected_base": round_["base"],
                "expected_review": round_["revision"], "expected_revision": shown["live_digest"], **body}
    request = urllib.request.Request(
        f"http://{view.host}:{view.port}{path}?t={view.token}",
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.loads(response.read())


def run_containing(payload, needle):
    for start, text in runs(payload["render"]):
        if needle in text:
            at = start + text.index(needle)
            return at, at + len(needle)
    raise AssertionError(f"no run holds {needle!r}")


def test_paragraph_and_cell_comments_land_in_the_ledger(served, html_store):
    """(a) The rendered page is what the view serves, and its runs anchor."""
    shown = call(served, "/api/state")
    assert shown["format"] == "html"
    assert "<table>" in shown["render"] and "<td" in shown["render"]
    for needle in ("30 seconds", "45s"):
        start, end = run_containing(shown, needle)
        call(served, "/api/comment", {"space": "base", "start": start, "end": end, "body": "on " + needle})
    comments = html_store.fold().comments.values()
    assert sorted(c.anchor.exact for c in comments) == ["30 seconds", "45s"]


def test_a_markdown_document_still_renders_as_markdown(store, doc):
    store.open_round(doc, author="alice")
    view = WebView(store=store, path=doc, author="alice", port=0)
    view.start()
    try:
        shown = call(view, "/api/state")
    finally:
        view.shutdown()
    assert shown["format"] == "markdown"
    assert '<span class="t" data-s=' in shown["render"]


def test_refresh_carries_comments_onto_the_edited_page(served, html_store, page_doc):
    """(b) Edit the paragraph, publish, and the comment follows its text."""
    start, end = run_containing(call(served, "/api/state"), "Retries are capped.")
    call(served, "/api/comment", {"space": "base", "start": start, "end": end, "body": "kept"})
    start, end = run_containing(call(served, "/api/state"), "45s")
    call(served, "/api/comment", {"space": "base", "start": start, "end": end, "body": "lost"})
    page_doc.write_text(
        HTML.replace("<h1>", "<p>A new opening paragraph.</p>\n<h1>")
            .replace("waits 30 seconds", "waits 60 seconds")
            .replace("<td class=\"n\">45s</td>", "<td class=\"n\">n/a</td>"),
        encoding="utf-8",
    )
    call(served, "/api/refresh", {})
    shown = call(served, "/api/state")
    by_body = {c["body"]: c for c in shown["comments"]}
    kept = by_body["kept"]["current_anchor"]
    assert shown["base"][kept["start"]:kept["end"]] == "Retries are capped."
    assert run_containing(shown, "Retries are capped.") == (kept["start"], kept["end"])
    assert by_body["lost"]["orphaned"]
    assert len(shown["round"]["revisions"]) == 2


def test_the_round_diff_is_text(served, page_doc):
    """(b) A file edit past the base shows as text rows, not as markup soup."""
    page_doc.write_text(HTML.replace("waits 30 seconds", "waits 60 seconds"), encoding="utf-8")
    rows = call(served, "/api/state")["diff"]["rows"]
    changed = [(r["op"], r["text"]) for r in rows if r["op"] != "same"]
    assert ("removed", "<p>The proxy waits 30 seconds. Retries are capped.</p>") in changed
    assert ("added", "<p>The proxy waits 60 seconds. Retries are capped.</p>") in changed
