"""Snapshot store: round trips, content addressing, and refusal to lie."""

import pytest

from specround.errors import SnapshotError
from specround.snapshots import SnapshotStore, digest_text, parse_ref


@pytest.fixture
def store(tmp_path):
    return SnapshotStore(tmp_path / ".specround")


def test_text_round_trip_is_byte_exact(store):
    text = "# Spec\n\n한글 · emoji 🌱 · tab\tend"
    ref = store.put_text(text)
    assert store.get_text(ref) == text


@pytest.mark.parametrize(
    "text",
    [
        "no trailing newline",
        "trailing newline\n",
        "two trailing newlines\n\n",
        "crlf line\r\nsecond\r\n",
        "lone cr\rsecond",
        "",
    ],
)
def test_line_endings_and_edges_survive_verbatim(store, text):
    # A round's base must be the document as it was, not a normalised copy:
    # anchors are character offsets into exactly these bytes.
    ref = store.put_text(text)
    assert store.get_text(ref) == text


def test_same_content_is_stored_once(store):
    first = store.put_text("identical")
    second = store.put_text("identical")
    assert first == second
    objects = [p for p in store.objects_dir.rglob("*") if p.is_file()]
    assert len(objects) == 1


def test_different_content_gets_different_refs(store):
    assert store.put_text("a") != store.put_text("b")


def test_ref_is_the_sha256_of_the_utf8_bytes(store):
    text = "content addressed"
    assert store.put_text(text) == digest_text(text)
    assert store.put_bytes(text.encode("utf-8")) == digest_text(text)


def test_has_reports_membership(store):
    ref = digest_text("not stored yet")
    assert store.has(ref) is False
    assert store.put_text("not stored yet") == ref
    assert store.has(ref) is True


def test_missing_object_is_reported_not_guessed(store):
    ref = digest_text("never stored")
    with pytest.raises(SnapshotError, match="is not in the store"):
        store.get_bytes(ref)


def test_corrupt_object_is_detected_on_read(store):
    ref = store.put_text("original")
    store.path_for(ref).write_bytes(b"tampered")
    with pytest.raises(SnapshotError, match="is corrupt"):
        store.get_bytes(ref)


def test_put_file_stores_current_bytes(store, tmp_path):
    doc = tmp_path / "spec.md"
    doc.write_text("first revision\n", encoding="utf-8")
    first = store.put_file(doc)
    doc.write_text("second revision\n", encoding="utf-8")
    second = store.put_file(doc)
    assert first != second
    # The frozen copy does not follow the document.
    assert store.get_text(first) == "first revision\n"


def test_put_file_reports_a_missing_document(store, tmp_path):
    with pytest.raises(SnapshotError, match="cannot read"):
        store.put_file(tmp_path / "absent.md")


@pytest.mark.parametrize(
    "ref",
    [
        "deadbeef",  # no algorithm
        "md5:" + "0" * 32,  # wrong algorithm
        "sha256:" + "0" * 63,  # short digest
        "sha256:" + "Z" * 64,  # non-hex
        "sha256:" + "A" * 64,  # uppercase is not the canonical form
    ],
)
def test_malformed_refs_are_rejected(ref):
    with pytest.raises(SnapshotError):
        parse_ref(ref)


def test_objects_are_sharded_one_level(store):
    ref = store.put_text("sharded")
    hexdigest = parse_ref(ref)
    path = store.path_for(ref)
    assert path.parent.name == hexdigest[:2]
    assert path.name == hexdigest[2:]
    assert path.parent.parent == store.objects_dir


def test_store_creates_its_directories_on_demand(tmp_path):
    store = SnapshotStore(tmp_path / "deep" / "nested" / ".specround")
    ref = store.put_text("created on demand")
    assert store.get_text(ref) == "created on demand"
