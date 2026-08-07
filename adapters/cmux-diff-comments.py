#!/usr/bin/env python3
"""Convert cmux's diff-viewer comments into a specround import file.

An example converter, and the reference one. It is a standalone script on
purpose: nothing in the ``specround`` package knows this tool exists, and
nothing here imports from the package. The only thing joining them is the file
format in ``docs/import-format.md``.

    adapters/cmux-diff-comments.py --doc SPEC.md > incoming.json
    specround import SPEC.md --file incoming.json          # the plan
    specround import SPEC.md --file incoming.json --apply  # record it

cmux keeps line comments from its diff viewer in a directory of JSON files, one
per diff, each holding the repository root it was taken in and the comments made
in it:

    {"repoRoot": "/path/to/repo",
     "comments": [{"id": "…", "filePath": "docs/spec.md", "startLine": 10,
                   "endLine": 10, "lineText": "…", "message": "…",
                   "side": "additions", "createdAt": "…", "updatedAt": "…"}]}

Two things this converter will not do, because they are the failures the import
format is arranged to prevent:

**It quotes what cmux captured, never what the file says now.** ``lineText`` is
the line as the reviewer saw it. Re-reading the document to reconstruct a quote
would silently succeed against a line that has since been rewritten.

**It reports what it dropped, on stderr.** A comment this script cannot
represent is a comment that would otherwise vanish between two tools, which is
the same loss as never exporting it.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

IMPORT_SCHEMA = "specround.import/v0"
SOURCE = "cmux"

#: cmux's own name for the side of the diff a line is on. ``additions`` means
#: the new version, which is the only side whose line numbers can be looked up
#: in the file as it is now.
ADDITIONS = "additions"

#: Where cmux keeps the store, unless told otherwise. Derived from the user's
#: home rather than written down, so this file carries nobody's paths.
STORE_ENV = "CMUX_DIFF_COMMENTS_DIR"
STORE_TAIL = ("cmux", "diff-comments")


def default_store() -> Path:
    """The platform's usual place for cmux application data."""
    override = os.environ.get(STORE_ENV)
    if override:
        return Path(override).expanduser()
    if sys.platform == "darwin":
        return Path.home().joinpath("Library", "Application Support", *STORE_TAIL)
    data_home = os.environ.get("XDG_DATA_HOME")
    root = Path(data_home) if data_home and os.path.isabs(data_home) else Path.home() / ".local" / "share"
    return root.joinpath(*STORE_TAIL)


def warn(message: str) -> None:
    print(f"cmux-diff-comments: {message}", file=sys.stderr)


