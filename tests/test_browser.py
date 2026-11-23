"""Optional real-engine smoke check; no browser library or download required."""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from specround import webview
from specround.reviews import Review
from specround.store import ReviewStore
from specround.workspace import Workspace


@pytest.mark.parametrize("width,height", [(390, 600), (800, 900), (1000, 900), (1440, 900)])
def test_responsive_comment_layout_keeps_editor_visible_and_drafts_intact(store, doc, monkeypatch, tmp_path, width, height):
    """A hidden composer, remounted draft, or inline text counted as source fails this."""
    browser = os.environ.get("SPECROUND_TEST_BROWSER") or shutil.which("chromium")
    if not browser:
        pytest.skip("set SPECROUND_TEST_BROWSER to a Chromium/headless-shell executable")
    doc.write_text("# Review\n\nTarget 😀 sentence.\n\n[Diagram](diagram.html)\n\n" + "Long document paragraph.\n\n" * 70, encoding="utf-8")
    (doc.parent / "diagram.html").write_text("<!doctype html><h1>Architecture preview</h1>", encoding="utf-8")
    round_id = store.open_round(doc, author="test")
    store.add_comment(round_id, author="test", body="Anchored feedback",
                      anchor=store.anchor_in_round(round_id, "Target 😀 sentence."))
    store.add_comment(round_id, author="test", body="Whole document feedback")
    original = webview.page()
    probe = r"""
<script>
(async () => {
  if (new URLSearchParams(location.search).has('layout-child')) return;
  const report = document.createElement('pre'); report.id = 'browser-result';
  document.body.appendChild(report);
  const check = (ok, message) => { if (!ok) throw Error(message); };
  const waitFor = async predicate => {
    for (let i = 0; i < 500; i++) {
      if (predicate()) return;
      await new Promise(resolve => setTimeout(resolve, 20));
    }
    throw Error('timed out');
  };
  const visible = element => {
    const r = element.getBoundingClientRect();
    return r.width > 0 && r.height > 0 && r.top >= 0 && r.bottom <= innerHeight
      && r.left >= 0 && r.right <= innerWidth;
  };
  const selectTarget = () => {
    const holder = [...document.querySelectorAll('#doc [data-s]')].find(e => e.textContent === 'Target 😀 sentence.');
    const range = document.createRange(); range.selectNodeContents(holder);
    getSelection().removeAllRanges(); getSelection().addRange(range);
    return selectedSpan();
  };
  try {
    await waitFor(() => state.data);
    await waitFor(() => document.querySelector('.htmlpreview iframe'));
    const preview = document.querySelector('.htmlpreview iframe');
    check(document.getElementById('layout').classList.contains('inline-comments') === (innerWidth < 1100), 'automatic placement chose wrong layout');
    const span = selectTarget();
    setView({kind:'fold', field:'threads'});
    commentBox(span);
    const area = document.querySelector('#composer textarea');
    check(visible(area), 'comment editor is hidden or outside the viewport');
    check(visible(document.querySelector('#composer .primary')), 'submit button is off screen');
    area.value = 'Keep my draft'; area.dispatchEvent(new Event('input'));
    const mode = document.getElementById('commentlayout');
    check(mode, 'comment placement control missing');
    for (const value of ['inline', 'sidebar', 'auto', 'inline']) {
      mode.value = value; mode.dispatchEvent(new Event('change'));
      check(document.querySelector('#composer textarea') === area && area.value === 'Keep my draft', 'placement lost/remounted draft');
      check(visible(area), 'placement hid editor');
      check(document.querySelector('.htmlpreview iframe') === preview, 'placement remounted HTML preview');
    }
    check(document.querySelector('#doc #composer'), 'inline editor is not beside source');
    const selectedAgain = selectTarget();
    check(selectedAgain.start === 10 && selectedAgain.end === 28, 'inline comments changed Unicode source offsets');
    const cid = state.data.comments.find(c => c.body === 'Anchored feedback').id;
    openReply(cid);
    check(document.querySelector('#composer textarea') === area && area.value === 'Keep my draft', 'reply erased new comment draft');
    commentBox(null);
    check(document.querySelector('#composer textarea') === area, 'second composer erased draft');
    setMode('raw');
    check(document.querySelector('#composer textarea') === area && area.value === 'Keep my draft', 'render mode erased draft');
    setMode('render');
    check(document.querySelector('#composer textarea') === area, 'return to render erased draft');
    area.value = ''; clearComposer();
    focus(cid, 'mark'); openReply(cid);
    const reply = document.querySelector('.replybox textarea');
    reply.value = 'Reply draft'; reply.dispatchEvent(new Event('input'));
    for (const value of ['sidebar', 'inline']) {
      mode.value = value; mode.dispatchEvent(new Event('change'));
      check(document.querySelector('.replybox textarea') === reply && reply.value === 'Reply draft', 'placement lost reply draft');
      check(visible(reply), 'placement hid reply editor');
    }
    check(document.querySelectorAll('.card[data-comment]').length === 2, 'threads duplicated or missing');
    check(document.querySelector('#inlinefallback').textContent.includes('Whole document feedback'), 'document comment lost');
    check(document.querySelector('#doc .inline-thread'), 'anchored comment not inline');
    setMode('diff');
    document.getElementById('changesonly').click();
    check(document.querySelectorAll('.card[data-comment]').length === 2, 'diff filter removed inline threads');
    check(document.querySelector('.replybox textarea')?.value === 'Reply draft', 'diff filter lost inline reply editor');
    document.getElementById('changesonly').click();
    setMode('render');
    const attached = state.data.comments.find(c => c.id === cid);
    await sendReply(attached, reply.value);
    check(state.data.comments.find(c => c.id === cid).replies.at(-1).body === 'Reply draft', 'inline reply did not reach its thread');
    commentBox(selectTarget());
    document.querySelector('#composer textarea').value = 'Inline selection saved';
    document.querySelector('#composer .primary').click();
    await waitFor(() => state.data.comments.some(c => c.body === 'Inline selection saved'));
    const saved = state.data.comments.find(c => c.body === 'Inline selection saved');
    check(saved.current_anchor.start === 10 && saved.current_anchor.end === 28, 'inline submission anchored incorrectly');
    await waitFor(() => !document.querySelector('#composer textarea'));
    state.focused = null;
    document.getElementById('nextthread').click();
    check(state.focused === cid, 'next unresolved comment did not follow document order');
    const previousFocused = state.focused;
    document.getElementById('nextthread').click();
    check(state.focused !== previousFocused, 'next unresolved comment did not advance');
    document.getElementById('prevthread').click();
    check(state.focused === previousFocused, 'previous unresolved comment did not return');
    for (const name of ['reading', 'balanced', 'review', 'reset']) {
      layoutPreset(name);
      check(document.documentElement.scrollWidth <= innerWidth, 'layout preset overflow');
    }
    mode.value = 'inline'; mode.dispatchEvent(new Event('change'));
    const source = [...document.querySelectorAll('#doc [data-s]')].find(e => e.textContent === 'Target 😀 sentence.');
    check(!source.querySelector('.card, #composer'), 'inline controls entered source holder');
    const previous = state.view.threads;
    document.getElementById('splitthreads').dispatchEvent(new KeyboardEvent('keydown', {key:'ArrowLeft'}));
    check(state.view.threads > previous, 'keyboard resize did not adjust width');
    check(JSON.parse(localStorage.getItem('specround.view')).placement === 'inline', 'placement not persisted');
    check(document.documentElement.scrollWidth <= innerWidth, 'horizontal page overflow');
    if (innerWidth > 1200) {
      report.textContent = 'waiting for child view';
      const frame = document.createElement('iframe');
      frame.style.cssText = 'position:fixed;left:0;top:0;z-index:99;display:block;width:1300px;height:650px;border:0';
      frame.src = location.href + '&layout-child=1'; document.body.appendChild(frame);
      await waitFor(() => { try { return frame.contentWindow.eval('state.data'); } catch (_) { return false; } });
      const win = frame.contentWindow;
      const select = win.document.getElementById('commentlayout');
      select.value = 'auto'; select.dispatchEvent(new win.Event('change'));
      win.commentBox({space:'base',start:10,end:28,quote:'Target 😀 sentence.'});
      const draft = win.document.querySelector('#composer textarea'); draft.value = 'resize draft';
      for (const size of [600, 1200, 360]) {
        report.textContent = 'resizing child to ' + size;
        frame.style.width = size + 'px';
        void frame.offsetWidth;
        // Headless virtual time may exhaust its budget before a compositor
        // frame. Exercise our resize handler against the real resized viewport.
        win.dispatchEvent(new win.Event('resize'));
        check(win.innerWidth === size && win.document.getElementById('layout').classList.contains('inline-comments') === (size < 1100), 'resize did not choose responsive placement');
        check(win.document.querySelector('#composer textarea') === draft && draft.value === 'resize draft', 'viewport resize lost draft');
        const r = draft.getBoundingClientRect();
        check(r.width > 0 && r.top >= 0 && r.bottom <= win.innerHeight && r.right <= size, 'viewport resize hid editor');
        check(win.document.documentElement.scrollWidth <= size, 'viewport resize overflow');
      }
      frame.remove();
    }
    setMode('raw');
    check(await act({space:'base',start:9,end:9,body:'Boundary caret'}, '/api/comment'), 'insertion comment failed');
    check(await act({space:'base',start:10,end:11,body:'First letter'}, '/api/comment'), 'following line comment failed');
    const letter = state.data.comments.find(c => c.body === 'First letter');
    const group = document.querySelector('.card[data-comment="' + letter.id + '"]').closest('.inline-thread');
    check(group?.previousElementSibling?.querySelector('[data-s="10"]'), 'insertion caret moved next-line thread to wrong row');
    startEditing();
    document.getElementById('edittext').value += '\nUnsent suggestion';
    setMode('render');
    check(state.mode === 'raw' && document.getElementById('edittext').value.includes('Unsent suggestion'), 'mode switch discarded suggestion draft');
    stopEditing(); setMode('render');
    report.textContent = 'PASS';
  } catch (error) { report.textContent = 'FAIL: ' + error.message + ' [' + report.textContent + ']'; }
})();
</script>
"""
    monkeypatch.setattr(webview, "page", lambda: original.replace(b"</body>", probe.encode() + b"</body>"))
    served = webview.WebView(store=store, path=doc, author="test", port=0)
    served.start()
    try:
        result = subprocess.run([browser, "--no-sandbox", "--headless", "--disable-gpu",
                                 f"--window-size={width},{height}", f"--user-data-dir={tmp_path / 'browser'}",
                                 f"--screenshot={tmp_path / 'layout.png'}",
                                 "--dump-dom", "--virtual-time-budget=15000", served.url],
                                capture_output=True, text=True, timeout=35)
        assert result.returncode == 0, result.stderr
        import re
        outcome = re.search(r'<pre id="browser-result">(.*?)</pre>', result.stdout, re.S)
        assert outcome and outcome.group(1) == "PASS", outcome.group(1) if outcome else result.stdout[-3000:]
    finally:
        served.shutdown()


