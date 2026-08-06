"""specround — spec review rounds for humans and AI agents.

The package is a ledger core: an append-only JSONL event log plus a
content-addressed snapshot store, both living in a ``.specround/`` directory
next to the reviewed document. Nothing here shells out to git, and nothing
requires the document to be tracked by (or even near) a repository.

The wire format is the contract; see ``docs/ledger-format.md``.
"""

from specround.anchors import Anchor, anchor_for, anchor_for_quote
from specround.errors import (
    AnchorError,
    InvariantError,
    LedgerError,
    SchemaError,
    SnapshotError,
    SpecroundError,
)
from specround.events import (
    EVENT_TYPES,
    SCHEMA,
    SCHEMA_NAME,
    SCHEMA_VERSION,
    TERMINAL_VERDICTS,
    VERDICTS,
    validate_event,
)
from specround.fold import Comment, Disposition, Reply, Round, State, fold
from specround.ledger import Ledger
from specround.snapshots import SnapshotStore
from specround.store import STORE_DIRNAME, ReviewStore

__version__ = "0.0.1"

__all__ = [
    "Anchor",
    "AnchorError",
    "Comment",
    "Disposition",
    "EVENT_TYPES",
    "InvariantError",
    "Ledger",
    "LedgerError",
    "Reply",
    "ReviewStore",
    "Round",
    "SCHEMA",
    "SCHEMA_NAME",
    "SCHEMA_VERSION",
    "STORE_DIRNAME",
    "SchemaError",
    "SnapshotError",
    "SnapshotStore",
    "SpecroundError",
    "State",
    "TERMINAL_VERDICTS",
    "VERDICTS",
    "__version__",
    "anchor_for",
    "anchor_for_quote",
    "fold",
    "validate_event",
]
