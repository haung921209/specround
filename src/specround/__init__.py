"""specround — spec review rounds for humans and AI agents.

The package is a ledger core: an append-only JSONL event log plus a
content-addressed snapshot store, both living in a ``.specround/`` directory
next to the reviewed document. Nothing here shells out to git, and nothing
requires the document to be tracked by (or even near) a repository.

The wire format is the contract; see ``docs/ledger-format.md``.
"""

from specround.errors import (
    AnchorError,
    InvariantError,
    LedgerError,
    SchemaError,
    SnapshotError,
    SpecroundError,
)

__version__ = "0.0.1"

__all__ = [
    "AnchorError",
    "InvariantError",
    "LedgerError",
    "SchemaError",
    "SnapshotError",
    "SpecroundError",
    "__version__",
]