def test_html_preview_is_isolated_and_survives_commenting(store, doc, monkeypatch, tmp_path):
    browser = os.environ.get("SPECROUND_TEST_BROWSER") or shutil.which("chromium")
    if not browser:
        pytest.skip("set SPECROUND_TEST_BROWSER to a Chromium/headless-shell executable")

    doc.write_text("# Preview 😀\n\n[Diagram](diagram.html)\n\n😀 before Comment here.\n\n## Details\n\n[Jump](#details)\n", encoding="utf-8")
    fixture = """<!doctype html>
<html><body><svg viewBox="0 0 300 100"><text y="40">Diagram</text></svg>
"""
    if os.environ.get("SPECROUND_TEST_HTML"):
        fixture = Path(os.environ["SPECROUND_TEST_HTML"]).read_text(encoding="utf-8")
    probe_script = """
<script>
let parentBlocked = false, storageBlocked = false;
try { parent.document.body; } catch (_) { parentBlocked = true; }
try { localStorage.getItem('token'); } catch (_) { storageBlocked = true; }
let interactive = true;
const themeButton = document.getElementById('btn-theme');
if (themeButton) {
  const before = document.documentElement.dataset.theme;
  themeButton.click();
  interactive = document.documentElement.dataset.theme !== before;
}
document.addEventListener('securitypolicyviolation', (event) => {
  if (event.effectiveDirective === 'connect-src') parent.postMessage({
    probe: true, parentBlocked, storageBlocked, networkBlocked: true, interactive,
    referrer: document.referrer, url: location.href,
    svg: !!document.querySelector('svg'),
  }, '*');
});
fetch('https://example.invalid/blocked').catch(() => {});
</script>"""
    (doc.parent / "diagram.html").write_text(fixture + probe_script, encoding="utf-8")
    store.open_round(doc, author="test")
    original = webview.page()
    probe = r"""
<script>
(async () => {
  const check = (ok, message) => { if (!ok) throw Error(message); };
  const waitFor = async (predicate) => {
    for (let i = 0; i < 500; i++) {
      if (predicate()) return;
      await new Promise(resolve => setTimeout(resolve, 20));
    }
    throw Error('timed out');
  };
  let child;
  window.addEventListener('message', event => { if (event.data.probe) child = event.data; });
  const report = document.createElement('pre');
  report.id = 'browser-result';
  document.body.appendChild(report);
  try {
    await waitFor(() => child);
    check(child.parentBlocked && child.storageBlocked && child.networkBlocked, 'sandbox boundary');
    check(child.referrer === '' && !child.url.includes(TOKEN), 'token exposed');
    check(child.svg && child.interactive, 'HTML SVG or controls did not render');
    const frame = document.querySelector('.htmlpreview iframe');
    const source = frame.src;
    document.getElementById('stage').style.width = '320px';
    check(frame.getBoundingClientRect().width <= 320, 'preview overflow');
    const selectComment = () => {
      const holder = [...document.querySelectorAll('#doc [data-s]')].find(e => e.textContent.includes('Comment here.'));
      const walker = document.createTreeWalker(holder, NodeFilter.SHOW_TEXT);
      let node;
      while ((node = walker.nextNode())) {
        if (!node.nodeValue.includes('Comment here.')) continue;
        const range = document.createRange();
        const offset = node.nodeValue.indexOf('Comment');
        range.setStart(node, offset);
        range.setEnd(node, offset + 7);
        window.getSelection().removeAllRanges();
        window.getSelection().addRange(range);
        return selectedSpan();
      }
      throw Error('selection node missing');
    };
    const selected = selectComment();
    check(selected.quote === 'Comment' && selected.start === 47 && selected.end === 54, 'Unicode DOM offsets: ' + JSON.stringify(selected));
    const at = selected.start;
    check(await act({body: 'first', start: at, end: at, space: 'base'}, '/api/comment'), 'first comment');
    const afterCaret = selectComment();
    check(afterCaret.start === at, 'generated caret changed selection offsets');
    check(await act({body: 'second', ...afterCaret}, '/api/comment'), 'second comment');
    check(document.querySelector('.htmlpreview iframe') === frame && frame.src === source, 'frame reloaded');
    check(document.querySelectorAll('.caret').length === 1, 'insertion caret duplicated');
    const mark = document.querySelector('mark.anch.here');
    check(mark && mark.textContent === 'Comment', 'anchor drifted');
    const link = document.querySelector('#doc a [data-s]');
    check(link && link.textContent === 'Diagram', 'link anchor lost');
    const target = state.focused;
    check(await act({target, body: 'reply'}, '/api/reply'), 'reply');
    check(document.querySelector('.htmlpreview iframe') === frame, 'reply reloaded HTML');
    check(document.querySelector('mark.anch.here')?.textContent === 'Comment', 'reply lost focused mark');
    check(await act({target, verdict: 'applied', reason: 'done'}, '/api/dispose'), 'dispose');
    check(!state.data.comments.find(c => c.id === target).resolved, 'dispose silently resolved');
    check(await act({target, resolved: true}, '/api/thread'), 'resolve');
    check(document.querySelector('.htmlpreview iframe') === frame && frame.src === source, 'resolve reloaded HTML');
    check(!document.querySelector('mark.anch'), 'resolved mark left behind');
    check(document.querySelector('#doc a[href="#md-details"]') && document.getElementById('md-details'), 'heading link');
    check(document.querySelector('.outline a[href="#md-details"]'), 'contents missing');
    setMode('raw');
    check(selectComment().start === at, 'raw offsets differ from render');
    setMode('diff');
    check(selectComment().start === at, 'diff offsets differ from render');
    check(!(await act({open: false, allow_undisposed: true}, '/api/round')), 'unresolved round closed');
    commentBox(selectComment());
    const draft = document.querySelector('#composer textarea');
    draft.value = 'keep this draft';
    await api('/api/round', {open: false, allow_undisposed: true, allow_unresolved: true});
    await api('/api/round', {open: true});
    await load();
    document.querySelector('#composer .primary').click();
    await waitFor(() => document.getElementById('notice').textContent.includes('reload'));
    check(document.querySelector('#composer textarea') === draft && draft.value === 'keep this draft', 'stale draft lost');
    report.textContent = 'PASS';
  } catch (error) {
    report.textContent = 'FAIL: ' + error.message;
  }
})();
</script>
"""
    monkeypatch.setattr(webview, "page", lambda: original.replace(b"</body>", probe.encode() + b"</body>"))
    served = webview.WebView(store=store, path=doc, author="test", port=0)
    served.start()
    try:
        result = subprocess.run([
            browser, "--headless", "--no-first-run", "--no-default-browser-check",
            "--disable-background-networking", "--disable-component-update",
            f"--user-data-dir={tmp_path / 'browser-profile'}", "--dump-dom",
            "--virtual-time-budget=15000", served.url,
        ], capture_output=True, text=True, timeout=60)
        assert result.returncode == 0, result.stderr[-3000:]
        assert '<pre id="browser-result">PASS</pre>' in result.stdout, result.stdout[-4500:]
        assert len(store.fold().comments) == 2
        anchors = [c.anchor for c in store.fold().comments.values()]
        assert [a.exact for a in anchors] == ["", "Comment"]
    finally:
        served.shutdown()


