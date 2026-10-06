#!/usr/bin/env python3
"""Deterministic tests for the GitHub source-archive pin contract.

Run: python3 -B -m unittest discover -s tests -v
No network: forge HTTP responses and `git ls-remote` are stubbed. Recipes are
real PKGBUILDs evaluated by the checker's own metadata extractor.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT / ".agents/skills/aur-package-fleet-maintenance"))

import check

REPO = "acme/demo"
COMMIT = "a" * 40
MOVED_COMMIT = "b" * 40


def pkgbuild(source: str, pkgver: str = "1.2.3", extra: str = "", url: str = f"https://github.com/{REPO}") -> str:
    return f"""pkgname=demo
pkgver={pkgver}
pkgrel=1
pkgdesc='demo'
arch=('x86_64')
url='{url}'
license=('MIT')
{extra}
source=({source})
sha256sums=('SKIP')
"""


def recipe_from(text: str) -> check.Recipe:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        path = root / "demo" / "PKGBUILD"
        path.parent.mkdir()
        path.write_text(text, encoding="utf-8")
        recipe = check.extract_metadata(path, root)
    assert not recipe.metadata_error, recipe.metadata_error
    return recipe


def fake_http(tags=("v1.2.3",), npm_latest="1.2.3"):
    http = mock.Mock(spec=check.HttpClient)

    def get_paginated_json(url, page_size):
        if "/releases?" in url:
            return []
        if "/tags?" in url:
            return [{"name": name} for name in tags]
        raise AssertionError(f"unexpected URL {url}")

    def get_json(url):
        if "registry.npmjs.org" in url:
            return {"dist-tags": {"latest": npm_latest}, "version": npm_latest}
        raise AssertionError(f"unexpected URL {url}")

    http.get_paginated_json.side_effect = get_paginated_json
    http.get_json.side_effect = get_json
    return http


def remote(tags=None, heads=None):
    """Stub for check.ls_remote returning only the requested refs."""
    tags = tags or {}
    heads = heads or {}

    def ls_remote(repo, refs):
        return (
            {ref: sha for ref, sha in tags.get(repo, {}).items() if ref in refs},
            {ref: sha for ref, sha in heads.get(repo, {}).items() if ref in refs},
        )

    return ls_remote


def audit(text, override=None, tags=None, heads=None, http=None):
    recipe = recipe_from(text)
    target = check.classify(recipe, override or {})
    with mock.patch.object(check, "ls_remote", side_effect=remote(tags, heads)):
        return check.audit_one(recipe, target, http or fake_http())


PINNED_SOURCE = '"demo-$pkgver-$_commit.tar.gz::$url/archive/$_commit.tar.gz"'
PIN = f"_commit={COMMIT}"


class ArchiveParsingTest(unittest.TestCase):
    def parse(self, value):
        archive = check.github_archive_source(value)
        return archive and (archive.repo, archive.kind, archive.refs)

    def test_forms(self):
        cases = {
            f"https://github.com/{REPO}/archive/{COMMIT}/demo-1.2.3.tar.gz": (REPO, "commit", (COMMIT,)),
            f"x.tar.gz::https://github.com/{REPO}/archive/refs/tags/v1.2.3.tar.gz": (REPO, "tag", ("v1.2.3",)),
            f"https://github.com/{REPO}/archive/refs/tags/pkg%2Fv1.2.3.zip": (REPO, "tag", ("pkg/v1.2.3", "pkg")),
            f"https://github.com/{REPO}/archive/v1.2.3.tar.gz": (REPO, "ref", ("v1.2.3",)),
            f"https://github.com/{REPO}/archive/v1.2.3/demo-1.2.3.tar.gz": (REPO, "ref", ("v1.2.3/demo-1.2.3", "v1.2.3")),
            f"https://github.com/{REPO}/archive/refs/heads/main.tar.gz": (REPO, "branch", ("main",)),
            f"https://github.com/{REPO}/archive/{COMMIT.upper()}.tar.gz": (REPO, "commit", (COMMIT,)),
            f"https://codeload.github.com/{REPO}/tar.gz/refs/tags/v1.2.3": (REPO, "tag", ("v1.2.3",)),
            f"https://codeload.github.com/{REPO}/legacy.zip/v1.2.3": (REPO, "ref", ("v1.2.3",)),
            f"https://codeload.github.com/{REPO}/tar.gz/{COMMIT}": (REPO, "commit", (COMMIT,)),
            # An abbreviated hash is not an immutable commit reference.
            f"https://github.com/{REPO}/archive/{COMMIT[:12]}.tar.gz": (REPO, "ref", (COMMIT[:12],)),
            # A tag whose path merely ends in a commit-looking name is still a tag.
            f"https://github.com/{REPO}/archive/refs/tags/archive/{COMMIT}.tar.gz": (REPO, "tag", (f"archive/{COMMIT}", "archive")),
        }
        for value, expected in cases.items():
            with self.subTest(value=value):
                self.assertEqual(expected, self.parse(value))

    def test_non_archive_github_sources_ignored(self):
        for value in (
            f"https://github.com/{REPO}/releases/download/v1.2.3/demo-linux-x64.tar.gz",
            f"https://raw.githubusercontent.com/{REPO}/v1.2.3/LICENSE",
            f"git+https://github.com/{REPO}.git#tag=v1.2.3",
            "https://codeberg.org/acme/demo/archive/v1.2.3.tar.gz",
        ):
            with self.subTest(value=value):
                self.assertIsNone(check.github_archive_source(value))


class LsRemoteParsingTest(unittest.TestCase):
    def test_annotated_lightweight_and_branch(self):
        output = (
            f"{'1' * 40}\trefs/tags/v1.2.3\n"
            f"{COMMIT}\trefs/tags/v1.2.3^{{}}\n"
            f"{MOVED_COMMIT}\trefs/tags/v1.2.2\n"
            f"{'c' * 40}\trefs/heads/main\n"
        )
        tags, heads = check.parse_ls_remote(output)
        self.assertEqual({"v1.2.3": COMMIT, "v1.2.2": MOVED_COMMIT}, tags)
        self.assertEqual({"main": "c" * 40}, heads)


class NewPackageEnforcementTest(unittest.TestCase):
    """Packages without any audit-overrides entry are gated automatically."""

    def test_tag_archive_is_never_current(self):
        result = audit(pkgbuild('"demo-$pkgver.tar.gz::$url/archive/refs/tags/v$pkgver.tar.gz"'))
        self.assertEqual("uncertain", result.status)
        self.assertIn("mutable tag archive", result.detail)

    def test_ambiguous_ref_resolving_to_tag_is_uncertain(self):
        result = audit(
            pkgbuild('"demo-$pkgver.tar.gz::$url/archive/v$pkgver/demo-$pkgver.tar.gz"'),
            tags={REPO: {"v1.2.3": COMMIT}},
        )
        self.assertEqual("uncertain", result.status)
        self.assertIn(f"{REPO}@v1.2.3", result.detail)

    def test_ambiguous_ref_evidenced_as_branch_is_excluded(self):
        result = audit(
            pkgbuild('"demo.tar.gz::$url/archive/stable.tar.gz"'),
            heads={REPO: {"stable": COMMIT}},
        )
        self.assertEqual("current", result.status)
        self.assertIn("branch archive", result.detail)

    def test_unresolvable_ambiguous_ref_is_uncertain(self):
        result = audit(pkgbuild('"demo.tar.gz::$url/archive/stable.tar.gz"'))
        self.assertEqual("uncertain", result.status)

    def test_custom_tag_unrelated_to_pkgver_is_uncertain(self):
        text = pkgbuild(
            f'"demo-$pkgver-$_commit.tar.gz::$url/archive/$_commit.tar.gz" '
            f'"grammar.tar.gz::https://github.com/other/grammar/archive/refs/tags/grammar-2024.tar.gz"',
            extra=PIN,
        )
        result = audit(text, tags={REPO: {"v1.2.3": COMMIT}})
        self.assertEqual("uncertain", result.status)
        self.assertIn("other/grammar@grammar-2024", result.detail)

    def test_secondary_tag_archive_on_npm_package_is_uncertain(self):
        text = pkgbuild(
            '"https://registry.npmjs.org/demo/-/demo-$pkgver.tgz" '
            '"LICENSE.tar.gz::https://github.com/acme/demo-src/archive/refs/tags/v$pkgver.tar.gz"',
            url="https://www.npmjs.com/package/demo",
        )
        result = audit(text)
        self.assertEqual("npm", result.channel)
        self.assertEqual("uncertain", result.status)

    def test_unsupported_channel_still_reports_mutable_archive(self):
        result = audit(
            pkgbuild('"demo-$pkgver.tar.gz::$url/archive/refs/tags/v$pkgver.tar.gz"'),
            override={"channel": "gitea"},
        )
        self.assertEqual("untrackable", result.status)
        self.assertIn("mutable tag archive", result.detail)


class CommitPinTest(unittest.TestCase):
    def test_pin_matching_peeled_tag_is_current(self):
        result = audit(pkgbuild(PINNED_SOURCE, extra=PIN), tags={REPO: {"v1.2.3": COMMIT}})
        self.assertEqual("current", result.status)
        self.assertIn("matches tag v1.2.3", result.detail)

    def test_same_version_retag_is_outdated(self):
        result = audit(pkgbuild(PINNED_SOURCE, extra=PIN), tags={REPO: {"v1.2.3": MOVED_COMMIT}})
        self.assertEqual("outdated", result.status)
        self.assertIn(MOVED_COMMIT, result.detail)

    def test_newer_version_with_valid_pin_is_outdated(self):
        result = audit(
            pkgbuild(PINNED_SOURCE, extra=PIN),
            tags={REPO: {"v1.2.3": COMMIT}},
            http=fake_http(tags=("v1.2.3", "v1.3.0")),
        )
        self.assertEqual("outdated", result.status)
        # The commit URL carries no tag name; the prefix comes from the peeled tag.
        self.assertEqual("1.3.0", result.latest)

    def test_matching_alias_does_not_mask_moved_tag_family(self):
        # Both 1.2.3 and v1.2.3 exist; the bare family moved. Choosing the alias
        # that still matches the pin would hide the retag.
        result = audit(
            pkgbuild(PINNED_SOURCE, extra=PIN),
            tags={REPO: {"1.2.3": MOVED_COMMIT, "v1.2.3": COMMIT}},
            http=fake_http(tags=("1.2.3", "v1.2.3")),
        )
        self.assertEqual("outdated", result.status)
        self.assertIn("@1.2.3 now targets", result.detail)

    def test_broken_pin_stays_uncertain_when_newer_version_exists(self):
        result = audit(
            pkgbuild('"demo-$pkgver.tar.gz::$url/archive/refs/tags/v$pkgver.tar.gz"'),
            http=fake_http(tags=("v1.2.3", "v1.3.0")),
        )
        self.assertEqual("uncertain", result.status)

    def test_missing_version_tag_is_uncertain(self):
        result = audit(pkgbuild(PINNED_SOURCE, extra=PIN))
        self.assertEqual("uncertain", result.status)

    def test_cache_filename_without_commit_is_uncertain(self):
        result = audit(
            pkgbuild('"demo-$pkgver.tar.gz::$url/archive/$_commit.tar.gz"', extra=PIN),
            tags={REPO: {"v1.2.3": COMMIT}},
        )
        self.assertEqual("uncertain", result.status)
        self.assertIn("local file name", result.detail)

    def test_unrenamed_commit_archive_keeps_commit_identity(self):
        result = audit(pkgbuild('"$url/archive/$_commit.tar.gz"', extra=PIN), tags={REPO: {"v1.2.3": COMMIT}})
        self.assertEqual("current", result.status)

    def test_unused_or_abbreviated_commit_variable_is_uncertain(self):
        for extra, source in (
            (PIN, '"demo-$pkgver.tar.gz::$url/releases/download/v$pkgver/demo.tar.gz"'),
            (f"_commit={COMMIT[:12]}", '"demo-$pkgver-$_commit.tar.gz::$url/archive/$_commit.tar.gz"'),
        ):
            with self.subTest(extra=extra):
                result = audit(pkgbuild(source, extra=extra), tags={REPO: {"v1.2.3": COMMIT}})
                self.assertEqual("uncertain", result.status)

    def test_standalone_secondary_commit_archive_is_accepted(self):
        text = pkgbuild(
            f'{PINNED_SOURCE} "vendor-{MOVED_COMMIT}.tar.gz::https://github.com/other/vendor/archive/{MOVED_COMMIT}.tar.gz"',
            extra=PIN,
        )
        result = audit(text, tags={REPO: {"v1.2.3": COMMIT}})
        self.assertEqual("current", result.status)
        self.assertIn("standalone commit archive other/vendor", result.detail)

    def test_source_tags_maps_secondary_repo_retag(self):
        text = pkgbuild(
            f'{PINNED_SOURCE} "vendor-{MOVED_COMMIT}.tar.gz::https://github.com/other/vendor/archive/{MOVED_COMMIT}.tar.gz"',
            extra=PIN,
        )
        override = {"source_tags": {"other/vendor": "vendor-{version}"}}
        tags = {REPO: {"v1.2.3": COMMIT}, "other/vendor": {"vendor-1.2.3": "c" * 40}}
        result = audit(text, override=override, tags=tags)
        self.assertEqual("outdated", result.status)
        self.assertIn("other/vendor@vendor-1.2.3", result.detail)

    def test_head_pinned_channel_commit_archive_is_not_tag_checked(self):
        text = pkgbuild('"$url/archive/$_commit.tar.gz"', extra=PIN, pkgver="0.r5")
        recipe = recipe_from(text)
        target = check.classify(recipe, {"channel": "pinned-github-head", "repo": REPO})
        with mock.patch.object(check, "ls_remote", side_effect=AssertionError("resolver called")):
            status, detail = check.audit_archive_pins(recipe, target)
        self.assertEqual("", status)
        self.assertIn("standalone commit archive", detail)


BROKEN_PKGBUILD = """pkgname=demo
pkgver=1.0
pkgrel=1
arch=('x86_64' 'aarch64')
source_aarch64=(
  "demo-$pkgver-aarch64.tar.gz::https://example.invalid/demo-aarch64.tar.gz"
sha256sums=('abc')
"""


class MetadataParseFailureTest(unittest.TestCase):
    """A PKGBUILD bash cannot parse must fail extraction, never yield partial fields."""

    def write(self, root: Path, text: str) -> Path:
        path = root / "demo" / "PKGBUILD"
        path.parent.mkdir()
        path.write_text(text, encoding="utf-8")
        return path

    def test_mid_file_syntax_error_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self.write(Path(tmp), BROKEN_PKGBUILD)
            with self.assertRaises(check.AuditError) as caught:
                check.bash_metadata(path)
        # isolated_env pins LC_ALL=C.UTF-8, so bash reports in English.
        self.assertIn("syntax error", str(caught.exception))

    def test_valid_pkgbuild_still_returns_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self.write(Path(tmp), pkgbuild('"$url/archive/v$pkgver.tar.gz"'))
            fields = check.bash_metadata(path)
        self.assertEqual(["1.2.3"], fields["pkgver"])
        self.assertEqual(["SKIP"], fields["sha256sums"])

    def test_extract_metadata_records_error_and_audits_uncertain(self):
        real_which = check.shutil.which

        def which(name, *args, **kwargs):
            return None if name == "makepkg" else real_which(name, *args, **kwargs)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = self.write(root, BROKEN_PKGBUILD)
            with mock.patch.object(check.shutil, "which", side_effect=which):
                recipe = check.extract_metadata(path, root)
        self.assertEqual({}, recipe.fields)
        self.assertEqual("", recipe.pkgver)
        self.assertIn("syntax error", recipe.metadata_error)
        target = check.classify(recipe, {"channel": "github", "repo": REPO})
        result = check.audit_channel(recipe, target, fake_http())
        self.assertEqual("uncertain", result.status)
        self.assertIn("syntax error", result.detail)


if __name__ == "__main__":
    unittest.main()
