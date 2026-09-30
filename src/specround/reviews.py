"""Named review namespaces over the existing directory-store format."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import secrets
import shutil
import tempfile
from typing import Any

from specround.errors import InvariantError, SpecroundError
from specround.locations import DIRECTORY, Origin, canonical_path, central_root, read_origin
from specround.store import ReviewStore

REVIEW_ID = re.compile(r"R-[0-9a-f]{12}\Z")


class PartialReviewError(SpecroundError):
    def __init__(self, review_id: str, results: list[dict[str, Any]]):
        self.report = {"review_id": review_id, "results": results}
        super().__init__("review operation only partly completed: " + "; ".join(
            f"{r['doc']}: {r.get('error', r['status'])}" for r in results
        ))


class Review:
    def __init__(self, directory: Path, metadata: dict[str, Any]):
        self.directory = directory
        self.id = metadata.get("id")
        if (metadata.get("schema") != "specround.review/v1" or not isinstance(self.id, str)
                or not REVIEW_ID.fullmatch(self.id) or metadata.get("mode") not in ("files", "directory")
                or not isinstance(metadata.get("title"), str)
                or not isinstance(metadata.get("root"), str)
                or not Path(metadata["root"]).is_absolute()
                or not isinstance(metadata.get("files"), list)):
            raise SpecroundError(f"invalid review manifest in {directory}")
        self.metadata = metadata
        self.root = Path(metadata["root"])
        self.title = metadata["title"]
        self.mode = metadata["mode"]
        for key in metadata["files"]:
            self._key_path(key)
        origin = read_origin(directory / "store") or Origin(DIRECTORY, self.root)
        if origin.kind != DIRECTORY or origin.path != self.root:
            raise SpecroundError("review manifest and store origin disagree")
        self.store = ReviewStore(directory / "store", origin=origin)

    @staticmethod
    def registry() -> Path:
        return central_root() / "reviews"

    @classmethod
    def load(cls, review_id: str) -> Review:
        if not REVIEW_ID.fullmatch(review_id):
            raise SpecroundError(f"invalid review ID {review_id!r}")
        directory = cls.registry() / review_id
        try:
            metadata = json.loads((directory / "review.json").read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise SpecroundError(f"cannot read review {review_id}: {exc}") from exc
        if not isinstance(metadata, dict) or metadata.get("id") != review_id:
            raise SpecroundError(f"invalid review manifest for {review_id}")
        return cls(directory, metadata)

    @classmethod
    def all(cls) -> list[Review]:
        root = cls.registry()
        return [cls.load(p.name) for p in sorted(root.iterdir()) if REVIEW_ID.fullmatch(p.name)] if root.exists() else []

    @classmethod
    def containing(cls, path: Path) -> list[Review]:
        path = canonical_path(path)
        out = []
        for review in cls.all():
            try:
                key = path.relative_to(review.root).as_posix()
            except ValueError:
                continue
            if review.mode == "files":
                selected = key in review.metadata["files"]
            else:
                selected = path.suffix.lower() in (".md", ".markdown") and not any(p.startswith(".") for p in Path(key).parts)
            if selected and review.store.fold().open_rounds:
                out.append(review)
        return out

    @classmethod
    def create(cls, paths: list[Path], *, title: str, author: str) -> Review:
        from specround.workspace import Workspace

        paths = [canonical_path(p) for p in paths]
        if not paths or len(set(paths)) != len(paths):
            raise SpecroundError("choose one directory or distinct files")
        directory_mode = len(paths) == 1 and paths[0].is_dir()
        if not directory_mode and any(not p.is_file() for p in paths):
            raise SpecroundError("choose one directory or an explicit list of existing files")
        root = paths[0] if directory_mode else Path(os.path.commonpath([p.parent for p in paths]))
        if directory_mode:
            paths = sorted({canonical_path(root / key) for key in Workspace(root, bounded=True).scan()[0]
                            if canonical_path(root / key).is_relative_to(root)})
        if not paths:
            raise SpecroundError("the directory contains no reviewable Markdown files")
        for path in paths:
            try:
                path.read_bytes().decode("utf-8")
            except (OSError, UnicodeError) as exc:
                raise SpecroundError(f"cannot review {path}: {exc}") from exc
        registry = cls.registry()
        registry.mkdir(parents=True, exist_ok=True, mode=0o700)
        review_id = "R-" + secrets.token_hex(6)
        staging = Path(tempfile.mkdtemp(prefix=".creating-", dir=registry))
        metadata = {"schema": "specround.review/v1", "id": review_id, "title": title,
                    "mode": "directory" if directory_mode else "files", "root": str(root),
                    "files": [] if directory_mode else [p.relative_to(root).as_posix() for p in paths]}
        try:
            review = cls(staging, metadata)
            for path in paths:
                review.store.open_round(path, author=author, title=title, ext={"review": {"id": review_id}})
            (staging / "review.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            staging.rename(registry / review_id)
        except BaseException:
            if staging.exists():
                shutil.rmtree(staging)  # only the new, unpublished staging directory
            raise
        return cls.load(review_id)

    def _key_path(self, key: str) -> Path:
        if not isinstance(key, str) or not key or key != key.strip():
            raise SpecroundError("invalid document key in review")
        relative = Path(key)
        if relative.is_absolute() or relative.as_posix() != key or any(p in (".", "..") for p in relative.parts):
            raise SpecroundError("document key steps outside this review")
        path = self.root / relative
        if canonical_path(path) != path:
            raise SpecroundError("document path no longer names the published review member (symlink or alias)")
        return path

    def members(self, state=None) -> list[str]:
        state = state or self.store.fold()
        return sorted({r.doc for r in state.rounds.values()})

    def resolve_key(self, key: str) -> Path:
        path = self._key_path(key)
        if key not in self.members():
            raise SpecroundError(f"{key!r} is not published in review {self.id}; refresh the review to include new directory files")
        return path

    def resolve_input(self, value: str) -> Path:
        members = set(self.members())
        candidates = {canonical_path(Path(value).expanduser())}
        if not Path(value).is_absolute():
            candidates.add(canonical_path(self.root / value))
        selected = [p for p in candidates if p.is_relative_to(self.root) and p.relative_to(self.root).as_posix() in members]
        if len(selected) != 1:
            raise SpecroundError(f"{value!r} does not unambiguously name a published member of {self.id}")
        return self.resolve_key(selected[0].relative_to(self.root).as_posix())

    def new_files(self, state=None) -> list[str]:
        from specround.workspace import Workspace

        state = state or self.store.fold()
        if self.mode != "directory" or not state.open_rounds:
            return []
        found = {canonical_path(self.root / key) for key in Workspace(self.root, bounded=True).scan()[0]}
        return sorted(p.relative_to(self.root).as_posix() for p in found
                      if p.is_relative_to(self.root) and p.relative_to(self.root).as_posix() not in self.members(state))

    def describe(self, state=None) -> dict[str, Any]:
        state = state or self.store.fold()
        members = self.members(state)
        missing = []
        for key in members:
            try:
                if not self._key_path(key).is_file():
                    missing.append(key)
            except SpecroundError:
                missing.append(key)
        open_count = len(state.open_rounds)
        status = "closed" if not open_count else "open" if open_count == len(state.rounds) else "partially_closed"
        return {"id": self.id, "title": self.title, "mode": self.mode, "root": str(self.root),
                "members": members, "new_files": self.new_files(state), "missing_files": missing,
                "status": status, "comment_visibility": "this_review_only",
                "membership_policy": "publish_new_files_on_refresh" if self.mode == "directory" else "explicit_files",
                "working_files": "shared; published snapshots and comments are isolated",
                "counts": {"documents": len(members), "open_rounds": open_count,
                           "undisposed": len(state.undisposed), "unresolved_threads": len(state.active_threads)},
                "commands": {"comments": ["specround", "comments", "--review", self.id, "--json"],
                             "status": ["specround", "review", "status", self.id, "--json"],
                             "refresh": ["specround", "review", "refresh", self.id, "--json"],
                             "close": ["specround", "review", "close", self.id, "--json"],
                             "view": ["specround", "view", "--review", self.id]}}

    def review_status(self, path: Path, *, state=None, round_id=None) -> dict[str, Any]:
        state = state or self.store.fold()
        status = self.store.review_status(path, state=state, round_id=round_id)
        if status["next_action"]:
            if all(r.open for r in state.rounds.values()):
                status["next_action"] = {"verb": "review.refresh", "review_id": self.id,
                                         "when": "changes_ready_for_review"}
            else:
                status["next_action"] = None
                status["message"] += "; this review is closed or partially closed — open a new named review"
        return status

    def refresh(self, *, author: str) -> list[dict[str, Any]]:
        state = self.store.fold()
        if not state.open_rounds or any(not r.open for r in state.rounds.values()):
            raise InvariantError("cannot refresh a closed or partially closed review; open a new review")
        known = {r.doc: r for r in state.rounds.values()}
        keys = sorted(set(known) | set(self.new_files(state)))
        for key in keys:
            path = self._key_path(key)
            if path.exists():
                try:
                    path.read_bytes().decode("utf-8")
                except (OSError, UnicodeError) as exc:
                    raise SpecroundError(f"cannot publish {key}: {exc}") from exc
        results = []
        for key in keys:
            try:
                path = self._key_path(key)
                if not path.is_file():
                    results.append({"doc": key, "status": "retained_missing"})
                elif key not in known:
                    self.store.open_round(path, author=author, title=self.title, ext={"review": {"id": self.id}})
                    results.append({"doc": key, "status": "added"})
                else:
                    before = known[key]
                    report = self.store.refresh_round(before.id, path, author=author, expected_review=before.revision)
                    current = self.store.fold().rounds[before.id]
                    results.append({"doc": key, "status": "updated" if current.revision != before.revision else "unchanged",
                                    "orphaned": report.orphaned, "ambiguous": report.ambiguous})
            except (SpecroundError, OSError) as exc:
                results.append({"doc": key, "status": "error", "error": str(exc)})
                raise PartialReviewError(self.id, results) from exc
        return results

    def close(self, *, author: str, allow_undisposed=False, allow_unresolved=False) -> list[dict[str, Any]]:
        state = self.store.fold()
        if self.new_files(state):
            raise InvariantError("new directory files are not published; run review refresh before closing")
        if state.undisposed and not allow_undisposed:
            raise InvariantError("review has undisposed comments — record final verdicts before closing")
        if state.active_threads and not allow_unresolved:
            raise InvariantError("review has unresolved threads — resolve completed conversations before closing")
        results = []
        for round_ in state.open_rounds:
            try:
                self.store.close_round(round_.id, author=author, allow_undisposed=allow_undisposed,
                                       allow_unresolved=allow_unresolved)
                results.append({"doc": round_.doc, "status": "closed"})
            except (SpecroundError, OSError) as exc:
                results.append({"doc": round_.doc, "status": "error", "error": str(exc)})
                raise PartialReviewError(self.id, results) from exc
        return results