def test_same_round_update_notifies_without_replacing_an_active_draft(store, doc, doc_text, monkeypatch, tmp_path):
    browser = os.environ.get("SPECROUND_TEST_BROWSER") or shutil.which("chromium")
    if not browser:
        pytest.skip("set SPECROUND_TEST_BROWSER to Chromium")
    round_id = store.open_round(doc, author="test")
    cid = store.add_comment(round_id, author="test", body="existing",
                            anchor=store.anchor_in_round(round_id, "30 seconds"))
    original = webview.page()
    def publish(handler):
        handler._body()
        doc.write_text("Published introduction.\n\n" + doc_text, encoding="utf-8")
        store.refresh_round(round_id, doc, author="agent")
        handler._json({"ok": True})
    monkeypatch.setitem(webview._POSTS, "/api/test-publish", publish)
    probe = r"""<script>
(async () => {
  const report = document.createElement('pre');
  report.id = 'browser-result'; document.body.appendChild(report);
  const check = (ok, reason) => { if (!ok) throw Error(reason); };
  const waitFor = async (test) => {
    for (let i = 0; i < 500; i++) { if (test()) return; await new Promise(r => setTimeout(r, 20)); }
    throw Error('timed out');
  };
  try {
    await waitFor(() => state.data);
    const round = state.data.round.id;
    const revision = state.data.round.revision;
    const text = $('doc').textContent;
    commentBox({space:'base', start:0, end:0, quote:''});
    const draft = document.querySelector('#composer textarea');
    draft.value = 'keep my draft';
    await api('/api/test-publish', {});
    await waitFor(() => !checkingReview);
    await checkReview();
    check(!$('reviewnotice').hidden && !$('loadrevision').hidden, 'notification missing');
    check(state.data.round.revision === revision && $('doc').textContent === text, 'poll replaced text');
    await acceptReviewRevision();
    check(state.data.round.revision === revision && draft.value === 'keep my draft', 'draft not protected');
    // Sending the draft written on the earlier revision saves it, carried into the
    // current one, and the page catches up so the saved card is on screen.
    document.querySelector('#composer .primary').click();
    await waitFor(() => state.data.round.revision !== revision);
    check(state.data.round.id === round, 'wrong round');
    check(![...document.querySelectorAll('#composer textarea')].some(a => a.value.trim()), 'saved draft left in the composer');
    check([...document.querySelectorAll('.body')].some(e => e.textContent === 'keep my draft'), 'saved comment not drawn');
    check($('doc').textContent.includes('Published introduction.'), 'published text missing');
    check(document.querySelector('mark.anch')?.textContent === '30 seconds', 'anchor lost');
    check($('reviewnotice').hidden, 'accepted update still pending');
    report.textContent = 'PASS';
  } catch (error) { report.textContent = 'FAIL: ' + error.message; }
})();
</script>"""
    monkeypatch.setattr(webview, "page", lambda: original.replace(b"</body>", probe.encode() + b"</body>"))
    served = webview.WebView(store=store, path=doc, author="test", port=0)
    served.start()
    try:
        result = subprocess.run([browser, "--headless", "--no-first-run", "--disable-background-networking",
                                 f"--user-data-dir={tmp_path / 'profile'}", "--dump-dom", "--virtual-time-budget=15000", served.url],
                                capture_output=True, text=True, timeout=60)
        assert result.returncode == 0, result.stderr[-2500:]
        assert '<pre id="browser-result">PASS</pre>' in result.stdout, result.stdout[-2000:] + result.stderr[-2000:]
        state = store.fold()
        assert list(state.comments)[0] == cid and state.rounds[round_id].open
        assert [c.body for c in state.comments.values()][1:] == ["keep my draft"]
    finally:
        served.shutdown()


def test_stale_send_with_another_draft_open_says_it_was_saved(store, doc, doc_text, monkeypatch, tmp_path):
    # A reply draft keeps the page on the revision it was drawn on, so the
    # saved card cannot be drawn yet; the page has to say the comment was saved.
    browser = os.environ.get("SPECROUND_TEST_BROWSER") or shutil.which("chromium")
    if not browser:
        pytest.skip("set SPECROUND_TEST_BROWSER to Chromium")
    round_id = store.open_round(doc, author="test")
    cid = store.add_comment(round_id, author="test", body="existing",
                            anchor=store.anchor_in_round(round_id, "30 seconds"))
    original = webview.page()
    def publish(handler):
        handler._body()
        doc.write_text("Published introduction.\n\n" + doc_text, encoding="utf-8")
        store.refresh_round(round_id, doc, author="agent")
        handler._json({"ok": True})
    monkeypatch.setitem(webview._POSTS, "/api/test-publish", publish)
    probe = r"""<script>
(async () => {
  const report = document.createElement('pre');
  report.id = 'browser-result'; document.body.appendChild(report);
  const check = (ok, reason) => { if (!ok) throw Error(reason); };
  const waitFor = async (test) => {
    for (let i = 0; i < 500; i++) { if (test()) return; await new Promise(r => setTimeout(r, 20)); }
    throw Error('timed out');
  };
  try {
    await waitFor(() => state.data);
    const revision = state.data.round.revision;
    openReply(state.data.comments[0].id);
    const reply = document.querySelector('.card .replybox textarea');
    reply.value = 'half a reply';
    reply.dispatchEvent(new Event('input'));
    commentBox(null);
    document.querySelector('#composer textarea').value = 'sent from the old page';
    await api('/api/test-publish', {});
    document.querySelector('#composer .primary').click();
    await waitFor(() => $('notice').textContent.includes('Saved as'));
    check(state.data.round.revision === revision, 'reply draft did not hold the page');
    check(state.reply.drafts[state.data.comments[0].id] === 'half a reply', 'reply draft lost');
    report.textContent = 'PASS';
  } catch (error) { report.textContent = 'FAIL: ' + error.message; }
})();
</script>"""
    monkeypatch.setattr(webview, "page", lambda: original.replace(b"</body>", probe.encode() + b"</body>"))
    served = webview.WebView(store=store, path=doc, author="test", port=0)
    served.start()
    try:
        result = subprocess.run([browser, "--headless", "--no-first-run", "--disable-background-networking",
                                 f"--user-data-dir={tmp_path / 'profile'}", "--dump-dom", "--virtual-time-budget=15000", served.url],
                                capture_output=True, text=True, timeout=60)
        assert result.returncode == 0, result.stderr[-2500:]
        assert '<pre id="browser-result">PASS</pre>' in result.stdout, result.stdout[-2000:] + result.stderr[-2000:]
        bodies = [c.body for c in store.fold().comments.values()]
        assert bodies == ["existing", "sent from the old page"]
    finally:
        served.shutdown()


