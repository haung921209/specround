# Integrating with specround — what an adapter may depend on

An adapter is anything outside this repository that drives or observes a
review: an editor plugin, a terminal-multiplexer helper, a watcher that pokes
an agent session when a comment lands. This page names the surfaces an adapter
may build on — and the one it must not.

The rule behind the list: **the contract is in the formats, not in the
processes** (G5). Anything a process serves can change with the process;
anything written down as a format carries a version and refuses what it does
not know.

## The four promised surfaces

| surface | contract | where it is written |
|---|---|---|
| the ledger and the store layout | `specround.ledger/v1` (reads v0 too) | [`ledger-format.md`](ledger-format.md) — snapshots and revision history are append-only |
| CLI output | `specround.cli/v1` | versioned revision/provenance and publication-status fields; human tables may be reworded |
| exit codes | `0` ok · `2` fix the invocation · `3` the history refuses · `1` anything else | [`README.md`](../README.md) — judge by `$?`, never by matching output text |
| `view` stdout | the **first line is the URL**, before the server is up; `port …` and `token …` lines follow and say where each half of that URL came from | README / SPEC §3 |
| a document's URL | the **same document comes back on the same URL** — port and token both derived from, or stored against, the document's path. It moves only when the port was taken (`port_source: fallback`) or `--rotate-token` was passed, and both say so | README, and "restarting a view" below |
| a URL for anyone else | **hand out the `share` URL, never the owner one** — `--share read\|comment` mints a scoped second token (`share` in the JSON payload, a `share …` line on stdout; nothing shared disposes). It is not stored: stopping the view revokes every share, and the owner URL survives that same restart | README |
| external comments in | `specround.import/v0` | [`import-format.md`](import-format.md) — per-tool converters stay outside the core |

Two consequences worth spelling out:

- **Watch the ledger, not the wire.** A watcher that tails `ledger.jsonl`
  sees every surface's writes — CLI, web view, harvester, import — at one
  choke point, with `seq` for a cursor. It also keeps working while no server
  is running.
- **Collect through the CLI.** `comments --json` / `round status --json` are
  the read path an adapter can parse; their field sets are closed and their
  envelope is versioned.

## Completing a review as an agent

Collecting a comment does not complete it. For each comment, read its thread,
make and verify the change or answer it, then record the verdict and end the
conversation when it is finished:

```bash
specround comments SPEC.md --context --json
specround dispose SPEC.md --comment c-ID --as applied --why "changed and verified" --resolve --actor agent
# Already disposed, but the conversation is still open:
specround resolve SPEC.md --comment c-ID --actor agent --note "complete"
specround round status SPEC.md --json
specround round close SPEC.md
```

`dispose`, `round status`, and `round close` JSON payloads include `next_actions`:
objects with `verb` (`dispose` or `resolve`), `comment` (the full ID), and `when`
(`final_verdict_decided` or `conversation_complete`).
They describe remaining work, not unconditional commands: a thread awaiting a
reviewer's answer must stay open. Status covers every round on the document;
close covers the round being closed; dispose covers the affected comment.
New resolutions require a final verdict first: `applied`, `rejected` or `answered`.
To defer a completed thread, first reopen it and then supersede its verdict.
There is no additional pending state or queue. Historical resolutions without
a final verdict remain readable; `round status` reports their IDs separately
as `incomplete_resolutions`. Inspect them with `comments --all --context --json`
and record the missing outcome. Reading status does not reopen or modify them.

`comments --context` adds a `contexts` map keyed by comment ID (or `null` for a
whole-document comment). It names the snapshot hash, the anchor's `line_start`
and `line_end`, and two lines around the anchor's starting line with their line
numbers. `matches_file` says whether those offsets describe the file currently
on disk. Do not apply snapshot line numbers directly to a changed file. The
ordinary comment object already carries the quoted anchor and all replies in
order, with the latest reply last. Orphan context comes from its last placed
snapshot, not a guessed location in the current file.

Both `undisposed` and `unresolved_threads` must be empty to report the document's
review complete. `round close` checks those two axes on its own round and exits
3 if either is left. Explicit carry-over requires `--allow-undisposed` and/or
`--allow-unresolved`; closing with these flags does not complete or hide the
remaining work. Report those retained IDs and reasons in the handoff.
`deferred` remains outstanding and cannot be combined with `dispose --resolve`.

The combined dispose/resolve command writes two ordinary ledger events. If it
is interrupted after the verdict, inspect status and run `resolve` for that ID;
do not repeat the verdict with `--supersede` just to close the thread.

## Named review scope: pass the ID, not just a filename

`review open a.md b.md` creates an isolated, fixed file selection; `review open
docs/` creates a directory selection. Both return a `scope.id` (`R-…`). Pass that
ID with `--review` on all document commands. To collect the complete review, use
`comments --review R-ID --context --json`, not an unscoped scan of its files.
An open named review containing a file makes an unscoped document command fail
with the candidate review IDs rather than silently choosing another ledger.

