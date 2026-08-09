"""The files a document points at — resolved, bounded, and served or refused.

A spec with a screenshot in it is a spec that can be checked against the thing
it describes. That is not decoration: a capture in a review round caught a
sentence of prose that was simply wrong about the screen it claimed to
describe, and a view that answers 404 for ``![](img/shot.png)`` throws that
away. Inlining the bytes as a ``data:`` URI is the other way to get a picture
onto the page, and it is worse — the document grows by tens of kilobytes of
base64 that the raw mode then has to show, which breaks the one mode whose
whole job is to be the text.

So the file is served. This module is the part of that with a decision in it:
**which path a reference names, and whether this process will read it.** The
HTTP is next door in :mod:`specround.webview`; everything here is a pure
function of a root, a base directory, and the string a document wrote.

**Two directories, not one.** A reference resolves against the *document's* own
directory, because that is what a relative path in a markdown file means
everywhere else and a reader who moved the file expects the picture to move with
it. What it may not leave is the *root* — the document's directory for a file
view, and the whole tree for a directory view (H15), so that ``../shared/x.png``
works between two documents of one reviewed tree and stops at its edge.

**Symlinks are followed, and then judged.** Refusing to follow them would be the
easier rule and the wrong one: a tree that keeps its captures in a linked
``img/`` is an ordinary tree, and the question a boundary asks is never "how did
you get here" but "where did you end up". So the real path is what is tested,
which is also what closes ``img -> /`` as a way out.

**Every refusal says which one it is** (:data:`REASONS`). Four different
mistakes answering with one silent 404 is a debugging session spent guessing:
the author who misspelled a filename, the author who wrote ``../../etc/passwd``,
the author who linked a ``.svg``, and the author whose PNG is 40 MB all need
different words. They share a *status* — see :class:`AssetRefused` for why that
is deliberate — and never a reason.

(The name is ``assetfiles`` and not ``assets`` because ``specround/assets/`` is
already the directory the page ships in, and one import spelling for two things
is a trap for whoever adds the third.)
"""

from __future__ import annotations

from pathlib import Path

from specround.errors import SpecroundError

__all__ = [
    "MAX_BYTES",
    "MISSING",
    "OUTSIDE",
    "REASONS",
    "SUFFIX_TYPES",
    "TOO_LARGE",
    "UNSUPPORTED",
    "AssetRefused",
    "read",
    "resolve",
]

#: What may be served, and as what. The list is images a browser draws in an
#: ``<img>`` and nothing else, because that is the whole of what a rendered
#: markdown document asks this process for.
#:
#: **SVG is not on it, and that is a decision rather than an omission.** An SVG
#: is a document: scripts in it do not run when it is drawn in an ``<img>``, but
#: they do run when the file is opened directly — and a direct visit is one
#: click from the rendered page, on *this* origin, which is the origin holding
#: the view's token and the routes that write to the ledger. The defence exists
#: (``Content-Security-Policy: script-src 'none'``, and a sandbox header beside
#: it), and it is a defence that has to be right in a place nothing else here
#: depends on getting right. v1 leaves it out and says so in the refusal, so an
#: author who tried learns why in one line instead of filing a bug. Adding it
#: later is a small change plus one test that a direct visit cannot script.
SUFFIX_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
}

#: The most this will read off disk for one picture. A screen capture in a spec
#: is a few hundred kilobytes; eight megabytes is far above anything a review
#: needs and far below anything that would hurt a loopback process to hold. The
#: cap is here so a mistake — a video renamed to ``.png``, a directory of raw
#: exports — is refused with a sentence instead of paged into memory.
MAX_BYTES = 8 * 1024 * 1024

#: Why an asset was not served. These are *codes*, kept apart from the sentence
#: that carries them, so a test and a caller can tell the four cases apart
#: without matching on prose.
MISSING = "missing"
OUTSIDE = "outside"
UNSUPPORTED = "unsupported"
TOO_LARGE = "too-large"
REASONS = (MISSING, OUTSIDE, UNSUPPORTED, TOO_LARGE)