def test_review_browser_shows_scope_and_explicitly_includes_new_files(monkeypatch, tmp_path):
    browser = os.environ.get("SPECROUND_TEST_BROWSER") or shutil.which("chromium")
    if not browser:
        pytest.skip("set SPECROUND_TEST_BROWSER to Chromium")
    root = tmp_path / "docs"
    root.mkdir()
    (root / "a.md").write_text("# A\n\nShared text.\n", encoding="utf-8")
    a = Review.create([root], title="Alpha", author="alice")
    b = Review.create([root], title="Beta", author="alice")
    foreign = b.store.add_comment(b.store.latest_round(root / "a.md").id, author="bob", body="only Beta")
    def add_file(handler):
        handler._body()
        (root / "new.md").write_text("# New\n", encoding="utf-8")
        handler._json({"ok": True})
    monkeypatch.setitem(webview._POSTS, "/api/test-new-file", add_file)
    original = webview.page()
    probe = r"""<script>
(async () => {
  const report = document.createElement('pre'); report.id = 'browser-result'; document.body.appendChild(report);
  const check = (ok, why) => { if (!ok) throw Error(why); };
  const waitFor = async (test) => { for (let i=0;i<500;i++) { if(test()) return; await new Promise(r=>setTimeout(r,20)); } throw Error('timed out'); };
  try {
    await waitFor(() => state.data);
    const scope = state.data.scope.id;
    check($('scopename').textContent.includes('Alpha') && state.data.comments.length === 0, 'scope mixed');
    let denied = false;
    try { await api('/api/reply', {target: 'FOREIGN_ID', body:'wrong review'}); } catch (_) { denied = true; }
    check(denied, 'foreign write accepted');
    await api('/api/test-new-file', {});
    await waitFor(() => !checkingReview);
    await checkReview();
    check(!$('publishrevision').hidden && $('reviewstatus').textContent.includes('new files'), 'new file notice missing');
    check(state.data.scope.members.length === 1, 'membership changed before publication');
    check(await act({}, '/api/scope-refresh'), 'review refresh failed');
    check(state.data.scope.members.includes('new.md') && state.data.scope.new_files.length === 0, 'new file not published');
    commentBox(null);
    const draft = $('composer').querySelector('textarea'); draft.value = 'Keep this on a.md';
    openDocument('new.md');
    check(state.doc === 'a.md' && $('composer').querySelector('textarea') === draft, 'file navigation discarded or retargeted draft');
    draft.value = ''; clearComposer();
    setView({kind:'size', field:'nav', px:560});
    setView({kind:'size', field:'threads', px:760});
    check($('doc').getBoundingClientRect().width >= 300 && document.documentElement.scrollWidth <= innerWidth, 'wide saved panels squeezed the document');
    openDocument('new.md');
    await waitFor(() => state.data.workspace.selected === 'new.md');
    check(state.data.scope.id === scope && state.data.comments.length === 0, 'navigation lost scope');
    check(await act({whole:true, body:'only Alpha'}, '/api/comment'), 'scoped comment failed');
    check(state.data.comments.length === 1 && state.data.comments[0].body === 'only Alpha', 'wrong comment set');
    check(roundAction(state.data).label.includes('review'), 'close control is not review-wide');
    report.textContent = 'PASS';
  } catch (error) { report.textContent = 'FAIL: ' + error.message; }
})();</script>""".replace("FOREIGN_ID", foreign)
    monkeypatch.setattr(webview, "page", lambda: original.replace(b"</body>", probe.encode() + b"</body>"))
    view = webview.WebView(store=a.store, path=root / "a.md", author="alice", port=0,
                           review=a, workspace=Workspace(root, review=a), doc="a.md")
    view.start()
    try:
        result = subprocess.run([browser, "--headless", "--no-first-run", "--disable-background-networking", "--window-size=1200,900",
                                 f"--user-data-dir={tmp_path / 'profile'}", "--dump-dom", "--virtual-time-budget=15000", view.url],
                                capture_output=True, text=True, timeout=60)
        assert result.returncode == 0, result.stderr[-2000:]
        assert '<pre id="browser-result">PASS</pre>' in result.stdout, result.stdout[-2500:] + result.stderr[-1500:]
        assert b.members() == ["a.md"]
        assert list(b.store.fold().comments) == [foreign]
        assert a.members() == ["a.md", "new.md"]
    finally:
        view.shutdown()


def test_html_document_renders_isolated_and_comments_from_the_page(doc, clock, monkeypatch, tmp_path):
    """An .html document under review: its page renders in a sandbox, its script
    cannot reach the review UI, and a selection in the page anchors in the ledger."""
    browser = os.environ.get("SPECROUND_TEST_BROWSER") or shutil.which("chromium")
    if not browser:
        pytest.skip("set SPECROUND_TEST_BROWSER to a Chromium/headless-shell executable")
    page_doc = doc.parent / "share.html"
    page_doc.write_text("""<!doctype html><html><head><title>t</title></head><body>
<p>The proxy waits 30 seconds.</p>
<table><tr><th>Item</th><td>cell value 45s</td></tr></table>
<div data-s="2"><h2>Author attr</h2><p>Hello world</p></div>
<p id="math">mutated $x$ more text</p>
<div class="quiz"><button id="q">Answer</button><p class="fb"></p></div>
<script>
(() => {
  const math = document.getElementById('math').firstChild;
  math.innerHTML = math.textContent.replace('$x$', '<b>x</b>');
  const result = {};
  try { parent.document.body.innerHTML = 'hacked'; result.parentDom = 'reached'; } catch (_) { result.parentDom = 'blocked'; }
  try { result.token = String(parent.TOKEN); } catch (_) { result.token = 'blocked'; }
  try { localStorage.getItem('x'); result.storage = 'reached'; } catch (_) { result.storage = 'blocked'; }
  try { top.location.href = 'about:blank'; } catch (_) {}
  result.referrer = document.referrer; result.url = location.href;
  parent.postMessage({ specround: true, kind: 'select', span: { start: 0, end: 5 }, rect: {} }, '*');
  parent.postMessage({ specround: true, kind: 'focus', id: 'c-forged' }, '*');
  document.getElementById('q').addEventListener('click', () => { document.querySelector('.fb').textContent = 'quiz ok'; });
  document.getElementById('q').click();
  result.quiz = document.querySelector('.fb').textContent;
  addEventListener('message', (event) => {
    if (event.data && event.data.probe === 'select') {
      const needle = event.data.needle;
      const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
      let text;
      while ((text = walker.nextNode()) && !text.nodeValue.includes(needle)) {}
      const range = document.createRange();
      range.setStart(text, text.nodeValue.indexOf(needle)); range.setEnd(text, text.nodeValue.indexOf(needle) + needle.length);
      getSelection().removeAllRanges(); getSelection().addRange(range);
    }
    if (event.data && event.data.probe === 'math') {
      parent.postMessage({ probe: 'math', kept: !!document.querySelector('#math b') && !!document.querySelector('div[data-s="2"] h2') }, '*');
    }
    if (event.data && event.data.probe === 'marks') {
      parent.postMessage({ probe: 'marks', marks: [...document.querySelectorAll('mark.sr-anch')].map(m => m.textContent) }, '*');
    }
  });
  parent.postMessage({ probe: 'child', ...result }, '*');
})();
</script>
</body></html>""", encoding="utf-8")
    from specround.store import ReviewStore
    html_store = ReviewStore.for_document(page_doc, clock=clock)
    html_store.open_round(page_doc, author="test")
    original = webview.page()
    probe = r"""
<script>
(async () => {
  const check = (ok, message) => { if (!ok) throw Error(message); };
  const waitFor = async (predicate) => {
    for (let i = 0; i < 500; i++) {
      if (predicate()) return;
      await new Promise(resolve => setTimeout(resolve, 20));
    }
    throw Error('timed out');
  };
  let child, marks, mathKept;
  window.addEventListener('message', event => {
    if (event.data && event.data.probe === 'child') child = event.data;
    if (event.data && event.data.probe === 'marks') marks = event.data.marks;
    if (event.data && event.data.probe === 'math') mathKept = event.data.kept;
  });
  const report = document.createElement('pre');
  report.id = 'browser-result';
  document.body.appendChild(report);
  try {
    await waitFor(() => state.data);
    check(state.data.format === 'html', 'not served as html');
    await waitFor(() => child);
    const frame = document.querySelector('#doc iframe.htmlpage');
    check(frame && frame.getAttribute('sandbox') === 'allow-scripts', 'page frame is not sandboxed');
    check(child.parentDom === 'blocked' && child.token === 'blocked' && child.storage === 'blocked', 'sandbox boundary');
    check(child.referrer === '' && !child.url.includes(TOKEN), 'token exposed');
    check(child.quiz === 'quiz ok', 'document script did not run');
    check(document.getElementById('modes') && document.body.textContent !== 'hacked', 'review UI changed');
    await new Promise(resolve => setTimeout(resolve, 300));
    check(!document.querySelector('#composer textarea'), 'forged message opened a composer');
    check(state.data.comments.length === 0, 'forged message wrote');
    const select = async (needle) => {
      state.frameSpan = 'pending';
      frame.contentWindow.postMessage({ probe: 'select', needle }, '*');
      await waitFor(() => state.frameSpan !== 'pending');
      return state.frameSpan;
    };
    const world = await select('world');
    check(world && world.quote === 'world', 'author data-s shifted the anchor: ' + JSON.stringify(world));
    check(await select('more text') === null, 'a run rewritten by the page was counted');
    await select('45s');
    check(state.frameSpan && state.frameSpan.quote === '45s', 'cell selection');
    check(document.getElementById('float').classList.contains('show'), 'float not shown for page selection');
    document.getElementById('float').dispatchEvent(new MouseEvent('mousedown', { bubbles: true }));
    await waitFor(() => document.querySelector('#composer textarea'));
    document.querySelector('#composer textarea').value = 'cell comment';
    document.querySelector('#composer .primary').click();
    await waitFor(() => state.data.comments.some(c => c.body === 'cell comment'));
    const saved = state.data.comments.find(c => c.body === 'cell comment');
    check(sliceText(state.data.base, saved.current_anchor.start, saved.current_anchor.end) === '45s', 'anchored on wrong text');
    check(document.querySelector('#doc iframe.htmlpage') === frame, 'commenting reloaded the page frame');
    frame.contentWindow.postMessage({ probe: 'marks' }, '*');
    await waitFor(() => marks);
    check(marks.includes('45s'), 'comment not highlighted in the page');
    frame.contentWindow.postMessage({ probe: 'math' }, '*');
    await waitFor(() => mathKept !== undefined);
    check(mathKept, 'painting undid the page script\'s rewrite');
    report.textContent = 'PASS';
  } catch (error) { report.textContent = 'FAIL: ' + error.message; }
})();
</script>
"""
    monkeypatch.setattr(webview, "page", lambda: original.replace(b"</body>", probe.encode() + b"</body>"))
    served = webview.WebView(store=html_store, path=page_doc, author="test", port=0)
    served.start()
    try:
        result = subprocess.run([browser, "--no-sandbox", "--headless", "--disable-gpu", "--window-size=1300,900",
                                 f"--user-data-dir={tmp_path / 'browser'}",
                                 "--dump-dom", "--virtual-time-budget=15000", served.url],
                                capture_output=True, text=True, timeout=35)
        assert result.returncode == 0, result.stderr
        import re
        outcome = re.search(r'<pre id="browser-result">(.*?)</pre>', result.stdout, re.S)
        assert outcome and outcome.group(1) == "PASS", outcome.group(1) if outcome else result.stdout[-3000:]
        comments = list(html_store.fold().comments.values())
        assert [c.anchor.exact for c in comments] == ["45s"]
    finally:
        served.shutdown()


