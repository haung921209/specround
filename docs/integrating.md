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
| `view` stdout | the **first line is the URL**, before the server is up; a `port …` line follows when the port fell back and says why | README / SPEC §3 |
| external comments in | `specround.import/v0` | [`import-format.md`](import-format.md) — per-tool converters stay outside the core |

Two consequences worth spelling out:

- **Watch the ledger, not the wire.** A watcher that tails `ledger.jsonl`
  sees every surface's writes — CLI, web view, harvester, import — at one
  choke point, with `seq` for a cursor. It also keeps working while no server
  is running.
- **Collect through the CLI.** `comments --json` / `round status --json` are
  the read path an adapter can parse; their field sets are closed and their
  envelope is versioned.

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
