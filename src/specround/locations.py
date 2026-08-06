"""Where a store lives (G5, G10).

A review store is one directory: ``ledger.jsonl``, ``objects/``, and an
``origin`` breadcrumb. This module answers the only question that directory
raises — which directory, for this document?

The default is a **central** store under the user's data directory, keyed by the
document's absolute path. Keeping it beside the document was tried and rejected:
inside a working tree a ``.specround/`` is untracked noise, and telling people to
write gitignore lines before they can review a document makes "no git required"
false in the one place it was supposed to be true. A central store is the model
an agent CLI already uses for session history — the tool keeps its own records
under the user's data directory and finds them again by path.

Teams that want the history shared do the opposite on purpose: a config file
opts the store back into the repository, where it is tracked like any other
file. That is a decision someone typed, not a default that arrives with a chore.

Resolution is three tiers, nearest wins: **argument > config > default**.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from specround.errors import ConfigError
from specround.events import canonical_json

#: The store directory's name when it sits inside a working tree.
STORE_DIRNAME = ".specround"
#: Opt-in configuration, found by walking up from the document.
CONFIG_FILENAME = ".specround.json"
#: The application's own directory under the data home.
APP_DIRNAME = "specround"
#: Central stores live under here, one per document.
DOCS_DIRNAME = "docs"
#: The plain-text record of what a store was created for.
ORIGIN_FILENAME = "origin"
ORIGIN_SCHEMA = "specround.origin/v0"

#: An origin is one document (central) or a folder of them (in-tree).
DOCUMENT = "document"
DIRECTORY = "directory"
ORIGIN_KINDS = (DOCUMENT, DIRECTORY)

#: What a config file may ask for.
MODES = ("central", "beside", "path")
#: Which tier decided a location.
SOURCES = ("argument", "config", "default")

#: Central store keys are sharded one level deep, like snapshot objects.
_SHARD = 2


# -- the central store ---------------------------------------------------


def data_home() -> Path:
    """The data directory — ``$XDG_DATA_HOME``, else ``~/.local/share``.

    Data, not cache and not config: a store is the only copy of a review's
    history, so it must not sit anywhere a cleaner may evict, and it is not
    hand-edited settings. ``$XDG_DATA_HOME`` is honoured only when absolute,
    which is what the basedir spec requires of a reader.

    One rule on every platform. macOS has its own convention
    (``~/Library/Application Support``), but a path that changes per platform is
    a path nobody can type from memory, and this project's promise is that plain
    tools reach the files — ``cat`` on a path you can guess.
    """
    configured = os.environ.get("XDG_DATA_HOME", "")
    if configured and Path(configured).is_absolute():
        return Path(configured)
    return Path.home() / ".local" / "share"


def central_root() -> Path:
    """The application directory that holds every central store."""
    return data_home() / APP_DIRNAME


def path_key(path: Path) -> str:
    """The store key for a path: the sha256 of its resolved absolute form.

    Resolving first is what makes the key deterministic — ``./spec.md``,
    ``../docs/spec.md`` and a symlink to the same file all land on one store,
    which is the point: one document, one history. The digest is over the path
    text, not the contents, so a key survives every edit the document will ever
    get.
    """
    return hashlib.sha256(str(Path(path).resolve()).encode("utf-8")).hexdigest()


def central_store_dir(doc: Path) -> Path:
    """The central store that owns ``doc``."""
    key = path_key(doc)
    return central_root() / DOCS_DIRNAME / key[:_SHARD] / key[_SHARD:]


# -- what a store was made for -------------------------------------------


@dataclass(frozen=True)
class Origin:
    """What a store was created for, and what its document keys count from.

    Written to the store as a plain-text breadcrumb. A central store is found by
    hashing a path, and a hash is a one-way trip: without this record a
    directory full of review history could not say whose it is. Re-binding a
    moved or renamed document (H10) is not implemented — keeping it possible is
    the whole job of this field.
    """

    kind: str
    path: Path

    def __post_init__(self) -> None:
        if self.kind not in ORIGIN_KINDS:
            raise ConfigError(f"unknown origin kind {self.kind!r}: expected one of {ORIGIN_KINDS}")
        if not self.path.is_absolute():
            raise ConfigError(f"an origin path must be absolute, got {self.path}")

    @property
    def base_dir(self) -> Path:
        """The folder document keys are relative to."""
        return self.path.parent if self.kind == DOCUMENT else self.path

    def to_json(self) -> dict[str, Any]:
        return {"schema": ORIGIN_SCHEMA, "kind": self.kind, "path": str(self.path)}

    @classmethod
    def from_json(cls, data: Mapping[str, Any]) -> "Origin":
        if not isinstance(data, Mapping):
            raise ConfigError("an origin record must be a JSON object")
        unknown = sorted(set(data) - {"schema", "kind", "path"})
        if unknown:
            raise ConfigError(f"unknown key(s) in origin record: {', '.join(unknown)}")
        schema = data.get("schema")
        if schema != ORIGIN_SCHEMA:
            raise ConfigError(f"unsupported origin schema {schema!r}: expected {ORIGIN_SCHEMA!r}")
        path = data.get("path")
        if not isinstance(path, str) or not path:
            raise ConfigError("an origin record needs a non-empty 'path'")
        return cls(kind=data.get("kind", ""), path=Path(path))

    def encode(self) -> str:
        """The single line this origin is stored as."""
        return canonical_json(self.to_json()) + "\n"

    @classmethod
    def decode(cls, text: str) -> "Origin":
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ConfigError(f"origin record is not valid JSON ({exc})") from exc
        return cls.from_json(data)


@dataclass(frozen=True)
class StoreLocation:
    """A resolved answer: the store directory, what it serves, and who decided."""

    root: Path
    origin: Origin
    source: str
    config: Path | None = None

    def __post_init__(self) -> None:
        if self.source not in SOURCES:
            raise ConfigError(f"unknown location source {self.source!r}: expected one of {SOURCES}")

    @property
    def is_central(self) -> bool:
        return self.origin.kind == DOCUMENT


# -- configuration -------------------------------------------------------


def find_config(start: Path) -> Path | None:
    """The nearest ``.specround.json`` at or above ``start``.

    Walking up is what lets one file at the top of a repository speak for every
    document under it, and the nearest file winning is what lets one folder
    disagree. The walk ends at the filesystem root; there is no marker that
    stops it, because a config that only sometimes applies is a config people
    have to reason about instead of read.
    """
    current = Path(start).resolve()
    for directory in (current, *current.parents):
        candidate = directory / CONFIG_FILENAME
        if candidate.is_file():
            return candidate
    return None


def read_config(path: Path) -> dict[str, Any] | None:
    """Return the validated ``store`` section of a config file, or ``None``.

    JSON rather than TOML: ``tomllib`` arrived in 3.11, this package supports
    3.10 and declares no dependencies, so TOML would cost either a dependency or
    a feature that vanishes on a supported interpreter. The project's format axis
    is JSON already.

    Unknown keys are refused for the reason the ledger refuses them — a setting
    that is silently ignored is a setting someone believes is in effect.
    """
    path = Path(path)
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"cannot read {path}: {exc}") from exc
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ConfigError(f"{path}: not valid JSON ({exc})") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"{path}: the top level must be a JSON object")
    unknown = sorted(set(data) - {"store"})
    if unknown:
        raise ConfigError(f"{path}: unknown top-level key(s): {', '.join(unknown)}")
    if "store" not in data:
        return None
    section = data["store"]
    if not isinstance(section, dict):
        raise ConfigError(f"{path}: 'store' must be an object")
    unknown = sorted(set(section) - {"mode", "path"})
    if unknown:
        raise ConfigError(f"{path}: unknown key(s) in 'store': {', '.join(unknown)}")
    mode = section.get("mode")
    if mode not in MODES:
        raise ConfigError(f"{path}: 'store.mode' must be one of {MODES}, got {mode!r}")
    location = section.get("path")
    if mode == "path":
        if not isinstance(location, str) or not location:
            raise ConfigError(f"{path}: mode 'path' needs a non-empty 'store.path'")
    elif location is not None:
        raise ConfigError(f"{path}: 'store.path' only applies to mode 'path', not {mode!r}")
    return {"mode": mode, "path": location}


# -- resolution ----------------------------------------------------------


def resolve_location(
    doc: Path,
    *,
    store: Path | None = None,
    base: Path | None = None,
) -> StoreLocation:
    """Decide where ``doc``'s review history lives.

    Three tiers, nearest wins:

    ``argument``
        An explicit ``store`` directory. Its keys count from ``base`` when given,
        otherwise from the store's parent — which is what makes ``.specround``
        beside a document a special case of this rule rather than a second one.
    ``config``
        The nearest ``.specround.json``: ``beside`` puts the store in the
        document's folder, ``path`` puts it at a chosen place relative to the
        config file, ``central`` restates the default.
    ``default``
        The central store, keyed by the document's absolute path. Nothing is
        written anywhere near the document.
    """
    document = Path(doc).resolve()

    if store is not None:
        root = Path(store).resolve()
        anchor = Path(base).resolve() if base is not None else root.parent
        return StoreLocation(root=root, origin=Origin(DIRECTORY, anchor), source="argument")

    config_path = find_config(document.parent)
    if config_path is not None:
        settings = read_config(config_path)
        if settings is not None:
            return _from_config(document, config_path, settings)

    return StoreLocation(
        root=central_store_dir(document),
        origin=Origin(DOCUMENT, document),
        source="default",
    )


def _from_config(document: Path, config_path: Path, settings: Mapping[str, Any]) -> StoreLocation:
    mode = settings["mode"]
    config_dir = config_path.parent
    if mode == "beside":
        root = document.parent / STORE_DIRNAME
        origin = Origin(DIRECTORY, document.parent)
    elif mode == "path":
        # A relative path counts from the config file, so the setting means the
        # same thing to everyone who clones the repository. An absolute one wins
        # outright — ``Path.__truediv__`` already does exactly that.
        root = (config_dir / settings["path"]).resolve()
        origin = Origin(DIRECTORY, config_dir)
    else:
        root = central_store_dir(document)
        origin = Origin(DOCUMENT, document)
    return StoreLocation(root=root, origin=origin, source="config", config=config_path)
