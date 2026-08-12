"""A temporary local web view — the browser as the GUI everyone already has (G6, G7).

Three modes over one document — rendered markdown, the raw text, and the round
diff — and **one anchor space under all of them**: whichever mode a comment is
made in, it lands on the same document anchor (SPEC §3). That is not a
convenience. It is what keeps the view and the CLI one review instead of two.

**This is not hosting.** The process is temporary, it binds loopback only, it
keeps no state of its own, and everything it records goes into the ledger the CLI
reads (G5). Closing the window loses nothing.

**The URL is printed and no browser is opened.** Embedding is the first-class
consumer: a terminal multiplexer's browser pane takes the URL and places it.
``--open`` is the opt-in for a person sitting at a shell.

**The port is the document's, not the moment's.** Because the consumer is an
embedded pane, a port drawn fresh each start makes every restart — a code
change, a view reclaimed and started again — kill the tab that was holding the
review, and makes a loop that lives in the ledger look like it lives in this
process. So the default port is derived from the document's path on the same
normalization the store keys by (:func:`derived_port`), and the same document
comes back on the same address. See :meth:`WebView.bind` for the three ways a
port gets chosen, and for the one rule under them — a port that is not the
expected one is a port whose reason is recorded, never one that quietly
wandered.

**And so is the token**, or the address would be the only stable half of a URL
that also carries ``?t=``. A grant minted per start sends the pane that
survived the restart back to the right port to be refused there, and what the
reviewer had typed into that page goes with the 403 — a comment posted through
a stale token is a refusal, not a draft. So the token persists on the same key
the port does. That is :mod:`specround.viewtokens`, and it is the *caller's* to
resolve: this class takes a token and keeps no state of its own (G5), so the
CLI hands one in. "A restart is a new grant" survives as ``--rotate-token`` —
opt-in, and printed, because a URL that moved without saying why is the one
thing this refuses.

Every route here either folds the ledger or appends to it through the same
:class:`~specround.store.ReviewStore` methods the CLI calls, and it answers in
:mod:`specround.wire`'s shapes. Nothing re-implements a rule: a view carrying its
own copy of "a reply needs an open thread" would be a second oracle, and the
format is explicit that two oracles drift (§6). So the refusals a caller sees
here are the ledger's own, translated to status codes and nothing more.

The anchor space is the round's base, for both of the modes that show one text.
It has to be: a comment's anchor is verified against the round's base (I7),
because that snapshot is the text this round is a review of. When the file on
disk has moved on, the diff mode is where that shows — and a selection on a line
only the revision has is carried into the base by the re-anchor ladder, or
refused with a reason. It is never guessed onto a nearby span.

**With no round there is no anchor space, and still a document to read.** The
two verbs the format ties to an open round stay blocked (I4) — but blocking a
comment was never a reason to withhold the text, and a view that showed nothing
until somebody opened a round answered "read only" with a blank page. So those
two modes read the live file instead, and ``reading`` in the state payload says
which of the two texts they are on. Nothing else moves: no round is opened by
looking, and the moment one exists the modes are back on its base.

**A document's own files are served beside it, behind the same token.** A spec
with a screen capture in it is reviewable against the thing it describes, and a
view that answered 404 for the picture threw that away. The boundary work — what
resolves where, and which of four refusals a request earned — is
:mod:`specround.assetfiles`; what is here is the route, the token in front of it,
and the headers that keep a served file from becoming a document. Nothing else
about the page changes: the picture is a file on disk, not review state.

**A directory is served the same way, from one process (H15).** The workspace
layer adds navigation and nothing else: every request names the document it is
about, and the answer is the same per-document projection a file view gives.
The selection is the *caller's*, never the server's — a process that remembered
which document was open would be state, and this one has none by design. That
is also what lets two browser tabs on one port read two documents at once.
"""

from __future__ import annotations

import hmac
import json
import secrets
import threading
from dataclasses import dataclass
from functools import lru_cache
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.parse import parse_qs, urlsplit

from specround import __version__, assetfiles, markdown
from specround.assetfiles import AssetRefused
from specround.diffs import changed_span, diff, unified_patch
from specround.errors import AnchorError, InvariantError, SpecroundError
from specround.events import ACTORS, HUMAN, SUPERSEDE, VERDICTS
from specround.fold import Round, State
from specround.locations import path_key
from specround.reanchor import POSITION
from specround.store import ReviewStore
from specround.wire import comment_json, comments_on, round_json, rounds_on
from specround.workspace import Workspace

__all__ = [
    "BASE",
    "DERIVED",
    "EPHEMERAL",
    "FALLBACK",
    "PINNED",
    "PORT_CEILING",
    "PORT_FLOOR",
    "PORT_SOURCES",
    "PortTaken",
    "REVISION",
    "Refusal",
    "WebView",
    "VIEW_SCHEMA",
    "derived_port",
]