def test_a_comment_inside_a_folded_block_opens_it(store, doc, monkeypatch, tmp_path):
    """A closed <details> hides its anchors; scrolling to one there would move nothing."""
    browser = os.environ.get("SPECROUND_TEST_BROWSER") or shutil.which("chromium")
    if not browser:
        pytest.skip("set SPECROUND_TEST_BROWSER to Chromium")
    doc.write_text("# Fold\n\n<details><summary>talked about</summary>\n\nInside sentence.\n\n</details>\n\n"
                   "<details><summary>quiet</summary>\n\nUntouched.\n\n</details>\n\n"
                   "<details><summary>titled</summary>\n\nBody unseen.\n\n</details>\n", encoding="utf-8")
    round_id = store.open_round(doc, author="test")
    cid = store.add_comment(round_id, author="test", body="here",
                            anchor=store.anchor_in_round(round_id, "Inside sentence."))
    store.add_comment(round_id, author="test", body="on the title",
                      anchor=store.anchor_in_round(round_id, "talked about"))
    store.add_comment(round_id, author="test", body="only the title",
                      anchor=store.anchor_in_round(round_id, "titled"))
    original = webview.page()
    probe = r"""<script>
(async () => {
  const report = document.createElement('pre');
  report.id = 'browser-result'; document.body.appendChild(report);
  const check = (ok, reason) => { if (!ok) throw Error(reason); };
  const waitFor = async (test) => {
    for (let i = 0; i < 500; i++) { if (test()) return; await new Promise(r => setTimeout(r, 20)); }
    throw Error('timed out');
  };
  try {
    await waitFor(() => document.querySelector('#doc mark.anch'));
    const [talked, quiet, titled] = document.querySelectorAll('#doc details.docfold');
    check(!titled.open, 'a comment on a visible summary opened its fold');
    check(talked.querySelector('summary').textContent === 'talked about', 'summary not drawn');
    check(talked.open, 'commented fold stayed closed');
    check(!quiet.open, 'uncommented fold opened');
    talked.open = false;
    focus('CID', 'card');
    check(talked.open, 'focusing the comment did not open its fold');
    talked.querySelector('summary mark.anch').click();
    check(talked.open, 'clicking a mark in the summary folded the block');
    talked.open = false;
    draw();
    check(document.querySelector('#doc details.docfold').open, 'a redraw left a commented fold shut');
    report.textContent = 'PASS';
  } catch (error) { report.textContent = 'FAIL: ' + error.message; }
})();
</script>""".replace("CID", cid)
    monkeypatch.setattr(webview, "page", lambda: original.replace(b"</body>", probe.encode() + b"</body>"))
    served = webview.WebView(store=store, path=doc, author="test", port=0)
    served.start()
    try:
        result = subprocess.run([browser, "--headless", "--no-first-run", "--disable-background-networking",
                                 f"--user-data-dir={tmp_path / 'profile'}", "--dump-dom", "--virtual-time-budget=15000", served.url],
                                capture_output=True, text=True, timeout=60)
        assert result.returncode == 0, result.stderr[-2500:]
        assert '<pre id="browser-result">PASS</pre>' in result.stdout, result.stdout[-2000:] + result.stderr[-2000:]
    finally:
        served.shutdown()


def test_a_share_page_asks_for_a_name_once_and_remembers_it(store, doc, monkeypatch, tmp_path):
    """Prefilling the owner's name on a share page, or forgetting the name, fails this."""
    browser = os.environ.get("SPECROUND_TEST_BROWSER") or shutil.which("chromium")
    if not browser:
        pytest.skip("set SPECROUND_TEST_BROWSER to a Chromium/headless-shell executable")
    store.open_round(doc, author="test")
    original = webview.page()
    served = webview.WebView(store=store, path=doc, author="owner", port=0, share_scope="comment")
    probe = r"""
<script>
(async () => {
  const params = new URLSearchParams(location.search);
  if (params.has('child')) return;
  const report = document.createElement('pre'); report.id = 'browser-result';
  document.body.appendChild(report);
  const check = (ok, message) => { if (!ok) throw Error(message); };
  const waitFor = async predicate => {
    for (let i = 0; i < 500; i++) {
      if (predicate()) return;
      await new Promise(resolve => setTimeout(resolve, 20));
    }
    throw Error('timed out');
  };
  const child = async token => {
    const frame = document.createElement('iframe');
    frame.src = '/?t=' + token + '&child=1'; document.body.appendChild(frame);
    await waitFor(() => { try { return frame.contentWindow.eval('state.data'); } catch (_) { return false; } });
    return frame.contentWindow.document.getElementById('author').value;
  };
  try {
    localStorage.clear();
    await waitFor(() => state.data);
    check(document.getElementById('author').value === '', 'share page prefilled a name');
    let asked = 0;
    window.prompt = () => { asked += 1; return 'carol'; };
    commentBox(null);
    document.querySelector('#composer textarea').value = 'first';
    document.querySelector('#composer .primary').click();
    await waitFor(() => state.data.comments.some(c => c.body === 'first'));
    check(state.data.comments.find(c => c.body === 'first').author === 'carol', 'prompted name not sent');
    check(document.getElementById('author').value === 'carol', 'name not shown after prompt');
    commentBox(null);
    document.querySelector('#composer textarea').value = 'second';
    document.querySelector('#composer .primary').click();
    await waitFor(() => state.data.comments.some(c => c.body === 'second'));
    check(asked === 1, 'asked for a name twice');
    check(await child('SHARE') === 'carol', 'name not remembered across a reload');
    const box = document.getElementById('author');
    box.value = 'dave'; box.dispatchEvent(new Event('change'));
    check(await child('SHARE') === 'dave', 'changed name not remembered');
    check(await child('OWNER') === 'owner', 'owner page took the remembered share name');
    // Storage that refuses (a private window) must not turn a declined name
    // into a question on every comment.
    Storage.prototype.getItem = () => { throw Error('blocked'); };
    Storage.prototype.setItem = () => { throw Error('blocked'); };
    box.value = '';
    window.prompt = () => { asked += 1; return ''; };
    for (const text of ['blocked one', 'blocked two']) {
      commentBox(null);
      document.querySelector('#composer textarea').value = text;
      document.querySelector('#composer .primary').click();
      await waitFor(() => state.data.comments.some(c => c.body === text));
      check(state.data.comments.find(c => c.body === text).author === 'guest-127.0.0.1', 'declined name not a guest');
    }
    check(asked === 1, 'blocked storage asked again: ' + asked);
    report.textContent = 'PASS';
  } catch (error) { report.textContent = 'FAIL: ' + error.message; }
})();
</script>
"""
    served.start()
    probe = probe.replace("SHARE", served.share_token).replace("OWNER", served.token)
    monkeypatch.setattr(webview, "page", lambda: original.replace(b"</body>", probe.encode() + b"</body>"))
    try:
        result = subprocess.run([browser, "--no-sandbox", "--headless", "--disable-gpu",
                                 f"--user-data-dir={tmp_path / 'browser'}",
                                 "--dump-dom", "--virtual-time-budget=15000", served.share_url],
                                capture_output=True, text=True, timeout=35)
        assert result.returncode == 0, result.stderr
        import re
        outcome = re.search(r'<pre id="browser-result">(.*?)</pre>', result.stdout, re.S)
        assert outcome and outcome.group(1) == "PASS", outcome.group(1) if outcome else result.stdout[-3000:]
    finally:
        served.shutdown()