def load_files(store: Path) -> list[tuple[Path, dict]]:
    """Every readable comment file in the store, with the path it came from."""
    if not store.is_dir():
        raise SystemExit(
            f"cmux-diff-comments: {store} is not a directory — pass --store, "
            f"or set ${STORE_ENV}"
        )
    found = []
    for path in sorted(store.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            # Not fatal: one unreadable file should not hide the comments in
            # the others, but it must not pass unmentioned either.
            warn(f"skipping {path.name}: {exc}")
            continue
        if isinstance(payload, dict):
            found.append((path, payload))
        else:
            warn(f"skipping {path.name}: not a JSON object")
    return found


def comments_for(store: Path, doc: Path) -> list[dict]:
    """cmux comments whose file is ``doc``, matched by resolved absolute path.

    Path identity rather than name matching: the store holds comments from
    several repositories at once, and two of them having a ``docs/spec.md`` is
    the normal case, not the exception.
    """
    wanted = doc.resolve()
    collected: list[dict] = []
    for path, payload in load_files(store):
        root = payload.get("repoRoot")
        if not isinstance(root, str):
            warn(f"skipping {path.name}: no repoRoot")
            continue
        base = Path(root).expanduser()
        for comment in payload.get("comments") or []:
            if not isinstance(comment, dict):
                continue
            relative = comment.get("filePath")
            if not isinstance(relative, str):
                continue
            try:
                target = (base / relative).resolve()
            except OSError:  # pragma: no cover - unresolvable path
                continue
            if target == wanted:
                collected.append(comment)
    return collected


def line_offsets(text: str) -> list[int]:
    """The character offset each 1-based line starts at; index 0 is unused."""
    offsets = [0, 0]
    for line in text.splitlines(keepends=True):
        offsets.append(offsets[-1] + len(line))
    return offsets


def span_for(text: str | None, offsets: list[int], line: int, quote: str) -> dict | None:
    """Offsets for ``quote`` on ``line``, when the document still has it there.

    Returns ``None`` rather than a guess. A span is an optional refinement that
    disambiguates a repeated quote; if the document has moved under it, the
    quote alone is the better answer and the importer's ladder will find it.
    """
    if text is None or line <= 0 or line + 1 >= len(offsets):
        return None
    start = offsets[line]
    if text[start : start + len(quote)] != quote:
        return None
    return {"start": start, "end": start + len(quote)}


def convert(comments: list[dict], *, doc: Path, with_span: bool, author: str | None) -> list[dict]:
    text: str | None = None
    offsets: list[int] = []
    if with_span:
        try:
            text = doc.read_text(encoding="utf-8")
        except OSError as exc:
            warn(f"--with-span: cannot read {doc} ({exc}) — falling back to quotes alone")
        else:
            offsets = line_offsets(text)

    items = []
    for comment in comments:
        identifier = comment.get("id")
        message = (comment.get("message") or "").strip()
        quote = comment.get("lineText") or ""
        if not isinstance(identifier, str) or not identifier:
            warn("dropping a comment with no id — it could not be imported idempotently")
            continue
        if not message:
            warn(f"dropping {identifier}: no message")
            continue
        if not quote.strip():
            # A blank line gives nothing to anchor to. Turning it into a
            # whole-document comment would move the comment somewhere its
            # author did not put it.
            warn(f"dropping {identifier}: the commented line is blank, so there is no quote")
            continue

        start_line, end_line = comment.get("startLine"), comment.get("endLine")
        if isinstance(start_line, int) and isinstance(end_line, int) and start_line != end_line:
            warn(
                f"{identifier}: cmux recorded lines {start_line}-{end_line} but stored only one "
                "line of text — the anchor will cover that line"
            )

        item: dict = {"id": identifier, "body": message, "quote": quote}
        if author:
            item["author"] = author
        created = comment.get("createdAt")
        if isinstance(created, str) and created:
            item["ts"] = created

        side = comment.get("side")
        if with_span and isinstance(start_line, int):
            if side != ADDITIONS:
                warn(
                    f"{identifier}: side is {side!r}, so its line number counts in the old "
                    "version — importing by quote instead"
                )
            else:
                span = span_for(text, offsets, start_line, quote)
                if span is not None:
                    item["span"] = span
        items.append(item)
    return items


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="cmux-diff-comments.py",
        description="Convert cmux diff-viewer comments into a specround import file.",
        epilog="The output format is docs/import-format.md; feed it to 'specround import --file'.",
    )
    parser.add_argument("--doc", required=True, help="the document whose comments to collect")
    parser.add_argument(
        "--store",
        metavar="DIR",
        help=f"cmux's diff-comments directory (default: ${STORE_ENV}, else the platform's)",
    )
    parser.add_argument(
        "--with-span",
        action="store_true",
        help="also emit character offsets, when the document still has the line where cmux "
        "recorded it (they disambiguate a repeated quote; the importer verifies them)",
    )
    parser.add_argument(
        "--author", help="record this author on every comment (cmux does not store one)"
    )
    parser.add_argument("--source", default=SOURCE, help=f"the source name to record (default: {SOURCE})")
    parser.add_argument("--output", metavar="PATH", help="write here instead of stdout")
    args = parser.parse_args(argv)

    doc = Path(args.doc).expanduser()
    store = Path(args.store).expanduser() if args.store else default_store()
    found = comments_for(store, doc)
    if not found:
        warn(f"no cmux comments on {doc} in {store}")
    items = convert(found, doc=doc, with_span=args.with_span, author=args.author)

    payload = {"schema": IMPORT_SCHEMA, "source": args.source, "comments": items}
    text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output:
        Path(args.output).expanduser().write_text(text, encoding="utf-8")
        warn(f"wrote {len(items)} comment(s) to {args.output}")
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