#: The payload's own version, for the reason the ledger lines carry one: a
#: consumer should be able to tell that the shape it parses is the shape it was
#: written against.
VIEW_SCHEMA = "specround.view/v0"

#: Which text a selection's offsets count in — and, as ``reading`` in
#: :meth:`WebView.state_payload`, which text the render and raw modes are
#: showing. ``base`` is the snapshot the round froze, and is the answer whenever
#: there is a round: a comment made on this round anchors in the text the round
#: is a review of (I7). ``revision`` is the document as it is now — the diff's
#: added lines, and what the other two modes read when there is no round at all
#: and therefore no anchor space to be in.
BASE = "base"
REVISION = "revision"
SPACES = (BASE, REVISION)

DEFAULT_HOST = "127.0.0.1"

#: The dynamic/private range (RFC 6335 §8.1.2) — the ports no service registers
#: and none of which need privilege. It is also the range an operating system
#: draws outbound source ports from, so a derived port is occasionally in use by
#: something with no opinion about this tool at all. That is not a flaw to design
#: around; it is the case :meth:`WebView.bind` falls back from, out loud. The
#: alternative — a range nothing else touches — does not exist on a machine this
#: process does not own.
PORT_FLOOR = 49152
PORT_CEILING = 65535
PORT_SPAN = PORT_CEILING - PORT_FLOOR + 1

#: Where :attr:`WebView.port_source` says the bound port came from. The
#: distinction a caller acts on is "will this URL come back": ``derived`` yes,
#: ``pinned`` yes, ``ephemeral`` no by request, ``fallback`` no and here is why.
DERIVED = "derived"
PINNED = "pinned"
EPHEMERAL = "ephemeral"
FALLBACK = "fallback"
PORT_SOURCES = (DERIVED, PINNED, EPHEMERAL, FALLBACK)

#: How often the serving loop checks whether it has been told to stop.
#: :meth:`~socketserver.BaseServer.shutdown` waits for one of these, so the
#: default half-second is half a second of a view that has been closed and has
#: not let go. Twenty wakeups a second of an otherwise sleeping thread is not a
#: cost worth keeping that for.
POLL_INTERVAL = 0.05
#: A body larger than this is not a review comment.
MAX_BODY = 8 * 1024 * 1024
_ASSETS = "assets"
_PAGE = "app.html"


def derived_port(path: Path) -> int:
    """The port a document — or a directory — always comes back on.

    It is the store's own key, folded into the dynamic range: same
    normalization, same digest, so the two answers can never disagree about
    which document this is. A relative spelling, a symlink, and (where the
    filesystem says they are one file) a different capitalisation all land on one
    port for the same reason they land on one history
    (``docs/ledger-format.md`` §1.2).

    Sharing the store's digest is deliberate rather than convenient. The
    alternative is a second normalization to keep in step with the first, and a
    port that drifts from the store key is a view addressed as one document while
    reading another's ledger.
    """
    return PORT_FLOOR + int(path_key(path), 16) % PORT_SPAN


class PortTaken(SpecroundError):
    """A port the caller named is in use — so nothing is served, and nothing moves.

    Only a *named* port raises this. The derived one falls back instead, because
    nobody typed it: the caller asked for "this document's view", and a free port
    still answers that as long as the move is said out loud. ``--port N`` is a
    different request, and quietly serving N+something would answer past it.
    """

    def __init__(self, host: str, port: int, reason: str) -> None:
        super().__init__(
            f"{host}:{port} is already in use ({reason}) — name a free port, or drop "
            "--port to take the one derived from the document's path"
        )
        self.host = host
        self.port = port
        self.reason = reason


class Refusal(SpecroundError):
    """An answer the view gives instead of doing what was asked.

    ``kind`` uses the CLI's vocabulary — ``usage`` for a request that cannot be
    carried out as sent, ``state`` for one the recorded history refuses — so a
    caller reading both surfaces learns one set of words. The status code is the
    same distinction for anything speaking HTTP.

    ``reason`` is the finer axis, and empty for everything that does not need
    one. It exists because the asset route answers four different mistakes with
    one status on purpose (:mod:`specround.assetfiles`), and a caller telling
    them apart by matching on the sentence would be a caller that breaks when
    the sentence is reworded.
    """

    def __init__(self, status: HTTPStatus, kind: str, message: str, reason: str = "") -> None:
        super().__init__(message)
        self.status = status
        self.kind = kind
        self.reason = reason


def _usage(message: str) -> Refusal:
    return Refusal(HTTPStatus.BAD_REQUEST, "usage", message)


def _state(message: str) -> Refusal:
    return Refusal(HTTPStatus.CONFLICT, "state", message)


def _strerror(exc: OSError) -> str:
    """What the operating system said, without the errno furniture.

    "Address already in use" is a sentence a person can act on; "[Errno 48]
    Address already in use" is the same sentence wearing a number that means
    nothing to the reader of a URL.
    """
    return exc.strerror or str(exc)


