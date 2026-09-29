#!/usr/bin/env python3
"""Deterministic tests for the forge pinned-source audit contract.

Run: python3 -B -m unittest discover -s tests -v
No network: HttpClient responses and tag resolution are stubbed.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT / ".agents/skills/aur-package-fleet-maintenance"))

import check

REPO = "stablyai/orca"
COMMIT = "a" * 40
MOVED_COMMIT = "b" * 40
PKGBUILD_TEMPLATE = """pkgname=demo
pkgver={pkgver}
pkgrel=1
url='https://github.com/{repo}'
arch=('x86_64')
_commit={pin}
source=("demo-$pkgver.tar.gz::$url/archive/{ref}.tar.gz")
sha256sums=('SKIP')
"""


def make_recipe(pkgver="1.4.216", pin=COMMIT, ref=None, url_var=None):
    ref = COMMIT if ref is None else ref
    raw = PKGBUILD_TEMPLATE.format(
        pkgver=pkgver, repo=REPO, pin=pin, ref=url_var or ref
    )
    source_url = f"https://github.com/{REPO}/archive/{ref}.tar.gz"
    fields = {
        "pkgname": ["demo"],
        "pkgbase": ["demo"],
        "pkgver": [pkgver],
        "pkgrel": ["1"],
        "url": [f"https://github.com/{REPO}"],
        "arch": ["x86_64"],
        "source": [f"demo-{pkgver}.tar.gz::{source_url}"],
        "sha256sums": ["SKIP"],
    }
    return check.Recipe(
        path=ROOT / "demo/PKGBUILD",
        relative_path="demo",
        pkgbase="demo",
        pkgnames=["demo"],
        pkgver=pkgver,
        pkgrel="1",
        url=f"https://github.com/{REPO}",
        arch=["x86_64"],
        fields=fields,
        raw=raw,
        extractor="test",
    )


def make_target(pinned_var="_commit"):
    return check.Target(
        channel="github", repo=REPO, tag_prefix="v", selection="tag",
        pinned_var=pinned_var,
    )


def fake_http(releases=(), tags=()):
    http = mock.Mock(spec=check.HttpClient)

    def get_paginated_json(url, page_size):
        if "/releases?" in url:
            return list(releases)
        if "/tags?" in url:
            return [{"name": name} for name in tags]
        raise AssertionError(f"unexpected URL {url}")

    http.get_paginated_json.side_effect = get_paginated_json
    return http


def resolved_tag(version, commit):
    return {f"v{version}": commit}


class PinnedArchiveRefTest(unittest.TestCase):
    def test_commit_archive_ref_detected(self):
        self.assertEqual(
            COMMIT, check.pinned_archive_ref(make_recipe(), make_target())
        )

    def test_tag_archive_has_no_commit_ref(self):
        recipe = make_recipe(ref="refs/tags/v1.4.216")
        self.assertEqual("", check.pinned_archive_ref(recipe, make_target()))

    def test_nested_tag_path_named_like_commit_rejected(self):
        recipe = make_recipe(ref="refs/tags/archive/" + COMMIT)
        self.assertEqual("", check.pinned_archive_ref(recipe, make_target()))

    def test_foreign_repo_source_ignored(self):
        recipe = make_recipe()
        recipe.fields["source"].append(
            "other.tar.gz::https://github.com/other/proj/archive/"
            + "c" * 40
            + ".tar.gz"
        )
        self.assertEqual(
            COMMIT, check.pinned_archive_ref(recipe, make_target())
        )


class LsRemoteTagCommitsTest(unittest.TestCase):
    def test_annotated_tag_peels_to_commit(self):
        output = (
            "2ec06f247084093fc237492614fcf54aa79255e2\trefs/tags/v1.4.216\n"
            "20d7a7d185cd66e993dcdfd60e9e604fe26e9c40\trefs/tags/v1.4.216^{}\n"
        )
        self.assertEqual(
            {"v1.4.216": "20d7a7d185cd66e993dcdfd60e9e604fe26e9c40"},
            check.ls_remote_tag_commits(output),
        )

    def test_lightweight_tag_resolves_to_ref(self):
        output = "083f583a53e4c74a65acf420eee4ca2e0efa9df1\trefs/tags/v1.4.215\n"
        self.assertEqual(
            {"v1.4.215": "083f583a53e4c74a65acf420eee4ca2e0efa9df1"},
            check.ls_remote_tag_commits(output),
        )


class AuditForgePinnedTest(unittest.TestCase):
    """End-to-end audit_forge classification over the pin state machine."""

    def audit(self, recipe, commits, tags=("v1.4.216",), releases=()):
        target = make_target()
        http = fake_http(releases=releases, tags=tags)
        with mock.patch.object(
            check, "resolve_tag_commits", return_value=commits
        ):
            return check.audit_forge(recipe, target, http)

    def test_matching_pin_is_current(self):
        result = self.audit(
            make_recipe(), resolved_tag("1.4.216", COMMIT)
        )
        self.assertEqual("current", result.status)
        self.assertIn(COMMIT, result.detail)

    def test_same_version_retag_is_outdated(self):
        result = self.audit(
            make_recipe(), resolved_tag("1.4.216", MOVED_COMMIT)
        )
        self.assertEqual("outdated", result.status)
        self.assertIn(MOVED_COMMIT, result.detail)

    def test_tag_archive_source_is_uncertain(self):
        recipe = make_recipe(ref="refs/tags/v1.4.216", url_var="refs/tags/v1.4.216")
        result = self.audit(recipe, resolved_tag("1.4.216", COMMIT))
        self.assertEqual("uncertain", result.status)
        self.assertIn("archive", result.detail)

    def test_missing_pin_variable_is_uncertain(self):
        recipe = make_recipe()
        recipe.raw = recipe.raw.replace("_commit=", "_gone=")
        result = self.audit(recipe, resolved_tag("1.4.216", COMMIT))
        self.assertEqual("uncertain", result.status)

    def test_pin_source_divergence_is_uncertain(self):
        recipe = make_recipe(pin=COMMIT, ref=MOVED_COMMIT)
        result = self.audit(recipe, resolved_tag("1.4.216", COMMIT))
        self.assertEqual("uncertain", result.status)
        self.assertIn("differs", result.detail)

    def test_missing_upstream_tag_is_uncertain(self):
        result = self.audit(make_recipe(), {})
        self.assertEqual("uncertain", result.status)
        self.assertIn("no upstream tag", result.detail)

    def test_newer_version_still_outdated(self):
        result = self.audit(
            make_recipe(),
            resolved_tag("1.4.216", COMMIT),
            tags=("v1.4.216", "v1.4.217"),
        )
        self.assertEqual("outdated", result.status)
        self.assertEqual("1.4.217", result.latest)

    def test_newer_version_with_moved_tag_is_outdated(self):
        result = self.audit(
            make_recipe(),
            resolved_tag("1.4.216", MOVED_COMMIT),
            tags=("v1.4.216", "v1.4.217"),
        )
        self.assertEqual("outdated", result.status)
        self.assertIn("re-pin", result.detail)

    def test_newer_version_with_tag_archive_source_is_uncertain(self):
        recipe = make_recipe(ref="refs/tags/v1.4.216")
        result = self.audit(
            recipe,
            resolved_tag("1.4.216", COMMIT),
            tags=("v1.4.216", "v1.4.217"),
        )
        self.assertEqual("uncertain", result.status)
        self.assertEqual("1.4.217", result.latest)

    def test_unpinned_recipe_skips_verification(self):
        target = make_target(pinned_var="")
        http = fake_http(tags=("v1.4.216",))
        with mock.patch.object(
            check, "resolve_tag_commits", side_effect=AssertionError("resolver called")
        ):
            result = check.audit_forge(make_recipe(), target, http)
        self.assertEqual("current", result.status)


if __name__ == "__main__":
    unittest.main()
