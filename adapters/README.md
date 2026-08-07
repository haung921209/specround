# Adapters

Converters that take comments out of another tool and into a specround import
file. They live here, outside the package, and that placement is the design.

**`specround` does not know any of these tools exist.** The core reads one
documented format — [`docs/import-format.md`](../docs/import-format.md) — and an
adapter's whole job is to emit it. If the core knew about a diff viewer's
storage, every new viewer would be a change to the core and every change to a
viewer would be a release of this package.

So an adapter:

- is a standalone script, stdlib only, importing nothing from `specround`
- takes its tool's storage and writes `specround.import/v0` to stdout
- quotes the text its tool captured, never a re-read of the document
- says on stderr what it dropped and why

Nothing here is on the `specround` install path. Run them from a checkout.

## `cmux-diff-comments.py`

For [cmux](https://cmux.com), whose diff viewer has a line gutter and keeps its
comments in a directory of JSON files outside any repository.

```bash
adapters/cmux-diff-comments.py --doc SPEC.md > incoming.json
specround import SPEC.md --file incoming.json            # the plan; nothing written
specround import SPEC.md --file incoming.json --apply    # record it
```

It finds the store at the platform's usual place for cmux application data;
`--store DIR` or `$CMUX_DIFF_COMMENTS_DIR` override that. Comments are matched
to the document by resolved absolute path, because one store holds several
repositories and two of them having a `docs/spec.md` is normal.

`--with-span` additionally emits character offsets, but only for lines the
document still has where cmux recorded them — offsets disambiguate a quote that
repeats, and the importer verifies them against the round's base before
believing them. Without it, or when the document has moved on, the quote alone
does the work.

Comments cmux stored on the deletions side of a diff carry line numbers that
count in the *old* version, so no span is emitted for them; they import by
quote. A comment on a blank line is dropped with a warning, because a blank line
gives nothing to anchor to and moving it to the document as a whole would put it
somewhere its author did not.

## Writing another one

Read the format doc, emit the JSON, and keep the two rules above. There is no
plugin interface to implement and nothing to register — a file this tool can
read is the entire contract.
