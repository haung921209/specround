"""Exception hierarchy.

The split matters for callers: a ``SchemaError`` means a single record is
malformed, an ``InvariantError`` means the record is well formed but the
history refuses it, and a ``SnapshotError`` means the object store cannot
honour a reference the ledger makes.
"""


class SpecroundError(Exception):
    """Base class for every error this package raises."""


class LedgerError(SpecroundError):
    """Something is wrong with the event log."""


class SchemaError(LedgerError):
    """A record does not match the ledger schema."""


class InvariantError(LedgerError):
    """A well formed record contradicts the history it would extend."""


class SnapshotError(SpecroundError):
    """A snapshot reference is malformed, missing, or does not match its digest."""


class AnchorError(SpecroundError):
    """An anchor is internally inconsistent or does not match the text it claims."""