class AssetRefused(SpecroundError):
    """This process will not serve that file, and here is which reason.

    All four reasons share one HTTP status upstream. That is not laziness: the
    only caller that can tell 403 from 404 here is a page probing this port for
    what exists on the disk behind it, and answering "not allowed" where the
    file is real and "not found" where it is not is how a boundary becomes a
    directory listing. The reason is in the body, where the author debugging
    their own document reads it and a stranger's ``<img>`` does not.
    """

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


def resolve(root: Path, base: Path, ref: str) -> Path:
    """Where ``ref`` points, if this process may read it.

    ``base`` is the directory the reference counts from — the document's own.
    ``root`` is the edge it may not cross. They are the same directory for a
    file view and differ for a workspace, which is the whole reason they are two
    arguments.

    The order of the checks is deliberate. Shape and suffix are settled before
    anything touches the filesystem, so a request naming a path this will never
    serve does not become a ``stat`` of that path — a refusal that took a
    different amount of time for a file that exists is a refusal that answers a
    question it meant to refuse.
    """
    if not ref or ref != ref.strip():
        raise AssetRefused(MISSING, "an asset reference must not be empty or padded")
    candidate = Path(ref)
    if candidate.is_absolute() or candidate.drive or ref.startswith("\\"):
        raise AssetRefused(
            OUTSIDE,
            f"{ref!r} is an absolute path — a document's images are named relative to it",
        )
    suffix = candidate.suffix.lower()
    if suffix not in SUFFIX_TYPES:
        raise AssetRefused(UNSUPPORTED, _unsupported(ref, suffix))
    # `resolve` both flattens `..` and follows every link on the way, so one
    # comparison covers the two ways out of the tree. Non-strict on purpose:
    # a path that does not exist still has a real place, and "outside" is a
    # truer answer for `../../etc/x.png` than "missing" would be.
    target = (base / candidate).resolve()
    edge = root.resolve()
    if not _within(target, edge):
        raise AssetRefused(
            OUTSIDE,
            f"{ref!r} resolves to {target}, outside {edge} — a view serves the files "
            "under the document it was started on, and follows a symlink only to where "
            "it lands",
        )
    if not target.is_file():
        raise AssetRefused(MISSING, f"no file at {ref!r} beside the document ({target})")
    return target


def read(path: Path, ref: str) -> bytes:
    """The file's bytes, or the refusal that it is too big to be a picture.

    Sized before it is read, and then read one byte past the cap: a file that
    grows between the two calls must not slip through on the strength of a
    ``stat`` that is already stale.
    """
    try:
        size = path.stat().st_size
    except OSError as exc:  # pragma: no cover - resolve() has just seen this file
        raise AssetRefused(MISSING, f"{ref!r} could not be read: {exc}") from exc
    if size > MAX_BYTES:
        raise AssetRefused(TOO_LARGE, _too_large(ref, size))
    with path.open("rb") as handle:
        data = handle.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise AssetRefused(TOO_LARGE, _too_large(ref, len(data)))
    return data


def content_type(path: Path) -> str:
    """The type to answer with — from the suffix :func:`resolve` already allowed."""
    return SUFFIX_TYPES[path.suffix.lower()]


def _within(target: Path, edge: Path) -> bool:
    return target == edge or edge in target.parents


def _unsupported(ref: str, suffix: str) -> str:
    kinds = ", ".join(sorted(SUFFIX_TYPES))
    if suffix == ".svg":
        return (
            f"{ref!r} is an SVG, which this view does not serve: an SVG opened directly "
            "is a document that can run scripts on this origin, and this origin holds the "
            f"view's token. Export it as a PNG. Served types: {kinds}"
        )
    return f"{ref!r} is not a type this view serves. Served types: {kinds}"


def _too_large(ref: str, size: int) -> str:
    return (
        f"{ref!r} is {size} bytes, over the {MAX_BYTES} a view will serve — a screen "
        "capture in a spec is a few hundred kilobytes, and something this size is "
        "usually a mistake. Shrink it, or link it instead of embedding it."
    )
