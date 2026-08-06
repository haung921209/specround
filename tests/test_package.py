"""The skeleton itself: the package imports and its errors form one hierarchy."""

import specround
from specround.errors import (
    AnchorError,
    InvariantError,
    LedgerError,
    SchemaError,
    SnapshotError,
    SpecroundError,
)


def test_version_is_exposed():
    assert specround.__version__


def test_every_error_descends_from_the_base():
    for err in (LedgerError, SchemaError, InvariantError, SnapshotError, AnchorError):
        assert issubclass(err, SpecroundError)


def test_schema_and_invariant_errors_are_ledger_errors():
    # Callers catch LedgerError to mean "this log rejected the record".
    assert issubclass(SchemaError, LedgerError)
    assert issubclass(InvariantError, LedgerError)