Every scoped response identifies `scope`: title, root, exact `members`, `new_files`,
`missing_files`, status, comment visibility, membership policy, counts and command
argument arrays. Review-wide comments/status also return `documents`, each with a
document key/path, publication `review` status and conditional `next_actions`.
These actions retain the existing verdict/conversation distinction; no new pending
state is introduced. Execute a document action with both its path and `--review`.

Directory discovery is not publication. If `scope.new_files` is nonempty, those
files are not visible in that review yet. After verifying edits, run `review
refresh R-ID --json`; inspect per-file `results` including ambiguous/orphaned
anchors. Missing members retain their last published snapshots and comments.
Explicit selections never expand. `review close R-ID` checks the entire group,
including unpublished new files; individual `round open/close --review` is refused.

Review A and B may name the same physical file, but their comments, snapshots,
tokens and completion state never merge. Editing the working file does affect the
same disk path: refreshing B is a separate decision, not a side effect of A.
Legacy document stores are not imported, and `view directory` is not a named
review. Handoff must include the review ID, members, unpublished changes and the
`scope.commands.comments` command so the next agent keeps this boundary.

The registry is `<central-root>/reviews/R-ID/review.json` with manifest schema
`specround.review/v1`; its sibling `store/` uses the existing directory-store
format and ledger v1. Each member is an ordinary round with `ext.review.id`.
Group operations preflight but are not cross-file transactions. On partial
failure, JSON errors include `report.review_id` and per-file `report.results`;
read status before retrying. Do not claim the whole group succeeded.

## Publishing file edits during a round

The CLI's `review` object (on comments, replies, dispositions, thread actions,
and round commands) describes the **published review snapshot**, not an acknowledgement
from an individual browser tab. `unpublished_changes: true` means render/raw will
still serve older text even after a reload. The hashes `base` and `file`, revision
ID, change counts, and `next_action` make this explicit.

After changing and verifying the file, run `specround round refresh SPEC.md`.
Do not close the round or resolve unfinished comments just to expose new text.
Check the returned `carried` report for orphaned or ambiguous anchors. The round
ID stays stable; the revision ID changes. Each comment records `anchor_base`
and `revision` for its original anchor, separately from later anchor placements.

Browser tabs announce the new revision and offer to load it. They do not replace
an active draft. An old revision cannot submit a comment as if it had reviewed
the new one, even when a later revision restores the same bytes.

New ledger writes use v1; old v0 events remain readable without rewriting them.
CLI JSON also uses `specround.cli/v1`, since a round's `base` now names its current
published revision; use `initial_base` for the opening snapshot.
Upgrade every CLI and running server sharing a store before writing with this
version. A v0-only binary will refuse histories containing v1 records.

## What a running view does *not* need restarting for

An adapter that cycles rounds around a live view should not be restarting the
server between them. This is written down because the opposite was inferred
once, from a page that looked stale, and the restart it produced cost a
reviewer's comment.

- **The round is resolved per request, not at startup.** A view started with no
  `--round` serves "the one open round" as of each request. Close a round from
  another terminal and open the next one, and the running view moves to it —
  new round, new base, new anchor space — with nobody restarting anything. The
  same is true in the other direction: a view started before any round existed
  begins commenting the moment one is opened.
- **Render/raw show the latest published revision of the round.** File edits
  appear in diff first. `round refresh` freezes those edits as the next review
  revision and carries comments there without closing the round. The initial
  snapshot stays in `initial_base`; `base` names the current published snapshot.
  Diff compares that current snapshot with the working file.

Reading the first as staleness produces a restart-per-round procedure, and
before the token was persisted every restart also rotated it: the URL the pane
was holding started answering `403`, and a comment submitted through it was
refused rather than saved. The behaviour above is pinned by tests
(`test_webview.py`) so that a later reader diagnoses it as design rather than
as a bug.

**When a view genuinely has to be restarted** — the package was upgraded, the
process died — the URL is unchanged, so an adapter that cached it can keep it.
Only `--rotate-token` and a taken port move it, and both announce themselves.

## The surface that is *not* promised

**The web view's HTTP API (`/api/*`) is internal.** It exists for the page
that ships in the same commit; the drift gates that keep page and server
aligned run inside this repository and protect nobody outside it. It carries
no version, and it changes without notice.

If a real consumer appears that cannot work through the ledger and the CLI,
the route is not "start depending on it quietly" — it is promoting the API to
a versioned schema (`specround.api/v0`), the same move every other surface
made. Open an issue; that is exactly what the `hole` label is for.

## What might become a contract later

- **An event hook** — "when events land in a ledger, run this command." Today
  that loop lives in adapters (a watcher process each adapter runs its own
  way). If several adapters end up rebuilding it, the polling loop — not the
  delivery (tmux, notifications, editors — those stay adapter-side) — is a
  candidate for the core to define once. Trigger: a second independent
  implementation, not before.

## Reference implementations

The [`adapters/`](../adapters) directory holds converters and examples that
consume only the promised surfaces and import nothing from the package. That
is the standing shape for anything meant to be copied: an adapter that works
by reading the formats is an adapter another tool can port.
