"""Optional real-engine smoke check; no browser library or download required."""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from specround import webview


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
