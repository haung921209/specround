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
| the ledger and the store layout | `specround.ledger/v0` | [`ledger-format.md`](ledger-format.md) — the format *is* the contract, including where a store lives and how a document's key is derived |
| CLI output | `specround.cli/v0` | every verb takes `--json` and the envelope carries its schema; human tables are for humans and may be reworded |
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
- **An open round's `render` and `raw` show the round's base, and that is not
  staleness.** A comment on this round is verified against the snapshot the
  round froze (I7), so the two modes a comment is made in have to show that
  snapshot. Edit the file while the round is open and the edit appears in the
  **diff** mode, which is what diff mode is for — not in the other two. Close
  the round and open the next one and the new base is picked up, again without
  a restart.

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
