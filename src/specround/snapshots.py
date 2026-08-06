"""Content-addressed snapshot store (G2, G5).

A round freezes the document it reviews. The frozen copy is stored by the
sha256 of its bytes, so the same content is stored once and a reference is a
proof of content rather than a pointer to a mutable place.

There is no git here. Objects are ordinary files under the store's ``objects/``
directory — wherever :mod:`specround.locations` puts that store — so a document
that is untracked, or that lives nowhere near a repository, gets the same
behaviour as one that is committed.
"""

from __future__ import annotations

import hashlib
import os
import tempfile
from pathlib import Path

from specround.errors import SnapshotError

ALGORITHM = "sha256"
_HEX_DIGITS = 64
#: Objects are sharded one level deep to keep directory listings small.
_SHARD = 2


def digest_bytes(data: bytes) -> str:
    """Return the reference (``sha256:<hex>``) for ``data``."""
    if not isinstance(data, (bytes, bytearray)):
        raise SnapshotError("snapshot content must be bytes")
    return f"{ALGORITHM}:{hashlib.sha256(bytes(data)).hexdigest()}"


def digest_text(text: str) -> str:
    """Return the reference for ``text`` encoded as UTF-8."""
    if not isinstance(text, str):
        raise SnapshotError("snapshot text must be a string")
    return digest_bytes(text.encode("utf-8"))


def parse_ref(ref: str) -> str:
    """Validate a reference and return its hex digest."""
    if not isinstance(ref, str):
        raise SnapshotError("snapshot reference must be a string")
    algorithm, separator, hexdigest = ref.partition(":")
    if not separator:
        raise SnapshotError(f"malformed snapshot reference {ref!r}: expected '<algorithm>:<hex>'")
    if algorithm != ALGORITHM:
        raise SnapshotError(f"unsupported digest algorithm {algorithm!r}: expected {ALGORITHM!r}")
    if len(hexdigest) != _HEX_DIGITS or not all(c in "0123456789abcdef" for c in hexdigest):
        raise SnapshotError(f"malformed {ALGORITHM} digest in reference {ref!r}")
    return hexdigest


class SnapshotStore:
    """Immutable blobs under ``root/objects``, addressed by digest."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    @property
    def objects_dir(self) -> Path:
        return self.root / "objects"

    def path_for(self, ref: str) -> Path:
        hexdigest = parse_ref(ref)
        return self.objects_dir / hexdigest[:_SHARD] / hexdigest[_SHARD:]

    def has(self, ref: str) -> bool:
        return self.path_for(ref).is_file()

    def put_bytes(self, data: bytes) -> str:
        """Store ``data`` and return its reference. Storing twice is a no-op."""
        ref = digest_bytes(data)
        target = self.path_for(ref)
        if target.is_file():
            return ref
        target.parent.mkdir(parents=True, exist_ok=True)
        # Write to a sibling temp file and rename, so a reader never observes a
        # half-written object under a name that promises a digest.
        fd, tmp_name = tempfile.mkstemp(dir=target.parent, prefix=".tmp-")
        tmp = Path(tmp_name)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, target)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
        return ref

    def put_text(self, text: str) -> str:
        """Store ``text`` as UTF-8. Line endings are preserved verbatim."""
        if not isinstance(text, str):
            raise SnapshotError("snapshot text must be a string")
        return self.put_bytes(text.encode("utf-8"))

    def put_file(self, path: Path) -> str:
        """Store the current bytes of ``path``."""
        try:
            data = Path(path).read_bytes()
        except OSError as exc:
            raise SnapshotError(f"cannot read {path}: {exc}") from exc
        return self.put_bytes(data)

    def get_bytes(self, ref: str) -> bytes:
        """Return the stored bytes, verifying they still hash to ``ref``."""
        target = self.path_for(ref)
        try:
            data = target.read_bytes()
        except FileNotFoundError as exc:
            raise SnapshotError(f"snapshot {ref} is not in the store") from exc
        except OSError as exc:
            raise SnapshotError(f"cannot read snapshot {ref}: {exc}") from exc
        actual = digest_bytes(data)
        if actual != ref:
            raise SnapshotError(
                f"snapshot {ref} is corrupt: stored bytes hash to {actual}"
            )
        return data

    def get_text(self, ref: str) -> str:
        data = self.get_bytes(ref)
        try:
            return data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise SnapshotError(f"snapshot {ref} is not valid UTF-8") from exc
