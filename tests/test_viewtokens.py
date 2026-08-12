"""The view's token cache — the other half of a URL that comes back.

The port is derived so an embedder's pane survives a restart. A token minted
per start undoes exactly that, because the URL the pane holds carries ``?t=``:
same address, refused. So the token is the document's too, and these tests are
about the three things that makes true — one document one token, the secret
staying out of the ledger, and rotation being something a caller asks for.

The store is deliberately not where this lives. A ledger is an exchanged
surface (``docs/import-format.md``, and a store directory somebody hands over
whole); a token in it would ride along with the review the day it is shared.
"""

import json
import stat

import pytest

from specround.locations import canonical_path, central_root, path_key
from specround.viewtokens import (
    MINTED,
    ROTATED,
    STORED,
    TOKEN_SOURCES,
    token_file,
    token_for,
    tokens_root,
)


def test_the_same_document_gets_the_same_token_every_time(doc):
    """The whole point: a restart lands on the same address *and* the same grant."""
    first, first_source = token_for(doc)
    second, second_source = token_for(doc)
    assert first and first == second
    assert (first_source, second_source) == (MINTED, STORED)


def test_two_documents_do_not_share_a_token(doc, tmp_path):
    """One token for every document would make each one a key to the others."""
    other = tmp_path / "other.md"
    other.write_text("# other\n", encoding="utf-8")
    assert token_for(doc)[0] != token_for(other)[0]


def test_the_token_follows_the_store_key_not_the_spelling(doc, tmp_path):
    """The same normalization the port and the store key already share (§1.2).

    A symlink and the file behind it are one document, so they are one port —
    and a view on the same port with a different token would be a URL that is
    right about the address and wrong about the grant.
    """
    link = tmp_path / "link-to-spec.md"
    link.symlink_to(doc)
    assert token_for(link)[0] == token_for(doc)[0]


def test_a_directory_keeps_its_own_token_apart_from_the_documents_in_it(doc, tmp_path):
    """A workspace view is keyed on the tree, because the tree is what it serves.

    The port is derived from :attr:`~specround.webview.WebView.port_path` for
    that reason (H15), and the token has to count from the same thing or the
    tree's URL would be half stable.
    """
    assert token_for(tmp_path)[0] != token_for(doc)[0]


def test_rotating_replaces_the_stored_token(doc):
    """Opt-in, and it takes effect for the next start as well as this one."""
    stale, _ = token_for(doc)
    fresh, source = token_for(doc, rotate=True)
    assert source == ROTATED
    assert fresh != stale
    assert token_for(doc) == (fresh, STORED)


def test_the_token_file_is_readable_only_by_its_owner(doc):
    """It is a secret sitting in a data directory, so the mode is the guard."""
    token_for(doc)
    file = token_file(doc)
    assert stat.S_IMODE(file.stat().st_mode) == 0o600
    for directory in (tokens_root(), file.parent):
        assert stat.S_IMODE(directory.stat().st_mode) == 0o700


def test_the_file_names_the_document_it_belongs_to(doc):
    """A key is a hash, and a hash is a one-way trip.

    Without the breadcrumb a directory of token files could not say whose they
    are, so nobody could revoke one deliberately — the same argument that puts
    an ``origin`` beside a central store.
    """
    token, _ = token_for(doc)
    record = json.loads(token_file(doc).read_text(encoding="utf-8"))
    assert record["token"] == token
    assert record["path"] == str(canonical_path(doc))


def test_a_file_that_cannot_be_read_is_replaced_rather_than_trusted(doc):
    """Corruption is not a reason to refuse to serve, nor to serve no token."""
    token_for(doc)
    file = token_file(doc)
    file.write_text("this is not the shape\n", encoding="utf-8")
    token, source = token_for(doc)
    assert source == MINTED
    assert token_for(doc) == (token, STORED)


def test_the_token_never_lands_in_the_history_that_gets_shared(doc, store, round_id):
    """The ledger is a surface people hand each other. A secret must not ride it.

    Asserted over the bytes rather than over the layout: a later refactor that
    moved the cache under the store would keep every path assertion true and
    break this one, which is the failure that actually costs something.
    """
    token, _ = token_for(doc)
    written = [path for path in store.root.rglob("*") if path.is_file()]
    assert written, "the fixture round should have written something to read"
    for path in written:
        assert token not in path.read_bytes().decode("utf-8", "replace")


def test_the_cache_lives_beside_the_stores_and_not_inside_one(doc, store):
    """Under the application's own data directory, on the store's own key."""
    key = path_key(doc)
    assert tokens_root() == central_root() / "view-tokens"
    assert token_file(doc) == tokens_root() / key[:2] / key[2:]
    assert store.root not in token_file(doc).parents


@pytest.mark.parametrize("source", TOKEN_SOURCES)
def test_every_source_is_one_a_caller_can_be_told_about(source):
    """The vocabulary is closed, like the port's — output quotes it verbatim."""
    assert isinstance(source, str) and source
