# The formula the tap serves. Copy it to Formula/specround.rb in
# haung921209/homebrew-specround; the checklist beside this file says when.
#
# It is short because the package has no runtime dependencies: a virtualenv
# with one thing in it, and no `resource` blocks to keep in sync with a lock
# file. tests/test_package.py asserts that emptiness, so the day a dependency
# arrives the suite says so before this file goes stale.
class Specround < Formula
  include Language::Python::Virtualenv

  desc "Spec review rounds for humans and AI agents"
  homepage "https://github.com/haung921209/specround"
  # The sdist built by `uv build` and attached to the release — not GitHub's
  # generated source archive, which carries the repository rather than the
  # package.
  url "https://github.com/haung921209/specround/releases/download/v0.1.0/specround-0.1.0.tar.gz"
  # PLACEHOLDER. Fill after the release is published:
  #   curl -sL <the url above> | shasum -a 256
  # or, equivalently, from the file the release was built from:
  #   shasum -a 256 dist/specround-0.1.0.tar.gz
  sha256 "a05c3813668ccd8a76b16c78ca07cabfed5a69236627fa127bb63ac66d23fdfb"
  license "MIT"

  # The package needs >= 3.10; this pins which interpreter the virtualenv is
  # built against. Bumping it is a formula edit and a `brew reinstall`, not a
  # source change.
  depends_on "python@3.12"

  def install
    virtualenv_install_with_resources
  end

  test do
    assert_match "specround #{version}", shell_output("#{bin}/specround --version")

    # A version string only proves the entry point resolves. This proves the
    # install can hold a review: no server, no git, and the ledger under a
    # data home of its own rather than beside the document.
    ENV["XDG_DATA_HOME"] = testpath/"data"
    (testpath/"SPEC.md").write("# Widget\n\nTimeouts are 30 seconds.\n")

    system bin/"specround", "round", "open", testpath/"SPEC.md", "--author", "brew"
    system bin/"specround", "comment", testpath/"SPEC.md",
           "--quote", "30 seconds", "--body", "too short", "--author", "brew"

    assert_match "too short", shell_output("#{bin}/specround comments #{testpath}/SPEC.md")
    refute_path_exists testpath/".specround"
  end
end