@pytest.mark.parametrize("grant,width", [("owner", 1440), ("share", 800)])
def test_another_clients_comments_appear_without_reloading(store, doc, monkeypatch, tmp_path, grant, width):
    """A page that waits for a reload to show someone else's comment, reply or
    verdict fails this, and so does one that shows it by dropping the reader's
    draft, cursor or place in the document."""
    browser = os.environ.get("SPECROUND_TEST_BROWSER") or shutil.which("chromium")
    if not browser:
        pytest.skip("set SPECROUND_TEST_BROWSER to a Chromium/headless-shell executable")
    doc.write_text("# Review\n\nTop sentence.\n\n" + "".join(f"Paragraph {i}.\n\n" for i in range(80)), encoding="utf-8")
    round_id = store.open_round(doc, author="test")
    first = store.add_comment(round_id, author="test", body="first thread",
                              anchor=store.anchor_in_round(round_id, "Paragraph 60."))
    fillers = [store.add_comment(round_id, author="test", body=f"filler {i}",
                                 anchor=store.anchor_in_round(round_id, f"Paragraph {61 + i}."))
               for i in range(14)]
    steps = iter([
        lambda: (store.add_comment(round_id, author="other", body="from another client",
                                   anchor=store.anchor_in_round(round_id, "Top sentence.")),
                 store.reply(first, author="other", body="another client's reply"),
                 store.dispose(first, author="other", verdict="answered", reason="settled elsewhere")),
        lambda: store.reply(first, author="other", body="second reply while typing"),
        lambda: store.reply(first, author="other", body="third reply during composition"),
        lambda: (store.dispose(fillers[0], author="other", verdict="answered", reason="done"),
                 store.resolve(fillers[0], author="other", actor="human")),
        lambda: (store.reply(fillers[2], author="other", body="a long reply above the reader. " * 40),
                 store.reply(fillers[5], author="other", body="a long reply below the reader's point. " * 40)),
        lambda: store.reply(fillers[9], author="other", body="step six reply"),
        lambda: (store.dispose(fillers[10], author="other", verdict="answered", reason="done"),
                 store.resolve(fillers[10], author="other", actor="human"),
                 store.add_comment(round_id, author="other", body="growth above the vanished card",
                                   anchor=store.anchor_in_round(round_id, "Paragraph 66."))),
    ])
    def other_client(handler):
        handler._body()
        next(steps)()
        handler._json({"ok": True})
    monkeypatch.setitem(webview._POSTS, "/api/test-other", other_client)
    original = webview.page()
    probe = r"""
<script>
(async () => {
  const report = document.createElement('pre'); report.id = 'browser-result';
  document.body.appendChild(report);
  const check = (ok, message) => { if (!ok) throw Error(message); };
  const waitFor = async (predicate, reason) => {
    for (let i = 0; i < 1000; i++) {
      if (predicate()) return;
      await new Promise(resolve => setTimeout(resolve, 20));
    }
    throw Error('timed out: ' + reason);
  };
  const drawn = text => [...document.querySelectorAll('.card .body, .card .reply, .card')].some(e => e.textContent.includes(text));
  const anchorTop = () => [...document.querySelectorAll('#doc [data-s]')].find(e => e.textContent === 'Paragraph 40.').getBoundingClientRect().top;
  try {
    await waitFor(() => state.data, 'first load');
    // Not every engine anchors scrolling (Safari does not); without it, a thread
    // drawn above the reader moves the page unless the page puts them back.
    document.documentElement.style.overflowAnchor = document.body.style.overflowAnchor = 'none';
    let loads = 0;
    document.getElementById('refresh').addEventListener('click', () => { loads += 1; });
    const first = state.data.comments.find(c => c.body === 'first thread').id;
    const aside = document.querySelector('aside');
    const inline = document.getElementById('layout').classList.contains('inline-comments');
    const groupOf = body => [...document.querySelectorAll('.inline-thread')].find(g => g.textContent.includes(body));
    if (inline) groupOf('filler 5').open = true;
    const actor = document.getElementById('actor');
    const otherRole = [...actor.options].map(o => o.value).find(v => v !== actor.value);
    if (otherRole) actor.value = otherRole;
    commentBox(null);
    const draft = document.querySelector('#composer textarea');
    draft.value = 'my unsent comment'; draft.dispatchEvent(new Event('input'));
    draft.focus(); draft.setSelectionRange(3, 9);
    window.scrollTo(0, 0);
    [...document.querySelectorAll('#doc [data-s]')].find(e => e.textContent === 'Paragraph 40.').scrollIntoView();
    const top = anchorTop();
    aside.scrollTop = 120;
    const asideTop = aside.scrollTop;
    await api('/api/test-other', {});
    await waitFor(() => drawn('from another client') && drawn("another client's reply"), 'comment and reply drawn');
    check(state.data.comments.find(c => c.id === first).verdict === 'answered', 'verdict not refreshed');
    check(document.querySelector('#composer textarea') === draft && draft.value === 'my unsent comment', 'composer draft lost');
    check(document.activeElement === draft && draft.selectionStart === 3 && draft.selectionEnd === 9, 'composer cursor lost');
    check(Math.abs(anchorTop() - top) < 2, 'reading place moved: ' + top + ' -> ' + anchorTop());
    if (!inline) {
      check(asideTop > 0 && Math.abs(aside.scrollTop - asideTop) < 2, 'thread column scrolled: ' + asideTop + ' -> ' + aside.scrollTop);
    }
    if (inline) check(groupOf('filler 5').open, 'an inline thread the reader opened was folded');
    if (otherRole) check(actor.value === otherRole, 'the poll reset the role the reader chose');
    draft.value = ''; clearComposer();
    openReply(first);
    const typing = document.querySelector('.replybox textarea');
    typing.value = 'my unsent reply'; typing.dispatchEvent(new Event('input'));
    typing.focus(); typing.setSelectionRange(2, 6, 'backward');
    await api('/api/test-other', {});
    await waitFor(() => drawn('second reply while typing'), 'second reply drawn');
    const after = document.querySelector('.replybox textarea');
    check(after && after.value === 'my unsent reply', 'reply draft lost');
    const box = after.getBoundingClientRect();
    check(box.top >= document.querySelector('header').getBoundingClientRect().bottom && box.bottom <= innerHeight,
      'the reply box left the screen: ' + box.top + '..' + box.bottom);
    check(document.activeElement === after && after.selectionStart === 2 && after.selectionEnd === 6
      && after.selectionDirection === 'backward', 'reply selection lost');
    // Mid-composition (an IME), the box is not rebuilt; the poll catches up after.
    after.dispatchEvent(new CompositionEvent('compositionstart', {bubbles: true}));
    await api('/api/test-other', {});
    await new Promise(resolve => setTimeout(resolve, 12000));
    check(!drawn('third reply during composition') && document.querySelector('.replybox textarea') === after, 'redrawn during composition');
    after.dispatchEvent(new CompositionEvent('compositionend', {bubbles: true}));
    await waitFor(() => drawn('third reply during composition'), 'drawn after composition');
    after.value = ''; after.dispatchEvent(new Event('input'));
    document.querySelector('.replybox .acts button:last-child').click();
    // A reply draft on a thread someone else resolves stays on screen.
    const resolvedId = state.data.comments.find(c => c.body === 'filler 0').id;
    openReply(resolvedId);
    const kept = document.querySelector('.replybox textarea');
    kept.value = 'reply to a thread closing elsewhere'; kept.dispatchEvent(new Event('input')); kept.focus();
    await api('/api/test-other', {});
    await waitFor(() => state.data.comments.find(c => c.id === resolvedId).resolved, 'resolve arrives');
    const still = document.querySelector('.replybox textarea');
    check(still && still.value === 'reply to a thread closing elsewhere' && document.activeElement === still, 'reply draft hidden with its resolved thread');
    still.value = ''; still.dispatchEvent(new Event('input'));
    document.querySelector('.replybox .acts button:last-child').click();
    if (!inline) {
      // The card at the top of the thread column stays put when a card above it grows.
      const cards = [...aside.querySelectorAll('.card[data-comment]')];
      const target = cards.find(e => e.textContent.includes('filler 6'));
      aside.scrollTop += target.getBoundingClientRect().top - aside.getBoundingClientRect().top;
      const before = target.getBoundingClientRect().top;
      await api('/api/test-other', {});
      await waitFor(() => drawn('a long reply above the reader'), 'long reply drawn');
      const now = aside.querySelector('.card[data-comment="' + target.dataset.comment + '"]').getBoundingClientRect().top;
      check(Math.abs(now - before) < 2, 'thread column card moved: ' + before + ' -> ' + now);
    } else {
      // Reading a card inside an inline thread: it stays put when that card grows below the reader's point.
      const id5 = state.data.comments.find(c => c.body === 'filler 5').id;
      const card5 = () => document.querySelector('#doc .card[data-comment="' + id5 + '"]');
      check(groupOf('filler 5').open, 'test needs the filler 5 thread open');
      const header = document.querySelector('header').getBoundingClientRect().bottom;
      window.scrollBy(0, card5().getBoundingClientRect().top - header - 2);
      const before = card5().getBoundingClientRect().top;
      await api('/api/test-other', {});
      await waitFor(() => drawn("a long reply below the reader's point"), 'long reply drawn');
      const now = card5().getBoundingClientRect().top;
      check(Math.abs(now - before) < 2, 'inline card the reader was on moved: ' + before + ' -> ' + now);
      // A focused thread the reader folded stays folded.
      const id7 = state.data.comments.find(c => c.body === 'filler 7').id;
      focus(id7, 'card');
      check(groupOf('filler 7').open, 'test needs the focused thread open');
      groupOf('filler 7').open = false;
      await api('/api/test-other', {});
      await waitFor(() => drawn('step six reply'), 'step six drawn');
      check(!groupOf('filler 7').open, 'a thread the reader folded reopened');
      // The card at the top goes (resolved elsewhere) while an open thread above
      // it grows: the block the card hung off stays put.
      check(groupOf('filler 5').open, 'test needs an open thread above');
      groupOf('filler 10').open = true;
      const id10 = state.data.comments.find(c => c.body === 'filler 10').id;
      const card10 = document.querySelector('#doc .card[data-comment="' + id10 + '"]');
      const head = document.querySelector('header').getBoundingClientRect().bottom;
      window.scrollBy(0, card10.getBoundingClientRect().top - head - 2);
      const block = groupOf('filler 10').previousElementSibling;
      const blockBefore = block.getBoundingClientRect().top;
      await api('/api/test-other', {});
      await waitFor(() => state.data.comments.find(c => c.id === id10).resolved, 'resolve arrives');
      check(Math.abs(block.getBoundingClientRect().top - blockBefore) < 2,
        'the block under a vanished card moved: ' + blockBefore + ' -> ' + block.getBoundingClientRect().top);
    }
    check(loads === 0, 'the page was reloaded by hand');
    report.textContent = 'PASS';
  } catch (error) { report.textContent = 'FAIL: ' + error.message; }
})();
</script>
"""
    monkeypatch.setattr(webview, "page", lambda: original.replace(b"</body>", probe.encode() + b"</body>"))
    served = webview.WebView(store=store, path=doc, author="owner", port=0, share_scope="comment")
    served.start()
    try:
        url = served.share_url if grant == "share" else served.url
        result = subprocess.run([browser, "--no-sandbox", "--headless", "--disable-gpu", f"--window-size={width},900",
                                 f"--user-data-dir={tmp_path / 'browser'}",
                                 "--dump-dom", "--virtual-time-budget=120000", url],
                                capture_output=True, text=True, timeout=90)
        assert result.returncode == 0, result.stderr[-2500:]
        import re
        outcome = re.search(r'<pre id="browser-result">(.*?)</pre>', result.stdout, re.S)
        assert outcome and outcome.group(1) == "PASS", outcome.group(1) if outcome else result.stdout[-3000:]
    finally:
        served.shutdown()