@lru_cache(maxsize=1)
def page() -> bytes:
    """The single static page. One file, no build step, no CDN."""
    from importlib.resources import files

    return (files("specround") / _ASSETS / _PAGE).read_bytes()


@dataclass
class WebView:
    """One document, served to one browser for as long as the process lives."""

    store: ReviewStore
    path: Path
    author: str
    actor: str = HUMAN
    #: The round to write to. ``None`` means "the one open round", resolved per
    #: request rather than at startup: a CLI in another terminal can close a
    #: round while this view is open, and the view should notice.
    round_hint: str | None = None
    host: str = DEFAULT_HOST
    #: Which port to take, and after :meth:`bind` the one that was taken.
    #: ``None`` — the default — means the one derived from the document's path,
    #: so the same document keeps its address across restarts. ``0`` asks for
    #: whatever is free. Anything else is a request that is met or refused.
    port: int | None = None
    #: The grant the URL carries. Empty means "mint one for this process", which
    #: keeps the class stateless; a caller that wants the *same* URL next time
    #: resolves it first (:mod:`specround.viewtokens`) and passes it here.
    token: str = ""
    #: The tree this view navigates, when it was started on a directory (H15).
    #: ``None`` is the file view, unchanged in every respect.
    workspace: Workspace | None = None
    #: This view's document as the *workspace* names it — a path relative to the
    #: root. Empty without a workspace. Kept apart from :attr:`key`, which is
    #: how the *store* names the same document; the two spaces differ (a central
    #: store keys ``docs/sub/a.md`` as ``a.md``) and mixing them would read one
    #: document's history under another's name.
    doc: str = ""

    def __post_init__(self) -> None:
        self.key = self.store.doc_key(self.path)
        self.token = self.token or secrets.token_urlsafe(16)
        #: One of :data:`PORT_SOURCES`, once :meth:`bind` has run.
        self.port_source = ""
        #: The derived port that was not free, when that is why the port moved.
        #: ``None`` every other time, including a successful derivation.
        self.wanted_port: int | None = None
        #: What the operating system said about it, verbatim.
        self.port_reason = ""
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        #: Whether a serving loop is running and can acknowledge a shutdown.
        self._serving = False
        #: Views onto the workspace's other documents, made when first asked
        #: for. They are projections, not sessions: each one re-folds its own
        #: ledger on every request, so keeping them can never serve stale state.
        self._bound: dict[str, "WebView"] = {}

    # -- lifecycle -------------------------------------------------------

    @property
    def port_path(self) -> Path:
        """What the derived port counts from — the tree, or else the document.

        A workspace view is one server over a directory (H15), and the directory
        is what the caller named. Deriving from the document it happens to open
        on would move the whole tree's port the day somebody adds a file that
        sorts before it, which is a port nobody could rely on.
        """
        return self.workspace.root if self.workspace is not None else self.path

    def bind(self) -> "WebView":
        """Take the port, so the URL is knowable before anything is served.

        Which port is three requests, and they differ by who is asking:

        ``port=N``
            The caller named it. It is taken, or :class:`PortTaken` — moving a
            named port answers a question nobody asked.
        ``port=0``
            The caller asked for whatever is free, and gets it. The URL will be
            a different one next time, which is what was requested.
        ``port=None`` (the default)
            Derived from the path, so a restart lands on the same address and an
            embedder's pane survives it. When something else already holds it, a
            free port stands in and :attr:`port_source` says ``fallback`` —
            recording *why* the URL moved is the whole difference between a
            fallback and a port that wanders.

        A stable port is only half of a stable URL — the other half is the
        token in :attr:`url`, and this class does not decide it. A view given
        no token mints one, which is right for a library that keeps no state:
        the persistence that makes a restart land on the *same* URL belongs to
        the caller, in :mod:`specround.viewtokens`. Handing a view the token
        the port already agrees about is what makes an embedder's pane survive
        a restart in full rather than in address only.
        """
        if self._server is not None:
            return self
        if self.port is None:
            wanted = derived_port(self.port_path)
            try:
                self._take(wanted)
            except OSError as exc:
                # Not a failure — the caller asked for this document's view, and
                # a free port still serves it. What would be a failure is doing
                # this silently, so the reason is kept for whoever prints it.
                self.wanted_port, self.port_reason = wanted, _strerror(exc)
                self._take(0)
                self.port_source = FALLBACK
            else:
                self.port_source = DERIVED
        elif self.port == 0:
            self._take(0)
            self.port_source = EPHEMERAL
        else:
            try:
                self._take(self.port)
            except OSError as exc:
                raise PortTaken(self.host, self.port, _strerror(exc)) from exc
            self.port_source = PINNED
        assert self._server is not None
        self.port = self._server.server_address[1]
        return self

    def _take(self, port: int) -> None:
        """Bind one port, leaving nothing behind if it cannot be had.

        ``TCPServer`` closes its own socket when the bind raises, so a refused
        port costs no descriptor and the next attempt starts clean.
        """
        server = _Server((self.host, port), _Handler)
        server.view = self
        self._server = server

    @property
    def url(self) -> str:
        """The address to hand to a browser — token included.

        The token is not a login. It is what keeps any page in the browser from
        posting to this port behind the reviewer's back: a local server that
        writes to a ledger is reachable by every tab, and an unguessable path is
        the cheap half of the answer. The other half is the ``Origin`` check in
        the handler.

        It now outlives the process it was first served by, which is what makes
        the URL keepable — and is also why revoking one has to be something a
        caller can ask for rather than something a restart does for free.
        """
        return f"http://{self.host}:{self.port}/?t={self.token}"

    def serve_forever(self) -> None:  # pragma: no cover - the CLI's blocking path
        self.bind()
        assert self._server is not None
        self._serving = True
        try:
            self._server.serve_forever(poll_interval=POLL_INTERVAL)
        finally:
            self.shutdown()

    def start(self) -> threading.Thread:
        """Serve in a background thread, leaving the caller in control.

        :meth:`serve_forever` is what the CLI wants, having nothing else to do.
        This is for a caller that does — and for the tests, which have to speak
        to a real socket: the routes are the contract a browser meets, and
        checking the methods behind them would leave the token, the status codes,
        and the JSON envelope untested.
        """
        self.bind()
        assert self._server is not None
        self._serving = True
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            kwargs={"poll_interval": POLL_INTERVAL},
            name="specround-view",
            daemon=True,
        )
        self._thread.start()
        return self._thread

    def shutdown(self) -> None:
        """Stop serving and release the port. Safe from either thread, and twice.

        ``shutdown`` on the socket server waits for the serving loop to
        acknowledge, and that acknowledgement only ever comes from the loop —
        so asking a server that was bound but never served would wait for a
        thread that does not exist. A view that binds and then fails on its way
        to serving is a real case (``--open`` raising, an interrupt in between),
        and there the port is released by closing alone.
        """
        server, thread, serving = self._server, self._thread, self._serving
        self._server, self._thread, self._serving = None, None, False
        if server is not None:
            if serving:
                server.shutdown()
            server.server_close()
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=5)

    def authorised(self, token: str | None, origin: str | None) -> bool:
        """Both halves: the caller knows the token and is not another origin."""
        if not token or not hmac.compare_digest(token, self.token):
            return False
        # A same-origin fetch may or may not send Origin depending on the
        # browser; a cross-origin one always does. Absent is therefore fine and
        # a mismatch never is.
        return origin in (None, "", f"http://{self.host}:{self.port}")

    # -- navigation ------------------------------------------------------

    def select(self, key: str | None) -> "WebView":
        """The view for the document a request named (H15).

        Every route goes through here, which is what keeps the workspace layer
        navigation only: what comes back is an ordinary :class:`WebView` over one
        document, and everything after this point is the code a file view runs.

        A file view refuses a name that is not its own rather than ignoring it.
        Serving this document under that name is the quiet wrong answer — the
        caller would read one review believing it was another's.

        The round hint does not travel. ``--round`` names a round, a round
        belongs to one document, and carrying the hint onto a sibling would
        turn every other document in the tree into "no round X on Y".
        """
        if self.workspace is None:
            if key and key != self.key:
                raise _usage(
                    f"this view serves {self.key!r}, not {key!r} — start it on the directory "
                    "with 'specround view <dir>' to move between documents"
                )
            return self
        if not key or key == self.doc:
            return self
        bound = self._bound.get(key)
        if bound is None:
            try:
                path = self.workspace.resolve(key)
            except SpecroundError as exc:
                raise _usage(str(exc)) from exc
            bound = WebView(
                store=self.workspace.store_for(path),
                path=path,
                author=self.author,
                actor=self.actor,
                round_hint=None,
                host=self.host,
                port=self.port,
                token=self.token,
                workspace=self.workspace,
                doc=key,
            )
            self._bound[key] = bound
        return bound

    def workspace_payload(self) -> dict[str, Any] | None:
        """The navigation bar's whole answer, or ``None`` for a file view.

        It rides in :meth:`state_payload` rather than on a route of its own.
        A route no page calls is an undocumented surface — the same rule that
        keeps a dead button from surviving here — and a listing fetched apart
        from the document it sits beside is a listing that can disagree with it.
        """
        if self.workspace is None:
            return None
        return {**self.workspace.list().to_json(), "selected": self.doc}

    # -- assets ----------------------------------------------------------

    @property
    def asset_base(self) -> Path:
        """What a reference in this document counts from — its own directory.

        Always the document's, workspace or not. A relative path in a markdown
        file means "beside this file" everywhere else that reads one, and a view
        that resolved from the tree root instead would break every document that
        keeps its captures next to itself the moment somebody served the folder
        rather than the file.
        """
        return self.path.parent

    @property
    def asset_root(self) -> Path:
        """The edge a reference may not cross — the tree, or else the directory.

        Wider than :attr:`asset_base` for a workspace, and the same directory for
        a file view. The reviewed tree is one thing being reviewed (H15), so a
        document in it may point at ``../shared/img/x.png``; a view started on a
        single file was given one directory and has no ground to serve out of a
        sibling nobody named.
        """
        return self.workspace.root if self.workspace is not None else self.path.parent

    def asset(self, ref: str) -> tuple[bytes, str]:
        """The bytes of a file this document points at, and what it is.

        Behind the same token as everything else — an image request is a request
        to read a file off this machine, and the fact that a browser makes it
        while drawing a page does not make it a smaller one.
        """
        try:
            target = assetfiles.resolve(self.asset_root, self.asset_base, ref)
            return assetfiles.read(target, ref), assetfiles.content_type(target)
        except AssetRefused as exc:
            raise Refusal(HTTPStatus.NOT_FOUND, "usage", str(exc), exc.reason) from exc

    # -- reading ---------------------------------------------------------

    def live_text(self) -> str | None:
        """The document as it is on disk, or ``None`` if it is not there.

        A document can be renamed or deleted while its history stays where it
        was, and the view still has something to show — the base is in the store.
        """
        if not self.path.is_file():
            return None
        try:
            return self.path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return None

    def resolve_round(self, state: State) -> tuple[Round | None, str | None]:
        """The round this view acts on, and why it cannot write if it cannot.

        Deliberately more permissive than the CLI's ``_live_round``: with no
        open round this returns the latest closed one instead of refusing, so
        the history of a finished review is still readable. Only the two verbs
        the format ties to an open round (I4) are blocked, and the reason says
        which command opens one.
        """
        rounds = rounds_on(state, self.key)
        if self.round_hint:
            named = state.rounds.get(self.round_hint)
            if named is None or named.doc != self.key:
                return None, f"no round {self.round_hint!r} on {self.key}"
            if not named.open:
                return named, (
                    f"round {named.id} is closed — reading only. Open a new one with "
                    "'specround round open' to add comments"
                )
            return named, None
        live = [r for r in rounds if r.open]
        if len(live) == 1:
            return live[0], None
        if len(live) > 1:
            names = ", ".join(sorted(r.id for r in live))
            return None, (
                f"{len(live)} rounds are open ({names}) — restart the view with "
                "--round to say which one this view writes to"
            )
        if rounds:
            return rounds[-1], (
                f"no open round on {self.key} — reading only. Open one with "
                "'specround round open'"
            )
        return None, (
            f"no rounds on {self.key} yet — read only. Comments need a round: "
            "'specround round open'"
        )

    def _writable(self, state: State) -> Round:
        round_, blocked = self.resolve_round(state)
        if round_ is None or not round_.open:
            assert blocked is not None
            raise _state(blocked)
        return round_

    def state_payload(self) -> dict[str, Any]:
        """Everything the page draws, in one answer.

        One request rather than several, because the three modes and the thread
        list are one consistent reading of one fold. Splitting them would let the
        page paint comments against a base it has not loaded yet, and the
        documents this reviews are small enough that the saving would be
        theoretical.

        ``workspace`` carries the navigation bar for the same reason, and it is
        ``None`` for a file view. One answer means a comment posted here repaints
        the bar's badges and the thread list together — two requests would let
        the bar say "nothing undisposed" about a document the panel beside it is
        showing an open thread on.
        """
        state = self.store.fold()
        round_, blocked = self.resolve_round(state)
        live = self.live_text()
        base = self.store.base_text(round_.id) if round_ is not None else None
        reading, shown = self._reading(base, live)
        comments = comments_on(state, self.key)
        payload: dict[str, Any] = {
            "schema": VIEW_SCHEMA,
            "version": __version__,
            "doc": self.key,
            "path": str(self.path),
            "store": str(self.store.root),
            "workspace": self.workspace_payload(),
            "author": self.author,
            "actor": self.actor,
            "actors": list(ACTORS),
            "verdicts": list(VERDICTS),
            "round": round_json(state, round_) if round_ is not None else None,
            "rounds": [round_json(state, r) for r in rounds_on(state, self.key)],
            "comments": [comment_json(c) for c in comments],
            "commentable": round_ is not None and round_.open,
            "blocked": blocked,
            "reading": reading,
            "base": base,
            "live": live,
            "render": markdown.render(shown) if shown is not None else "",
            "diff": self._diff_payload(base, live),
            "counts": {
                "comments": len(comments),
                "undisposed": sum(1 for c in comments if c.undisposed),
                "orphans": sum(1 for c in comments if c.orphaned),
                "misplaced": sum(1 for c in comments if c.misplaced),
                "resolved": sum(1 for c in comments if c.resolved),
                "events": state.count,
            },
        }
        return payload

    def _reading(self, base: str | None, live: str | None) -> tuple[str | None, str | None]:
        """Which text the render and raw modes show, and which space that is.

        With a round it is the base that round froze. It has to be: those two
        modes are the ones a comment is made in, and a comment on this round is
        verified against its base (I7). Following the file there would move the
        ground under a review that has already started.

        With no round there is no anchor space at all — nothing has been frozen,
        so nothing can be anchored, and :attr:`commentable` already says so. What
        was missing was a text: both modes read the base, and a document nobody
        had opened a round on had none, so it drew as a blank page. Reading is
        not the thing a round grants (I4), so the live file stands in, read only.

        ``None`` for both is the one case with nothing to show — no round froze a
        text and the file is not there to read either. Then :attr:`blocked` is
        the whole answer.
        """
        if base is not None:
            return BASE, base
        if live is not None:
            return REVISION, live
        return None, None

    def _diff_payload(self, base: str | None, live: str | None) -> dict[str, Any]:
        if base is None or live is None:
            # No base means no round; no live means the file is gone. Either way
            # there are two texts to compare and only one of them exists.
            return {
                "rows": [],
                "identical": False,
                "available": False,
                "added": 0,
                "removed": 0,
                "only_terminator": False,
            }
        computed = diff(base, live)
        return {
            "rows": [
                {
                    "op": row.op,
                    "text": row.text,
                    "base_line": row.base_line,
                    "live_line": row.live_line,
                    "base_start": row.base_start,
                    "live_start": row.live_start,
                }
                for row in computed.rows
            ],
            "identical": computed.identical,
            "available": True,
            "added": computed.added,
            "removed": computed.removed,
            "only_terminator": computed.only_terminator,
        }

    # -- writing ---------------------------------------------------------

    def add_comment(self, body: Mapping[str, Any]) -> dict[str, Any]:
        """Record a comment made in any of the three modes (G6)."""
        state = self.store.fold()
        round_ = self._writable(state)
        text = _text(body, "body")
        anchor = None
        carried: dict[str, Any] | None = None
        ext: dict[str, Any] | None = None
        if not body.get("whole"):
            space = str(body.get("space", BASE))
            if space not in SPACES:
                raise _usage(f"unknown space {space!r}: use {' or '.join(SPACES)}")
            start, end = _span(body)
            if space == BASE:
                anchor = self._cut(round_, start, end)
            else:
                anchor, carried = self._carry(round_, start, end)
                if carried["strategy"] != POSITION:
                    # Where a comment came from is provenance the closed field
                    # set has no room for, and it is exactly the kind of thing
                    # the format reserves ``ext`` for (§2). Losing it would leave
                    # a comment that reached the base through the fuzzy rung
                    # indistinguishable from one selected on the base itself —
                    # and §4 is explicit that the first is worth a look.
                    ext = {"view": {"space": space, **carried}}
        comment_id = self.store.add_comment(
            round_.id, author=_author(body, self.author), body=text, anchor=anchor, ext=ext
        )
        return {
            "comment": comment_json(self.store.fold().comments[comment_id]),
            "carried": carried,
        }

    def add_suggestion(self, body: Mapping[str, Any]) -> dict[str, Any]:
        """Take an edit of the raw text as a suggestion diff (G8).

        The edit is against the round's base, which is the text the raw mode
        shows and the only text a comment on this round may anchor in. The patch
        is stored, never applied: applying one is a disposition somebody records,
        and whether a patch may still be applied once its anchor has moved is
        H8 — open, and not decided by a view.
        """
        state = self.store.fold()
        round_ = self._writable(state)
        proposed = body.get("text")
        if not isinstance(proposed, str):
            raise _usage("a suggestion needs the edited text in 'text'")
        base = self.store.base_text(round_.id)
        span = changed_span(base, proposed)
        if span is None:
            raise _usage("the text is unchanged — there is nothing to propose")
        suggestion_id = self.store.add_suggestion(
            round_.id,
            author=_author(body, self.author),
            patch=unified_patch(base, proposed, label=self.key),
            body=str(body.get("body", "")).strip(),
            anchor=self._cut(round_, *span),
        )
        return {"comment": comment_json(self.store.fold().comments[suggestion_id])}

    def reply(self, body: Mapping[str, Any]) -> dict[str, Any]:
        target = _text(body, "target")
        answered = self.store.reply(
            target, author=_author(body, self.author), body=_text(body, "body")
        )
        del answered
        return {"comment": comment_json(self.store.fold().comments[target])}

    def dispose(self, body: Mapping[str, Any]) -> dict[str, Any]:
        """Record a verdict, overturning a settled one when the caller says so.

        ``supersede`` is carried here rather than left to the CLI because the
        view is a write surface of equal standing: a reviewer who settled a
        comment on this page and wants it back would otherwise have to leave the
        page to undo it, and a gate you can only pass somewhere else is a gate
        people route around.
        """
        target = _text(body, "target")
        verdict = _text(body, "verdict")
        if verdict not in VERDICTS:
            raise _usage(f"unknown verdict {verdict!r}: use {', '.join(VERDICTS)}")
        self.store.dispose(
            target,
            author=_author(body, self.author),
            verdict=verdict,
            reason=_text(body, "reason"),
            supersede=bool(body.get(SUPERSEDE, False)),
        )
        return {"comment": comment_json(self.store.fold().comments[target])}

    def thread(self, body: Mapping[str, Any]) -> dict[str, Any]:
        """Close or re-open a conversation (G11) — one call with a sign.

        Re-stating the state a thread is already in records nothing and is not an
        error, exactly as the ledger has it (I10): a retry has to be safe for the
        agents this is for, and a button somebody clicked twice is a retry.
        """
        target = _text(body, "target")
        actor = str(body.get("actor") or self.actor)
        if actor not in ACTORS:
            raise _usage(f"unknown actor {actor!r}: use {' or '.join(ACTORS)}")
        author = _author(body, self.author)
        if body.get("resolved"):
            event = self.store.resolve(
                target, author=author, actor=actor, note=str(body.get("note", "")) or None
            )
        else:
            event = self.store.reopen(
                target, author=author, actor=actor, reason=_text(body, "reason")
            )
        return {
            "comment": comment_json(self.store.fold().comments[target]),
            "event": event,
            "changed": event is not None,
        }

    # -- anchoring -------------------------------------------------------

    def _cut(self, round_: Round, start: int, end: int) -> Any:
        try:
            return self.store.anchor_span_in_round(round_.id, start, end)
        except AnchorError as exc:
            raise _usage(f"that span is not in the base round {round_.id} froze: {exc}") from exc

    def _carry(self, round_: Round, start: int, end: int) -> tuple[Any, dict[str, Any]]:
        """Carry a selection made on the revision into the round's base."""
        live = self.live_text()
        if live is None:
            raise _usage(f"{self.path} is not readable — nothing to select in the revision")
        try:
            rebind = self.store.carry_span_into_round(round_.id, live, start, end)
        except AnchorError as exc:
            raise _usage(f"that span is not in {self.path}: {exc}") from exc
        if rebind.orphaned:
            raise _state(
                f"that text has no place in the base round {round_.id} froze "
                f"({rebind.reason}) — comment on the whole document instead, or open a "
                "new round on the revised document to review it as it is now"
            )
        return rebind.anchor, {"strategy": rebind.strategy, "ambiguous": rebind.ambiguous}


