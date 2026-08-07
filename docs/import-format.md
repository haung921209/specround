# Import format — `specround.import/v0`

> The boundary for comments made somewhere else (SPEC.md H9). A converter that
> can write this file is an importer; nothing else has to change.

Review comments get left in other tools — a diff viewer with a line gutter, a
code host, an editor plugin. Wherever they land, they land outside this ledger,
and a comment nobody can list is a comment lost (G3).

**The core does not know about any of those tools.** `specround import` reads
this format and nothing else. Per-tool converters live in [`adapters/`](../adapters),
outside the package, and their whole job is to emit these files. That is the
boundary: teaching the core about one viewer's storage would make every future
viewer a change to the core.

```bash
adapters/cmux-diff-comments.py --doc SPEC.md > incoming.json
specround import SPEC.md --file incoming.json            # the plan; nothing written
specround import SPEC.md --file incoming.json --apply    # record it
```

## 1. The file

One JSON object. `--file -` reads it from stdin.

```json
{
  "schema": "specround.import/v0",
  "source": "cmux",
  "comments": [
    {
      "id": "F3EFA75F-4D2A-4E0B-A711-6BE2822A4C54",
      "body": "too short for the proxy",
      "author": "bob",
      "ts": "2026-08-06T06:07:16Z",
      "quote": "30 seconds"
    }
  ]
}
```

| field | required | meaning |
|---|---|---|
| `schema` | ✓ | `specround.import/v0` |
| `source` | ✓ | which tool these came from. Namespaces `id` — see §4 |
| `comments` | ✓ | the items, possibly empty |

**Unknown keys are refused**, at both levels, for the reason the ledger refuses
them: a field this reader ignores is a field the writer believes is in effect.
That costs more here than in the ledger, because the writer is a converter
somebody else wrote and the thing it silently failed to say is *where a comment
goes*. A `schema` this reader does not implement is refused rather than guessed
at, same rule and same reason.

## 2. An item

| field | required | meaning |
|---|---|---|
| `id` | ✓ | the source tool's own identifier for this comment |
| `body` | ✓ | the comment text |
| `author` | | who wrote it. Falls back to the importing caller's `--author` |
| `ts` | | when the source recorded it |
| `quote` | conditional | the text the comment is about — required unless `whole` |
| `occurrence` | | which appearance of `quote`, 0-based, when it repeats |
| `span` | | `{"start": N, "end": N}` — character offsets into the round's base |
| `whole` | | `true` for a comment on the document as a whole, which has no anchor |

`ts` is carried rather than stored as the event's own timestamp: a ledger
record's `ts` is when it was appended, and rewriting that to the source's clock
would make the log's order and its timestamps disagree. It goes to `ext` (§4).

**An anchored item without a `quote` is refused, not defaulted.** Silently
treating it as a comment on the whole document would turn a converter bug into
a successful-looking import — exit 0, comment recorded, anchor gone. Saying
"this is about the document" takes one field: `"whole": true`.

**`whole` excludes `quote`, `span`, and `occurrence`**, and `span` excludes
`occurrence`. Both are the same rule: an item gets one statement about where it
goes. Two statements have no way to be made to agree, and offsets already say
which appearance is meant.

## 3. How an item is anchored

Comments anchor in **the round's base** — the snapshot the round froze, which
is the text the round is a review of (I7). Not the file on disk, which may have
moved on since the source tool read it.

| the item says | what happens |
|---|---|
| `quote` | the ordinary quote-to-anchor path, the same one `specround comment --quote` uses |
| `quote` + `occurrence` | likewise, picking the *n*-th appearance |
| `quote` + `span` | the base is checked at those offsets: `base[start:end] == quote`. Then the offsets are used |
| `whole` | no anchor |

There is no fifth row. **Offsets alone are not accepted**: a span with nothing
to check it against lands the comment wherever those numbers now point, which
is the silent wrong answer this whole format is arranged to prevent. The quote
is what makes offsets verifiable, and the offsets are what makes a repeated
quote unambiguous — each covers the other's weakness, the same pairing the
anchors themselves use (ledger-format §5).

An item is **refused by itself, with a reason**, when:

- the quote is not in the base (the document was revised after the source read it)
- the quote repeats and the item did not say which one
- the named occurrence does not exist
- the offsets disagree with the item's own quote
- the offsets run past the end of the base

Nothing is guessed onto a neighbouring span, and **a refusal does not take the
file down with it**. The other items still import. That is deliberate: a
document that moved under one comment says nothing about the other twenty, and
re-running the same file afterwards is safe (§4).

A refused item is reported, not recorded. There is no `anchor.orphan` for it —
orphaning is for a comment that *is* in the ledger and lost its text, and this
one never got in.

## 4. Where it came from, and importing twice

Every imported comment records its origin in the ledger's reserved `ext`
object, which is exactly what that field is for (ledger-format §2):

```json
{"import": {"source": "cmux", "id": "F3EFA75F-…", "ts": "2026-08-06T06:07:16Z"}}
```

`ts` is omitted when the item had none. The pair `(source, id)` is the
**idempotency key**: before importing, the tool reads the comments already on
this document and skips any item whose origin is already there.

So **importing the same file twice imports it once**, and the second run says
which comments the first one made. This is what makes per-item refusals
workable: fix the one bad item, run the file again, and only the fixed item
lands.

`source` namespaces the id, so two tools that both use plain integers do not
collide. A comment with an `ext.import` of some other shape is not one of ours
and is not mistaken for one — `ext` is by contract a field this reader does not
police, so it is read defensively.

## 5. What `import` does with a round

An import needs an **open round**, like any other writing verb, and lands in it.
No open round is exit `3` — open one. Two open rounds asks for `--round`.

`--apply` is required to write. Without it the plan is printed and the ledger is
untouched: an import writes somebody else's judgement into this history, and the
part that can be wrong is the anchoring, so the plan is the thing you read
first.

Exit code is `0` even when items were refused — the same shape `reanchor`
already has for a comment it could not place. The refusals are in the output and
in `--json` under `rejected`, each with its reason. `--json` also carries
`planned`, `skipped`, `imported`, and `counts`.

A malformed file is exit `2`: the history has not been consulted yet and refuses
nothing, so it is the caller's to fix — the same class as a bad command line.

## 6. Writing a converter

The contract is this file. A converter reads its tool's storage and writes the
JSON; it does not need to import anything from this package, and it should not
need updating when the ledger grows a field.

Two things a converter owes its reader:

- **Emit the quote from the text the tool actually captured**, not from a
  re-read of the file. If the tool stored the line it commented on, that string
  is the quote. Reconstructing it from a document that may have changed is the
  guess this format exists to avoid.
- **Say when it dropped something, on stderr.** A converter that silently skips
  a comment it could not represent is the same loss as a viewer that never
  exported it.

Offsets are optional and worth emitting only when the converter can verify them
locally — see [`adapters/cmux-diff-comments.py`](../adapters/cmux-diff-comments.py),
which offers them under `--with-span` and falls back to quoting when the line it
recorded is no longer where it was.
