"""The skeleton itself: the package imports and its errors form one hierarchy."""

import specround
from specround.errors import (
    AnchorError,
    ConfigError,
    InvariantError,
    LedgerError,
    SchemaError,
    SnapshotError,
    SpecroundError,
)


def test_version_is_exposed():
    assert specround.__version__


def test_every_error_descends_from_the_base():
    for err in (LedgerError, SchemaError, InvariantError, SnapshotError, AnchorError, ConfigError):
        assert issubclass(err, SpecroundError)


def test_a_config_error_is_not_a_ledger_error():
    # It fires before any file is touched: nothing was read, nothing rejected.
    assert not issubclass(ConfigError, LedgerError)


def test_the_public_names_are_importable_and_sorted():
    assert [name for name in specround.__all__ if not hasattr(specround, name)] == []
    assert specround.__all__ == sorted(specround.__all__)


def test_schema_and_invariant_errors_are_ledger_errors():
    # Callers catch LedgerError to mean "this log rejected the record".
    assert issubclass(SchemaError, LedgerError)
    assert issubclass(InvariantError, LedgerError)


def test_the_view_page_ships_with_the_package():
    """The page is package data, not a file beside the source tree.

    ``specround view`` reads it through the package, so a wheel that left it out
    would answer the first route a browser asks for with a 500 — and the source
    checkout would keep working, which is the worst place for that to hide.
    """
    from specround.webview import page

    assert page().startswith(b"<!doctype html>")