def _author(body: Mapping[str, Any], fallback: str) -> str:
    """Who is speaking — the viewer's own name when the page says so (G4).

    The page carries a name because a view is a place two participants meet:
    the person who started it and an agent posting through the same routes. The
    launcher's ``--author`` is the default, not a lock.
    """
    given = str(body.get("author", "")).strip()
    return given or fallback


def _text(body: Mapping[str, Any], field: str) -> str:
    value = body.get(field)
    if not isinstance(value, str) or not value.strip():
        raise _usage(f"{field!r} is required and must not be empty")
    return value.strip()


def _span(body: Mapping[str, Any]) -> tuple[int, int]:
    values = []
    for field in ("start", "end"):
        value = body.get(field)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise _usage(f"{field!r} must be a non-negative integer offset")
        values.append(value)
    start, end = values
    if end < start:
        raise _usage("'end' must not precede 'start'")
    return start, end


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    view: WebView


class _Handler(BaseHTTPRequestHandler):
    """The routes. Each one is a call into :class:`WebView` and a status code."""

    server_version = f"specround/{__version__}"
    protocol_version = "HTTP/1.1"

    @property
    def view(self) -> WebView:
        return self.server.view  # type: ignore[attr-defined]

    def log_message(self, format: str, *args: Any) -> None:
        """Quiet. The URL on stdout is the interface, not an access log.

        Left in place rather than removed so the reason is here: this process
        shares stderr with the CLI's error channel, and a request log there would
        interleave with the one thing a caller is meant to read from it.
        """

    def do_GET(self) -> None:
        self._dispatch(_GETS)

    def do_POST(self) -> None:
        self._dispatch(_POSTS)

    # -- plumbing --------------------------------------------------------

    def _dispatch(self, routes: Mapping[str, Callable[["_Handler"], None]]) -> None:
        split = urlsplit(self.path)
        query = parse_qs(split.query)
        #: Which document this request is about, for the routes that read it off
        #: the query rather than a body. Per request, like everything else on a
        #: handler instance — the *server* holds no selection (H15).
        self.named_doc = (query.get("doc") or [None])[0]
        #: The rest of the query, for the one route that reads more of it. Kept
        #: on the handler rather than passed down, because a handler instance is
        #: one request and the server is the thing that must stay stateless.
        self.query = query
        token = (query.get("t") or [None])[0] or self.headers.get("X-Specround-Token")
        if not self.view.authorised(token, self.headers.get("Origin")):
            # Deliberately the same answer for a wrong token and a wrong origin:
            # this is not an account, and telling a caller which half it failed
            # is telling it what to fix.
            self._send(
                HTTPStatus.FORBIDDEN,
                b"specround: not this view's token\n",
                "text/plain; charset=utf-8",
            )
            return
        route = routes.get(split.path)
        if route is None:
            self._send(HTTPStatus.NOT_FOUND, b"specround: no such route\n", "text/plain; charset=utf-8")
            return
        try:
            route(self)
        except Refusal as exc:
            self._error(exc.status, exc.kind, str(exc), exc.reason)
        except InvariantError as exc:
            # The ledger refused. It is the same class of answer the CLI exits 3
            # for, and the message is the ledger's own — the view does not
            # paraphrase a rule it did not enforce.
            self._error(HTTPStatus.CONFLICT, "state", str(exc))
        except (SpecroundError, OSError) as exc:
            self._error(HTTPStatus.INTERNAL_SERVER_ERROR, "error", str(exc))

    def _body(self) -> dict[str, Any]:
        length = self.headers.get("Content-Length")
        try:
            size = int(length or 0)
        except ValueError as exc:
            raise _usage("Content-Length is not a number") from exc
        if size > MAX_BODY:
            raise _usage(f"body is larger than {MAX_BODY} bytes")
        raw = self.rfile.read(size) if size else b"{}"
        try:
            parsed = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise _usage(f"the request body is not JSON: {exc}") from exc
        if not isinstance(parsed, dict):
            raise _usage("the request body must be a JSON object")
        return parsed

    def _send(
        self,
        status: HTTPStatus,
        payload: bytes,
        content_type: str,
        *,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        # A temporary view of a file that changes under it caches nothing.
        self.send_header("Cache-Control", "no-store")
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(payload)

    def _json(self, payload: Mapping[str, Any], status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        self._send(status, body, "application/json; charset=utf-8")

    def _error(self, status: HTTPStatus, kind: str, message: str, reason: str = "") -> None:
        error: dict[str, Any] = {"kind": kind, "status": int(status), "message": message}
        if reason:
            error["reason"] = reason
        self._json({"schema": VIEW_SCHEMA, "error": error}, status)

    # -- routes ----------------------------------------------------------

    def _page(self) -> None:
        self._send(HTTPStatus.OK, page(), "text/html; charset=utf-8")

    def _state(self) -> None:
        self._json(self.view.select(self.named_doc).state_payload())

    def _asset(self) -> None:
        """A file the rendered document points at, served beside it.

        The headers are the second half of the extension whitelist. ``nosniff``
        is what keeps a file *named* ``.png`` from being read as anything else
        by a browser that thinks it knows better, and the content policy is a
        floor under everything the whitelist is meant to have already excluded —
        if a type that can execute ever gets onto the list by mistake, this is
        the line that still says no.
        """
        ref = (self.query.get("path") or [""])[0]
        payload, kind = self.view.select(self.named_doc).asset(ref)
        self._send(
            HTTPStatus.OK,
            payload,
            kind,
            headers={
                "X-Content-Type-Options": "nosniff",
                "Content-Security-Policy": "default-src 'none'; sandbox",
                "Content-Disposition": "inline",
            },
        )

    def _write(self, method: Callable[["WebView", Mapping[str, Any]], dict[str, Any]]) -> None:
        """Run a writing verb on the document the body named.

        The body is read before the view is chosen, because ``doc`` is in it.
        The method arrives unbound for that reason — binding it to
        ``self.view`` at route-table time would nail every write to the document
        the process started on.
        """
        body = self._body()
        named = body.get("doc")
        if named is not None and not isinstance(named, str):
            raise _usage("'doc' names a document in this workspace and must be a string")
        payload = method(self.view.select(named or self.named_doc), body)
        self._json({"schema": VIEW_SCHEMA, **payload})


_GETS: dict[str, Callable[[_Handler], None]] = {
    "/": _Handler._page,
    "/api/state": _Handler._state,
    "/api/asset": _Handler._asset,
}

_POSTS: dict[str, Callable[[_Handler], None]] = {
    "/api/comment": lambda h: h._write(WebView.add_comment),
    "/api/suggestion": lambda h: h._write(WebView.add_suggestion),
    "/api/reply": lambda h: h._write(WebView.reply),
    "/api/dispose": lambda h: h._write(WebView.dispose),
    "/api/thread": lambda h: h._write(WebView.thread),
}