def test_a_poll_yields_to_the_readers_own_moves(store, doc, monkeypatch, tmp_path):
    """A poll answer that drags the page back to the document the reader just
    left, hides a refused reply's box, or keeps fetching an answer it sets aside
    fails this."""
    browser = os.environ.get("SPECROUND_TEST_BROWSER") or shutil.which("chromium")
    if not browser:
        pytest.skip("set SPECROUND_TEST_BROWSER to a Chromium/headless-shell executable")
    (doc.parent / "other.md").write_text("# Other\n\nAnother document.\n", encoding="utf-8")
    round_id = store.open_round(doc, author="test")
    first = store.add_comment(round_id, author="test", body="first thread")
    ReviewStore.for_document(doc.parent / "other.md").open_round(doc.parent / "other.md", author="test")
    doc.write_text(doc.read_text(encoding="utf-8") + "\nAn unpublished line.\n", encoding="utf-8")
    def other_client(handler):
        handler._body()
        store.reply(first, author="other", body="a reply from elsewhere")
        handler._json({"ok": True})
    def other_document(handler):
        handler._body()
        other = ReviewStore.for_document(doc.parent / "other.md")
        other.add_comment(other.latest_round(doc.parent / "other.md").id, author="other", body="on the other document")
        handler._json({"ok": True})
    def anchored(handler):
        handler._body()
        store.add_comment(round_id, author="other", body="anchored elsewhere",
                          anchor=store.anchor_in_round(round_id, "30 seconds"))
        handler._json({"ok": True})
    monkeypatch.setitem(webview._POSTS, "/api/test-other", other_client)
    monkeypatch.setitem(webview._POSTS, "/api/test-other-doc", other_document)
    monkeypatch.setitem(webview._POSTS, "/api/test-anchored", anchored)
    original = webview.page()
    probe = r"""
<script>
(async () => {
  const report = document.createElement('pre'); report.id = 'browser-result';
  document.body.appendChild(report);
  const check = (ok, message) => { if (!ok) throw Error(message); };
  const waitFor = async (predicate, reason) => {
    for (let i = 0; i < 1000; i++) {
      if (predicate()) return;
      await new Promise(resolve => setTimeout(resolve, 20));
    }
    throw Error('timed out: ' + reason);
  };
  try {
    await waitFor(() => state.data, 'first load');
    const realApi = api;
    let states = 0;
    const held = [];
    let hold = false, refuse = false, failing = false;
    window.api = (path, body, params) => {
      if (failing && (path === '/api/review' || path === '/api/state')) return Promise.reject(Error('server down for the test'));
      if (path === '/api/state') states += 1;
      if (path === '/api/reply' && refuse) return (async () => {
        await realApi('/api/test-other', {});
        await load({ live: true });
        throw Error('refused for the test');
      })();
      // Asked now (for the document on screen now), answered when the test says.
      if (path === '/api/state' && hold) { const answer = realApi(path, body, params); return new Promise(resolve => held.push(() => resolve(answer))); }
      return realApi(path, body, params);
    };
    const first = state.data.comments.find(c => c.body === 'first thread').id;
    // A text selection about to become a comment is not repainted away: the
    // threads update at once, the marks once the selection is gone.
    const holder = document.querySelector('#doc [data-s]');
    const range = document.createRange(); range.selectNodeContents(holder);
    getSelection().removeAllRanges(); getSelection().addRange(range);
    const chosen = selectedSpan();
    check(chosen, 'test could not select text');
    await api('/api/test-anchored', {});
    await waitFor(() => [...document.querySelectorAll('.card')].some(e => e.textContent.includes('anchored elsewhere')), 'thread drawn under a selection');
    const after = selectedSpan();
    check(after && after.start === chosen.start && after.end === chosen.end, 'the selection was lost');
    const anchoredId = state.data.comments.find(c => c.body === 'anchored elsewhere').id;
    const marked = () => [...document.querySelectorAll('#doc mark.anch')].some(m => m.dataset.comments.split(' ').includes(anchoredId));
    check(!marked(), 'test expected the mark to wait for the selection');
    getSelection().removeAllRanges();
    await waitFor(marked, 'mark painted after the selection');
    // A selection across the base and the live text in diff mode survives too.
    setMode('diff');
    const texty = selector => [...document.querySelectorAll(selector)].find(e => e.firstChild?.nodeType === 3 && e.textContent.trim());
    const base = texty('#doc [data-s]:not([data-space="revision"])');
    const live = texty('#doc [data-space="revision"]');
    check(base && live, 'test needs both spaces in diff');
    const across = document.createRange();
    across.setStart(base.firstChild, 2); across.setEnd(live.firstChild, live.firstChild.length);
    getSelection().removeAllRanges(); getSelection().addRange(across);
    const text = getSelection().toString();
    check(text && !selectedSpan(), 'test needs a cross-space selection');
    await api('/api/test-anchored', {});
    await waitFor(() => state.data.comments.filter(c => c.body === 'anchored elsewhere').length === 2, 'drawn under a diff selection');
    check(getSelection().toString() === text, 'a diff selection across spaces was lost');
    getSelection().removeAllRanges();
    setMode('render');
    // A focused card button keeps focus when its card is rebuilt.
    const button = [...document.querySelector('.card[data-comment="' + first + '"]').querySelectorAll('button')].find(b => b.textContent.startsWith('reply'));
    const label = button.textContent;
    button.focus();
    await api('/api/test-other', {});
    await waitFor(() => state.data.comments.find(c => c.id === first).replies.length === 1, 'reply drawn');
    const now = document.activeElement;
    check(now !== button && now.tagName === 'BUTTON' && now.textContent === label
      && now.closest('.card')?.dataset.comment === first, 'the poll dropped focus from a card button');
    // A focused select in the composer (the verdict) keeps focus through a poll redraw.
    disposeBox(state.data.comments.find(c => c.id === first));
    const verdict = document.querySelector('#composer select');
    verdict.focus();
    check(document.activeElement === verdict, 'the verdict select took no focus');
    await api('/api/test-other', {});
    await waitFor(() => state.data.comments.find(c => c.id === first).replies.length === 2, 'reply drawn');
    check(document.activeElement === verdict, 'the poll took focus from the verdict select');
    clearComposer();
    const replies = () => state.data.comments.find(c => c.id === first).replies.length;
    // A disposition box keeps its verdict, reason and checkbox through a poll.
    disposeBox(state.data.comments.find(c => c.id === first));
    const choice = document.querySelector('#composer select');
    const why = document.querySelector('#composer textarea');
    const tick = document.querySelector('#composer input[type=checkbox]');
    choice.value = 'rejected'; choice.dispatchEvent(new Event('change'));
    why.value = 'my unsent reason'; why.dispatchEvent(new Event('input'));
    tick.checked = !tick.checked;
    const ticked = tick.checked;
    let count = replies();
    await api('/api/test-other', {});
    await waitFor(() => replies() === count + 1, 'drawn with a disposition box open');
    check(document.querySelector('#composer select') === choice && choice.value === 'rejected'
      && why.value === 'my unsent reason' && tick.checked === ticked, 'the disposition box lost input');
    clearComposer();
    // A message the reader has not read yet stays.
    notice('a message for the reader', 'bad');
    count = replies();
    await api('/api/test-other', {});
    await waitFor(() => replies() === count + 1, 'drawn with a notice up');
    check(document.getElementById('notice').textContent === 'a message for the reader', 'the poll wiped the notice');
    notice('');
    // A server that fails mid-poll changes nothing on the page.
    commentBox(null);
    const kept = document.querySelector('#composer textarea');
    kept.value = 'kept through an outage'; kept.dispatchEvent(new Event('input'));
    const drawnBefore = state.data;
    failing = true;
    await api('/api/test-other', {});
    await checkReview();
    await load({ live: true }).catch(() => {});
    failing = false;
    check(state.data === drawnBefore && kept.value === 'kept through an outage'
      && !document.getElementById('notice').textContent, 'a failed poll changed the page');
    kept.value = ''; clearComposer();
    await waitFor(() => replies() === count + 2, 'caught up after the outage');
    // The reader's own comment lands while an older poll answer is in flight.
    hold = true;
    const stale = load({ live: true });
    hold = false;
    check(await act({ whole: true, body: 'my own comment' }, '/api/comment'), 'own comment refused');
    held.shift()();
    await stale;
    check(state.data.comments.some(c => c.body === 'my own comment'), 'an older poll answer hid the reader\'s own comment');
    // A reply refused while a poll redrew the threads keeps its box and text.
    openReply(first);
    const box = document.querySelector('.replybox textarea');
    box.value = 'my reply'; box.dispatchEvent(new Event('input'));
    refuse = true;
    await sendReply(state.data.comments.find(c => c.id === first), box.value);
    refuse = false;
    const back = document.querySelector('.replybox textarea');
    check(back && back.value === 'my reply', 'a refused reply lost its box');
    back.value = ''; back.dispatchEvent(new Event('input'));
    document.querySelector('.replybox .acts button:last-child').click();
    // An answer the poll sets aside (here: the review's members changed) is fetched once.
    await api('/api/test-other', {});
    state.data.scope = { members: ['changed'] };
    const before = states;
    await new Promise(resolve => setTimeout(resolve, 21000));
    check(states - before === 1, 'a set-aside answer was fetched ' + (states - before) + ' times');
    state.data.scope = null;
    // The reader leaves for another document while a poll's answer is in flight.
    const start = state.doc;
    hold = true;
    const poll = load({ live: true });
    openDocument('other.md');
    hold = false;
    held.shift()();
    await poll;
    check(state.doc === 'other.md', 'the poll took the reader back to ' + state.doc);
    held.shift()();
    await waitFor(() => state.data.doc === 'other.md', 'other document drawn');
    check(start !== 'other.md', 'test started on the wrong document');
    // A count carried over from the document left behind does not mute this one.
    polledEvents = 1000000;
    openDocument(start);
    await waitFor(() => state.data.doc === start, 'back on the first document');
    openDocument('other.md');
    await waitFor(() => state.data.doc === 'other.md', 'other document again');
    await api('/api/test-other-doc', {});
    await waitFor(() => state.data.comments.some(c => c.body === 'on the other document'), 'comment on the other document drawn');
    // A poll whose answer arrives after an IME composition began does not redraw.
    const reply = state.data.comments.find(c => c.body === 'on the other document').id;
    openReply(reply);
    const typing = document.querySelector('.replybox textarea');
    hold = true;
    const late = load({ live: true });
    hold = false;
    typing.dispatchEvent(new CompositionEvent('compositionstart', {bubbles: true}));
    await api('/api/test-other-doc', {});
    held.shift()();
    check(await late === false, 'a late answer applied mid-composition');
    check(document.querySelector('.replybox textarea') === typing, 'the box was rebuilt mid-composition');
    typing.dispatchEvent(new CompositionEvent('compositionend', {bubbles: true}));
    await waitFor(() => state.data.comments.filter(c => c.body === 'on the other document').length === 2, 'drawn after composition');
    report.textContent = 'PASS';
  } catch (error) { report.textContent = 'FAIL: ' + error.message; }
})();
</script>
"""
    monkeypatch.setattr(webview, "page", lambda: original.replace(b"</body>", probe.encode() + b"</body>"))
    served = webview.WebView(store=store, path=doc, author="owner", port=0,
                             workspace=Workspace(doc.parent), doc=doc.name)
    served.start()
    try:
        result = subprocess.run([browser, "--no-sandbox", "--headless", "--disable-gpu", "--window-size=1440,900",
                                 f"--user-data-dir={tmp_path / 'browser'}",
                                 "--dump-dom", "--virtual-time-budget=120000", served.url],
                                capture_output=True, text=True, timeout=120)
        assert result.returncode == 0, result.stderr[-2500:]
        import re
        outcome = re.search(r'<pre id="browser-result">(.*?)</pre>', result.stdout, re.S)
        assert outcome and outcome.group(1) == "PASS", outcome.group(1) if outcome else result.stdout[-3000:]
    finally:
        served.shutdown()
