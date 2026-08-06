"""specround — spec review rounds for humans and AI agents.

The package is a ledger core: an append-only JSONL event log plus a
content-addressed snapshot store, living together in one store directory. By
default that directory is central — under the user's data home, keyed by the
document's absolute path — so reviewing a document adds nothing to the folder it
sits in. A config file opts the store back into the repository when a team wants
the history shared. Nothing here shells out to git, and nothing requires the
document to be tracked by (or even near) a repository.

The wire format is the contract; see ``docs/ledger-format.md``.
"""

from specround.anchors import Anchor, anchor_for, anchor_for_quote
from specround.errors import (
    AnchorError,
    ConfigError,
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
from specround.fold import Anchoring, Comment, Disposition, Reply, Round, State, fold
from specround.ledger import Ledger
from specround.reanchor import STRATEGIES, Rebind, reanchor
from specround.snapshots import SnapshotStore
from specround.store import STORE_DIRNAME, ReanchorReport, ReviewStore
from specround.locations import (
    CONFIG_FILENAME,
    ORIGIN_FILENAME,
    STORE_DIRNAME,
    Origin,
    StoreLocation,
    central_store_dir,
    data_home,
    resolve_location,
)
from specround.store import ReviewStore

__version__ = "0.0.1"

__all__ = [
    "Anchor",
    "AnchorError",
    "Anchoring",
    "CONFIG_FILENAME",
    "Comment",
    "ConfigError",
    "Disposition",
    "EVENT_TYPES",
    "InvariantError",
    "Ledger",
    "LedgerError",
    "ORIGIN_FILENAME",
    "Origin",
    "ReanchorReport",
    "Rebind",
    "Reply",
    "ReviewStore",
    "Round",
    "SCHEMA",
    "SCHEMA_NAME",
    "SCHEMA_VERSION",
    "STORE_DIRNAME",
    "STRATEGIES",
    "SchemaError",
    "SnapshotError",
    "SnapshotStore",
    "SpecroundError",
    "State",
    "StoreLocation",
    "TERMINAL_VERDICTS",
    "VERDICTS",
    "__version__",
    "anchor_for",
    "anchor_for_quote",
    "central_store_dir",
    "data_home",
    "fold",
    "reanchor",
    "resolve_location",
    "validate_event",
]
