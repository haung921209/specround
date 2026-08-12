"""Where a document's view token lives between two starts.

The port is the document's, so that an embedder's pane survives a restart
(:func:`specround.webview.derived_port`). A token minted per start takes that
back: the URL the pane kept carries ``?t=``, so it returns to the right address
and is refused there — and what the reviewer typed into that pane goes with the
403. A stable address that answers 403 is not a stable URL. So the token counts
from the same key the port does, and the same document comes back whole.

**Not in the ledger.** A store is an exchanged surface — handed to somebody,
imported from, copied between machines — and a secret written into it would
travel with the review the first time one was shared. This is a cache beside
the stores instead: one file per document key, owner-only, holding the token
and the path it belongs to.

**Not the view's, either.** :class:`~specround.webview.WebView` keeps no state
of its own (G5) and takes its token as an argument. Resolving it is the CLI's
job, done here before a view is built — which is what leaves a view a thing
you can construct twice in one process without either one reaching for a file.

It is a cache and not a format. Nothing outside this repository reads these
files, they carry no schema line, and losing the directory costs one rotation:
every URL moves once and the printed line says so, which is the same thing
``--rotate-token`` does on purpose.
"""

from __future__ import annotations

import json
import os
import secrets
from pathlib import Path

from specround.locations import canonical_path, central_root, path_key

__all__ = [
    "MINTED",
    "ROTATED",
    "STORED",
    "TOKENS_DIRNAME",
    "TOKEN_SOURCES",
    "token_file",
    "token_for",
    "tokens_root",
]

#: The cache directory, a sibling of the stores under the application's data
#: directory.
TOKENS_DIRNAME = "view-tokens"

#: This document had no token, and now it has one — every start after this gets
#: it back.
MINTED = "minted"
#: The token this document already had. The ordinary case, and the one that
#: makes a URL keepable.
STORED = "stored"
#: The caller asked for a new one. The previous URL is refused from here on,
#: which is the whole reason it is a flag and not a default.
ROTATED = "rotated"

#: Closed, like the port's vocabulary: the printed line and the JSON field both
#: quote one of these, so a consumer can branch on it.
TOKEN_SOURCES = (MINTED, STORED, ROTATED)

#: 128 bits of urlsafe base64. The token is not a login — it is what keeps
#: another tab from posting to this port behind the reviewer's back — but it now
#: outlives the process, so it is worth being unguessable for longer than one.
_TOKEN_BYTES = 16

#: The same two-character fan-out the stores use, for the same reason: one flat
#: directory per document is a directory nothing lists comfortably.
_SHARD = 2

_FILE_MODE = 0o600
_DIR_MODE = 0o700


def tokens_root() -> Path:
    """The directory the cache lives in — beside the stores, never inside one."""
    return central_root() / TOKENS_DIRNAME


def token_file(path: Path) -> Path:
    """This document's token file, on the key the port is derived from.

    ``path`` is what the view *serves*: a document for a file view, and the
    root for a workspace one (H15). Both go through the store's own
    normalization, so a relative spelling, a symlink, and a different
    capitalisation on a case-insensitive filesystem all reach one token for the
    same reason they reach one port and one history.
    """
    key = path_key(path)
    return tokens_root() / key[:_SHARD] / key[_SHARD:]


def token_for(path: Path, *, rotate: bool = False) -> tuple[str, str]:
    """This document's token, and which of :data:`TOKEN_SOURCES` it came from.

    Rotating replaces what is stored rather than standing in front of it for
    one run: a flag that changed the URL only for this process would leave the
    next start handing out the grant the caller had just revoked.
    """
    file = token_file(path)
    if rotate:
        token = _mint()
        _replace(file, token, path)
        return token, ROTATED

    stored = _read(file)
    if stored:
        return stored, STORED

    token = _mint()
    try:
        _create(file, token, path)
    except FileExistsError:
        # Two things arrive here and they want opposite answers. Another start
        # may have written one between the read and the create, and then its
        # token is the one both of them have to serve — two views of one
        # document on one port must agree about the grant. Or what is there
        # cannot be read, and a token nobody can read is not a token.
        raced = _read(file)
        if raced:
            return raced, STORED
        _replace(file, token, path)
    return token, MINTED


def _mint() -> str:
    return secrets.token_urlsafe(_TOKEN_BYTES)


def _read(file: Path) -> str:
    """The stored token, or ``""`` for anything this cannot make sense of.

    Unreadable is treated as absent on purpose. The alternative — refusing to
    serve because a cache file is malformed — would make a throwaway directory
    load-bearing, and the cost of being wrong is one rotation that gets printed.
    """
    try:
        record = json.loads(file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    if not isinstance(record, dict):
        return ""
    token = record.get("token")
    return token if isinstance(token, str) and token else ""


def _payload(path: Path, token: str) -> bytes:
    """The file's contents: the token, and what it is a token *for*.

    A key is a digest and a digest is a one-way trip, so a directory of these
    could not say whose they are — and nobody could revoke one deliberately.
    The same argument puts an ``origin`` breadcrumb beside a central store.
    """
    record = {"path": str(canonical_path(path)), "token": token}
    return (json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")


def _make_dirs(directory: Path) -> None:
    """Create the cache directories owner-only, and say so rather than assume it.

    ``mkdir`` applies its mode to the last component only, and through the
    umask. Setting each level explicitly is what keeps the guarantee from
    depending on the umask of whoever started the view.
    """
    for step in (tokens_root(), directory):
        step.mkdir(parents=True, exist_ok=True)
        os.chmod(step, _DIR_MODE)


def _create(file: Path, token: str, path: Path) -> None:
    """Write a token only if there is none — so a race has one winner, not two."""
    _make_dirs(file.parent)
    handle = os.open(file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, _FILE_MODE)
    with os.fdopen(handle, "wb") as out:
        out.write(_payload(path, token))


def _replace(file: Path, token: str, path: Path) -> None:
    """Write a token over whatever is there, atomically.

    Through a temporary file and a rename, because the reader is another start
    of this same view: a truncated file it happened to read would be a token it
    then treated as absent, and rotation would come out of one restart as two.
    """
    _make_dirs(file.parent)
    temporary = file.with_name(f"{file.name}.{os.getpid()}.new")
    try:
        handle = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, _FILE_MODE)
        with os.fdopen(handle, "wb") as out:
            out.write(_payload(path, token))
        os.replace(temporary, file)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
