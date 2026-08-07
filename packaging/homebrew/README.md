# Homebrew

`specround.rb` beside this file is the formula. It lives here, in the source
repository, because the version and the entry point it asserts are facts about
this package — the tap repository is where it gets *published*, not where it is
maintained.

```
brew tap haung921209/specround
brew install specround
```

## The tap

Homebrew resolves `haung921209/specround` to the GitHub repository
**`haung921209/homebrew-specround`**; the `homebrew-` prefix is how the short
name works, and the formula has to sit at `Formula/specround.rb` inside it.

```
homebrew-specround/
└── Formula/
    └── specround.rb      # a copy of packaging/homebrew/specround.rb
```

Creating it, once:

```bash
gh repo create haung921209/homebrew-specround --public \
  --description "Homebrew tap for specround"
git clone https://github.com/haung921209/homebrew-specround
mkdir -p homebrew-specround/Formula
cp packaging/homebrew/specround.rb homebrew-specround/Formula/specround.rb
```

`brew tap-new haung921209/specround` will also scaffold it, with a CI workflow
this tap has no bottles to run.

If you would rather not carry a second repository, any existing personal tap
takes the same file — `Formula/specround.rb` in `homebrew-<name>`, installed as
`brew install haung921209/<name>/specround`. The formula is identical; only the
path and the install line change.

## Releasing

The version is written in exactly one place, `src/specround/__init__.py`.
`pyproject.toml` reads it from there, so the distribution metadata, the sdist's
filename, and `specround --version` cannot disagree — and `tests/test_package.py`
fails if that wiring ever breaks.

1. **Bump** `__version__` in `src/specround/__init__.py`.
2. **Green**: `uv run pytest`.
3. **Build**: `uv build` → `dist/specround-X.Y.Z.tar.gz` and a wheel.
   The formula uses the sdist; the wheel is for anything installing from PyPI.
4. **Tag** the commit that built it: `git tag vX.Y.Z && git push origin vX.Y.Z`.
5. **Release**, with the sdist attached — this is the file the formula's `url`
   points at, so the release must carry it:

   ```bash
   gh release create vX.Y.Z dist/specround-X.Y.Z.tar.gz \
     --title "specround X.Y.Z" --notes "..."
   ```

6. **Checksum** what the release actually serves, not what is on your disk:

   ```bash
   curl -sL https://github.com/haung921209/specround/releases/download/vX.Y.Z/specround-X.Y.Z.tar.gz \
     | shasum -a 256
   ```

7. **Edit the formula** — `url` (both the tag and the filename carry the
   version) and `sha256`. It ships with 64 zeros as a placeholder, which fails
   loudly rather than installing something unverified.
8. **Publish** to the tap: copy `specround.rb` to `Formula/specround.rb` in
   `homebrew-specround`, commit, push.
9. **Verify** from a clean tap, the way a stranger would:

   ```bash
   brew untap haung921209/specround 2>/dev/null
   brew tap haung921209/specround
   brew install specround        # no bottles: this builds from source
   brew test specround
   specround --version
   ```

`brew audit --strict --new --formula haung921209/specround/specround` should be
silent once the release exists. Before it exists, the one thing it reports is
the `url` returning 404 — that is the release step above, not a defect in the
formula.

## What is already known to work

The formula was installed and tested locally against a `file://` URL pointing at
a locally built sdist — the only difference from a real release is where the
tarball came from. `brew install --build-from-source`, the virtualenv, the
linked `specround` executable, and `brew test` all passed, and `brew audit
--strict` found nothing. Two things about that are worth keeping:

**No `resource` blocks, on purpose.** The package has no runtime dependencies,
so the virtualenv holds one thing. `tests/test_package.py` asserts that
emptiness, which is what makes it safe to leave the formula this short: the day
a dependency arrives, the suite fails and this file has to be revisited.

**The build downloads the build backend.** Homebrew installs with pip's build
isolation on, so `hatchling` is fetched from PyPI while the formula builds.
That is fine for a tap, which builds on the user's machine with a network. A
submission to homebrew-core, which builds without one, would have to carry the
backend as `resource` blocks.

## Two things that look like problems and are not

**Lint outside a tap.** Running `brew style packaging/homebrew/specround.rb`
from this repository reports a missing Sorbet sigil and a missing frozen string
literal comment. Those cops are excluded for files under a `Formula/` directory,
which is where this file is graded. Copy it into a `Formula/` directory and the
same command reports nothing; that is the result that counts.

**A shadowed executable.** If `specround` is also installed with `uv tool
install` or `pipx`, `~/.local/bin` usually comes before Homebrew's prefix in
`PATH`, and brew says so at the end of the install. Both installations are
intact — the name resolves to the other one. Remove the other install, or call
`$(brew --prefix)/bin/specround` to be unambiguous.
