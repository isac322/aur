#!/usr/bin/env python3
"""Discover AUR recipes and audit their upstream versions without a roster file."""

from __future__ import annotations

import argparse
import concurrent.futures
import contextlib
import dataclasses
import datetime as dt
import fnmatch
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import threading
from typing import Any, Iterable, Mapping, Sequence
from urllib.error import HTTPError, URLError
from urllib.parse import quote, unquote, urlparse
from urllib.request import Request, urlopen

try:
    import tomllib
except ModuleNotFoundError as exc:  # pragma: no cover - Python < 3.11
    raise SystemExit("check.py requires Python 3.11 or newer") from exc


STATUSES = {
    "current",
    "outdated",
    "uncertain",
    "untrackable",
    "intentionally_held",
    "unmapped",
}
BAD_STATUSES = {"outdated", "uncertain", "untrackable", "unmapped"}
PRUNED_DIRS = {
    ".git",
    ".hg",
    ".svn",
    ".cache",
    ".mypy_cache",
    ".pytest_cache",
    ".tox",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "node_modules",
    "pkg",
    "src",
    "target",
    "vendor",
    "venv",
}
CHECKSUM_KEYS = ("sha256sums", "sha512sums", "b2sums", "md5sums")
ARCH_KEYS = ("x86_64", "aarch64", "armv7h", "armv6h", "i686", "pentium4")
USER_AGENT = "aur-package-fleet-maintenance-check/1"


class AuditError(RuntimeError):
    """An expected discovery, metadata, upstream, or configuration failure."""


@dataclasses.dataclass
class Recipe:
    path: Path
    relative_path: str
    pkgbase: str
    pkgnames: list[str]
    pkgver: str
    pkgrel: str
    url: str
    arch: list[str]
    fields: dict[str, list[str]]
    raw: str
    extractor: str
    metadata_error: str = ""

    def values(self, prefix: str) -> list[tuple[str, str]]:
        result: list[tuple[str, str]] = []
        for key, values in self.fields.items():
            if key == prefix or key.startswith(prefix + "_"):
                result.extend((key, value) for value in values)
        return result

    def sources(self) -> list[tuple[str, str]]:
        ordered: list[tuple[str, str]] = []
        if "source" in self.fields:
            ordered.extend(("source", value) for value in self.fields["source"])
        for key in sorted(self.fields):
            if key.startswith("source_"):
                ordered.extend((key, value) for value in self.fields[key])
        return ordered


@dataclasses.dataclass
class Target:
    channel: str
    name: str = ""
    repo: str = ""
    tag_prefix: str = ""
    selection: str = "published"
    selection_explicit: bool = False
    required_assets: list[str] = dataclasses.field(default_factory=list)
    required_assets_explicit: bool = False
    index_url: str = ""
    file_regex: str = ""
    pinned_var: str = ""
    source_url: str = ""
    hold_reason: str = ""
    notes: list[str] = dataclasses.field(default_factory=list)


@dataclasses.dataclass
class Result:
    pkgbase: str
    path: str
    pkgnames: list[str]
    channel: str
    current: str
    latest: str
    status: str
    upstream: str
    detail: str = ""
    required_assets: list[str] = dataclasses.field(default_factory=list)
    missing_assets: list[str] = dataclasses.field(default_factory=list)
    extractor: str = ""
    duplicate_paths: list[str] = dataclasses.field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)

class HttpClient:
    def __init__(self, timeout: float = 30.0) -> None:
        self.timeout = timeout
        self._cache: dict[str, bytes] = {}
        self._lock = threading.Lock()
        self._url_locks: dict[str, threading.Lock] = {}
        self._github_token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or self._token_from_gh()

    @staticmethod
    def _token_from_gh() -> str:
        gh = shutil.which("gh")
        if not gh:
            return ""
        process = subprocess.run(
            [gh, "auth", "token"],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        return process.stdout.strip() if process.returncode == 0 else ""

    def get_bytes(self, url: str) -> bytes:
        with self._lock:
            url_lock = self._url_locks.setdefault(url, threading.Lock())
        with url_lock:
            with self._lock:
                cached = self._cache.get(url)
            if cached is not None:
                return cached
            headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
            if self._github_token and urlparse(url).hostname == "api.github.com":
                headers["Authorization"] = f"Bearer {self._github_token}"
            request = Request(url, headers=headers)
            try:
                with urlopen(request, timeout=self.timeout) as response:
                    payload = response.read()
            except HTTPError as exc:
                raise AuditError(f"HTTP {exc.code} from {url}") from exc
            except URLError as exc:
                raise AuditError(f"request failed for {url}: {exc.reason}") from exc
            with self._lock:
                self._cache[url] = payload
            return payload

    def get_json(self, url: str) -> Any:
        try:
            return json.loads(self.get_bytes(url))
        except json.JSONDecodeError as exc:
            raise AuditError(f"invalid JSON from {url}: {exc}") from exc


    def get_paginated_json(self, url: str, page_size: int) -> list[Any]:
        records: list[Any] = []
        page = 1
        separator = "&" if "?" in url else "?"
        while True:
            payload = self.get_json(f"{url}{separator}page={page}")
            if not isinstance(payload, list):
                raise AuditError(f"paginated response is not a list: {url}")
            records.extend(payload)
            if len(payload) < page_size:
                return records
            page += 1
            if page > 1000:
                raise AuditError(f"pagination exceeded 1000 pages: {url}")

    def digest(self, url: str, algorithm: str) -> str:
        request = Request(url, headers={"User-Agent": USER_AGENT})
        try:
            digest = hashlib.new(algorithm)
        except ValueError as exc:
            raise AuditError(f"unsupported checksum algorithm: {algorithm}") from exc
        try:
            with urlopen(request, timeout=self.timeout) as response:
                while chunk := response.read(1024 * 1024):
                    digest.update(chunk)
        except HTTPError as exc:
            raise AuditError(f"HTTP {exc.code} from {url}") from exc
        except URLError as exc:
            raise AuditError(f"request failed for {url}: {exc.reason}") from exc
        return digest.hexdigest()


def isolated_env(home: Path, **extra: str) -> dict[str, str]:
    env = {
        "HOME": str(home),
        "TMPDIR": str(home),
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "CARCH": "x86_64",
        "CHOST": "x86_64-pc-linux-gnu",
        "MAKEFLAGS": "",
        "BASH_ENV": "/dev/null",
    }
    env.update(extra)
    return env


def discover_pkgbuilds(root: Path) -> list[Path]:
    found: list[Path] = []
    for directory, dirnames, filenames in os.walk(root, topdown=True, followlinks=False):
        dirnames[:] = sorted(name for name in dirnames if name not in PRUNED_DIRS)
        if "PKGBUILD" in filenames:
            found.append(Path(directory) / "PKGBUILD")
    return sorted(found, key=lambda path: path.relative_to(root).as_posix())


def parse_srcinfo(text: str) -> dict[str, list[str]]:
    fields: dict[str, list[str]] = {}
    for line in text.splitlines():
        match = re.match(r"^\s*([A-Za-z0-9_]+)\s*=\s*(.*)$", line)
        if match:
            fields.setdefault(match.group(1), []).append(match.group(2))
    return fields


def bash_metadata(pkgbuild: Path) -> dict[str, list[str]]:
    bash = shutil.which("bash")
    if not bash:
        raise AuditError("bash is unavailable for isolated PKGBUILD metadata fallback")
    script = r'''
set -o pipefail
emit_var() {
    local name=$1 decl value
    decl=$(declare -p "$name" 2>/dev/null) || return 0
    case "$decl" in
        "declare -a"*|"declare -A"*)
            eval 'for value in "${'"$name"'[@]}"; do printf "%s\0%s\0" "$name" "$value"; done'
            ;;
        *)
            value=${!name}
            printf "%s\0%s\0" "$name" "$value"
            ;;
    esac
}
source "$1" >/dev/null
for name in pkgbase pkgname pkgver pkgrel url arch; do emit_var "$name"; done
while IFS= read -r name; do
    case "$name" in
        source|source_*|sha256sums|sha256sums_*|sha512sums|sha512sums_*|b2sums|b2sums_*|md5sums|md5sums_*)
            emit_var "$name"
            ;;
    esac
done < <(compgen -A variable | LC_ALL=C sort -u)
'''
    with tempfile.TemporaryDirectory(prefix="aur-check-meta-") as temporary:
        sandbox = Path(temporary)
        process = subprocess.run(
            [bash, "--noprofile", "--norc", "-c", script, "bash", str(pkgbuild.resolve())],
            cwd=sandbox,
            env=isolated_env(sandbox),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
    if process.returncode:
        error = process.stderr.decode("utf-8", "replace").strip()
        raise AuditError(f"isolated bash metadata extraction failed: {error or 'exit ' + str(process.returncode)}")
    parts = process.stdout.split(b"\0")
    if parts and parts[-1] == b"":
        parts.pop()
    if len(parts) % 2:
        raise AuditError("isolated bash metadata output was truncated")
    fields: dict[str, list[str]] = {}
    for index in range(0, len(parts), 2):
        key = parts[index].decode("utf-8", "replace")
        value = parts[index + 1].decode("utf-8", "replace")
        fields.setdefault(key, []).append(value)
    return fields


def extract_metadata(pkgbuild: Path, root: Path) -> Recipe:
    raw = pkgbuild.read_text(encoding="utf-8")
    fields: dict[str, list[str]]
    extractor = "bash"
    metadata_error = ""
    makepkg = shutil.which("makepkg")
    if makepkg:
        process = subprocess.run(
            [makepkg, "--printsrcinfo"],
            cwd=pkgbuild.parent,
            env={**os.environ, "LC_ALL": "C.UTF-8"},
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        if process.returncode == 0:
            fields = parse_srcinfo(process.stdout)
            extractor = "makepkg"
        else:
            try:
                fields = bash_metadata(pkgbuild)
                extractor = "bash-after-makepkg-error"
                metadata_error = process.stderr.strip()
            except AuditError as fallback_error:
                fields = {}
                metadata_error = f"makepkg failed; {fallback_error}"
    else:
        try:
            fields = bash_metadata(pkgbuild)
        except AuditError as exc:
            fields = {}
            metadata_error = str(exc)

    relative = pkgbuild.parent.relative_to(root).as_posix()
    pkgnames = unique(fields.get("pkgname", []))
    pkgbase = first(fields, "pkgbase") or (pkgnames[0] if pkgnames else pkgbuild.parent.name)
    return Recipe(
        path=pkgbuild,
        relative_path=relative,
        pkgbase=pkgbase,
        pkgnames=pkgnames or [pkgbase],
        pkgver=first(fields, "pkgver"),
        pkgrel=first(fields, "pkgrel"),
        url=first(fields, "url"),
        arch=unique(fields.get("arch", [])),
        fields=fields,
        raw=raw,
        extractor=extractor,
        metadata_error=metadata_error,
    )


def first(fields: Mapping[str, Sequence[str]], key: str) -> str:
    values = fields.get(key, [])
    return values[0] if values else ""


def unique(values: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))


def source_url(value: str) -> str:
    value = value.split("::", 1)[-1]
    return value.strip()


def clean_vcs_url(value: str) -> str:
    value = source_url(value)
    for prefix in ("git+", "hg+", "svn+", "bzr+"):
        if value.startswith(prefix):
            value = value[len(prefix) :]
            break
    return value.split("#", 1)[0]


def github_repo(value: str) -> str:
    value = clean_vcs_url(value)
    parsed = urlparse(value)
    host = (parsed.hostname or "").lower()
    if host not in {"github.com", "www.github.com", "raw.githubusercontent.com"}:
        return ""
    parts = [unquote(part) for part in parsed.path.strip("/").split("/") if part]
    if len(parts) < 2:
        return ""
    repo = parts[1]
    if repo.endswith(".git"):
        repo = repo[:-4]
    return f"{parts[0]}/{repo}"


def codeberg_repo(value: str) -> str:
    value = clean_vcs_url(value)
    parsed = urlparse(value)
    if (parsed.hostname or "").lower() not in {"codeberg.org", "www.codeberg.org"}:
        return ""
    parts = [unquote(part) for part in parsed.path.strip("/").split("/") if part]
    if len(parts) < 2:
        return ""
    repo = parts[1][:-4] if parts[1].endswith(".git") else parts[1]
    return f"{parts[0]}/{repo}"


def primary_remote(recipe: Recipe) -> str:
    remotes = [source_url(value) for _, value in recipe.sources() if is_remote_source(value)]
    for value in remotes:
        if recipe.pkgver and recipe.pkgver in unquote(value):
            return value
    return remotes[0] if remotes else ""


def is_remote_source(value: str) -> bool:
    value = source_url(value)
    return bool(re.match(r"^(?:git\+|hg\+|svn\+|bzr\+)?https?://", value))


def npm_name(value: str) -> str:
    parsed = urlparse(source_url(value))
    host = (parsed.hostname or "").lower()
    parts = [unquote(part) for part in parsed.path.strip("/").split("/") if part]
    if host == "registry.npmjs.org" and parts:
        if parts[0].startswith("@") and len(parts) >= 2:
            return f"{parts[0]}/{parts[1]}"
        return parts[0]
    if host in {"npmjs.com", "www.npmjs.com"} and len(parts) >= 2 and parts[0] == "package":
        if parts[1].startswith("@") and len(parts) >= 3:
            return f"{parts[1]}/{parts[2]}"
        return parts[1]
    return ""


def pypi_name(value: str) -> str:
    parsed = urlparse(source_url(value))
    host = (parsed.hostname or "").lower()
    parts = [unquote(part) for part in parsed.path.strip("/").split("/") if part]
    if host in {"pypi.org", "www.pypi.org"} and len(parts) >= 2 and parts[0] in {"project", "pypi"}:
        return parts[1]
    if host == "files.pythonhosted.org":
        with contextlib.suppress(ValueError, IndexError):
            index = parts.index("source")
            return parts[index + 2].replace("_", "-")
    return ""


def has_pkgver_function(recipe: Recipe) -> bool:
    return bool(re.search(r"(?m)^\s*pkgver\s*\(\s*\)\s*\{", recipe.raw))


def moving_vcs_source(recipe: Recipe) -> str:
    if not has_pkgver_function(recipe):
        return ""
    for _, value in recipe.sources():
        remote = source_url(value)
        if re.match(r"^(?:git|hg|svn|bzr)\+", remote) and not re.search(r"#(?:tag|commit)=", remote):
            return value
    return ""


def shell_assignment(raw: str, name: str) -> str:
    pattern = rf"(?m)^\s*{re.escape(name)}\s*=\s*(?:'([^']*)'|\"([^\"]*)\"|([^\s#]+))"
    match = re.search(pattern, raw)
    if not match:
        return ""
    return next((group for group in match.groups() if group is not None), "")


def infer_tag_prefix(recipe: Recipe, repo: str) -> str:
    candidates: list[str] = []
    for _, value in recipe.sources():
        remote = unquote(source_url(value))
        if github_repo(remote) != repo and codeberg_repo(remote) != repo:
            continue
        patterns = (
            r"/releases/download/([^/]+)/",
            r"/archive/refs/tags/([^/]+?)(?:\.tar\.(?:gz|xz|bz2|zst)|\.zip|$)",
            r"/archive/([^/]+?)(?:\.tar\.(?:gz|xz|bz2|zst)|\.zip|/)",
            r"#tag=([^&#]+)",
        )
        for pattern in patterns:
            match = re.search(pattern, remote)
            if match:
                candidates.append(match.group(1))
        parsed = urlparse(remote)
        if (parsed.hostname or "").lower() == "raw.githubusercontent.com":
            parts = parsed.path.strip("/").split("/")
            if len(parts) >= 3:
                candidates.append(parts[2])
    for tag in candidates:
        if recipe.pkgver and recipe.pkgver in tag:
            return tag.split(recipe.pkgver, 1)[0]
    return ""


def infer_required_assets(recipe: Recipe, repo: str) -> list[str]:
    assets: list[str] = []
    for key, value in recipe.sources():
        remote = unquote(source_url(value))
        if "/releases/download/" not in remote or github_repo(remote) != repo:
            continue
        name = Path(urlparse(remote).path).name
        if not name:
            continue
        if recipe.pkgver:
            name = name.replace(recipe.pkgver, "{version}")
        assets.append(name)
    return unique(assets)


def load_overrides(path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    with path.open("rb") as stream:
        data = tomllib.load(stream)
    packages = data.get("packages", {})
    if not isinstance(packages, dict):
        raise AuditError("audit-overrides.toml: [packages] must be a table")
    result: dict[str, dict[str, Any]] = {}
    for pkgbase, value in packages.items():
        if not isinstance(value, dict):
            raise AuditError(f"audit-overrides.toml: packages.{pkgbase} must be a table")
        result[str(pkgbase)] = dict(value)
    return result


def classify(recipe: Recipe, override: Mapping[str, Any]) -> Target:
    hold_reason = str(override.get("hold_reason", ""))
    explicit_channel = str(override.get("channel", ""))
    if explicit_channel:
        target = Target(channel=explicit_channel, hold_reason=hold_reason)
    else:
        target = Target(channel="", hold_reason=hold_reason)

    target.name = str(override.get("package_name", ""))
    target.repo = str(override.get("repo", ""))
    target.selection = str(override.get("selection", "published"))
    target.selection_explicit = "selection" in override
    target.tag_prefix = str(override.get("tag_prefix", ""))
    target.required_assets = [str(item) for item in override.get("required_assets", [])]
    target.required_assets_explicit = "required_assets" in override
    target.index_url = str(override.get("index_url", ""))
    target.file_regex = str(override.get("file_regex", ""))
    target.pinned_var = str(override.get("pinned_var", ""))
    target.source_url = str(override.get("source_url", ""))

    moving = moving_vcs_source(recipe)
    primary = primary_remote(recipe)
    candidates = [primary, recipe.url] + [source_url(value) for _, value in recipe.sources()]
    distribution_candidates = unique([primary, recipe.url])

    if not target.channel and moving:
        target.channel = "vcs"
        target.source_url = moving

    if not target.channel:
        for value in distribution_candidates:
            name = npm_name(value)
            if name:
                target.channel = "npm"
                target.name = target.name or name
                break

    if not target.channel:
        for value in distribution_candidates:
            name = pypi_name(value)
            if name:
                target.channel = "pypi"
                target.name = target.name or name
                break

    if not target.repo:
        for value in candidates:
            target.repo = github_repo(value)
            if target.repo:
                break
    if not target.channel and target.repo:
        target.channel = "github"

    if not target.repo:
        for value in candidates:
            target.repo = codeberg_repo(value)
            if target.repo:
                break
    if not target.channel and target.repo:
        target.channel = "codeberg"

    if not target.channel and primary and recipe.pkgver not in unquote(primary):
        target.channel = "static-checksum"
        target.source_url = primary

    if target.channel in {"github", "codeberg"}:
        target.tag_prefix = target.tag_prefix or infer_tag_prefix(recipe, target.repo)
    if target.channel == "github" and not target.required_assets:
        target.required_assets = infer_required_assets(recipe, target.repo)
    if target.channel == "static-checksum":
        target.source_url = target.source_url or primary
    if target.channel == "pinned-github-head":
        target.pinned_var = target.pinned_var or "_commit"
        if not target.repo:
            for value in candidates:
                target.repo = github_repo(value)
                if target.repo:
                    break
    return target


def natural_key(value: str) -> tuple[tuple[int, Any], ...]:
    value = value.strip().lstrip("vV")
    tokens = re.findall(r"\d+|[A-Za-z]+", value)
    return tuple((1, int(token)) if token.isdigit() else (0, token.lower()) for token in tokens)


def is_stable_tag(version: str) -> bool:
    return not re.search(r"(?i)(?:^|[._-])(alpha|beta|rc|pre|preview|dev|nightly|snapshot)(?:[._-]|\d|$)", version)


def compare_versions(current: str, latest: str) -> int | None:
    if current == latest:
        return 0
    current_key = natural_key(current)
    latest_key = natural_key(latest)
    if not current_key or not latest_key:
        return None
    if current_key == latest_key:
        return 0
    return -1 if current_key < latest_key else 1


def result_status(current: str, latest: str, hold_reason: str = "") -> tuple[str, str]:
    comparison = compare_versions(current, latest)
    if comparison == 0:
        return "current", ""
    if comparison == -1:
        if hold_reason:
            return "intentionally_held", hold_reason
        return "outdated", ""
    if comparison == 1:
        return "uncertain", "packaged version sorts after upstream"
    return "uncertain", "versions are not comparable"


def published_key(release: Mapping[str, Any]) -> tuple[str, tuple[tuple[int, Any], ...]]:
    published = str(release.get("published_at") or "")
    tag = str(release.get("tag_name") or release.get("tag") or "")
    return published, natural_key(tag)


def release_assets(release: Mapping[str, Any]) -> list[str]:
    names: list[str] = []
    for asset in release.get("assets", []) or []:
        if isinstance(asset, Mapping):
            name = asset.get("name")
            if name:
                names.append(str(name))
    return names


def render_asset_patterns(patterns: Sequence[str], version: str, tag: str) -> list[str]:
    rendered: list[str] = []
    for pattern in patterns:
        try:
            rendered.append(pattern.format(version=version, tag=tag))
        except (KeyError, ValueError) as exc:
            raise AuditError(f"invalid required asset pattern {pattern!r}: {exc}") from exc
    return rendered


def missing_assets(required: Sequence[str], available: Sequence[str]) -> list[str]:
    return [pattern for pattern in required if not any(fnmatch.fnmatchcase(name, pattern) for name in available)]


def audit_forge(recipe: Recipe, target: Target, http: HttpClient) -> Result:
    if not target.repo:
        raise AuditError(f"{target.channel} repository could not be inferred")
    if target.selection not in {"published", "version"}:
        raise AuditError(f"unsupported release selection: {target.selection}")

    prefix = target.tag_prefix
    if target.channel == "github":
        base = f"https://api.github.com/repos/{target.repo}"
        releases = http.get_paginated_json(base + "/releases?per_page=100", 100)
        tag_endpoint = "/tags?per_page=100"
        tag_page_size = 100
    else:
        base = f"https://codeberg.org/api/v1/repos/{target.repo}"
        releases = http.get_paginated_json(base + "/releases?limit=50", 50)
        tag_endpoint = "/tags?limit=50"
        tag_page_size = 50

    matching_releases = [
        release
        for release in releases
        if isinstance(release, Mapping)
        and not release.get("draft", False)
        and not release.get("prerelease", False)
        and str(release.get("tag_name") or release.get("tag") or "").startswith(prefix)
        and is_stable_tag(
            str(release.get("tag_name") or release.get("tag") or "")[len(prefix) :]
        )
    ]

    def release_version(candidate: Mapping[str, Any]) -> str:
        tag_name = str(candidate.get("tag_name") or candidate.get("tag") or "")
        return tag_name[len(prefix) :]

    published_releases = sorted(matching_releases, key=published_key, reverse=True)
    version_releases = sorted(
        matching_releases,
        key=lambda candidate: natural_key(release_version(candidate)),
        reverse=True,
    )
    ordered_releases = version_releases if target.selection == "version" else published_releases

    release: Mapping[str, Any] | None = None
    required: list[str] = []
    missing: list[str] = []
    newer_incomplete: tuple[str, list[str]] | None = None
    source_kind = "release"
    if ordered_releases:
        if target.required_assets:
            for candidate in ordered_releases:
                candidate_tag = str(candidate.get("tag_name") or candidate.get("tag") or "")
                candidate_version = candidate_tag[len(prefix) :]
                candidate_required = render_asset_patterns(
                    target.required_assets, candidate_version, candidate_tag
                )
                candidate_missing = missing_assets(candidate_required, release_assets(candidate))
                if not candidate_missing:
                    release = candidate
                    required = candidate_required
                    break
                if newer_incomplete is None:
                    newer_incomplete = (candidate_version, candidate_missing)
            if release is None:
                release = ordered_releases[0]
        else:
            release = ordered_releases[0]

    if release is not None:
        tag = str(release.get("tag_name") or release.get("tag") or "")
        latest = tag[len(prefix) :]
        available_assets = release_assets(release)
        if not required:
            required = render_asset_patterns(target.required_assets, latest, tag)
        missing = missing_assets(required, available_assets)
    else:
        source_kind = "tag"
        tags = http.get_paginated_json(base + tag_endpoint, tag_page_size)
        names = [
            str(item.get("name", ""))
            for item in tags
            if isinstance(item, Mapping)
            and str(item.get("name", "")).startswith(prefix)
            and is_stable_tag(str(item.get("name", ""))[len(prefix) :])
        ]
        if not names:
            raise AuditError(f"no stable release or tag matches prefix {prefix!r}")
        tag = max(names, key=lambda name: natural_key(name[len(prefix) :]))
        latest = tag[len(prefix) :]
        available_assets = []
        required = render_asset_patterns(target.required_assets, latest, tag)
        missing = missing_assets(required, available_assets)

    status, detail = result_status(recipe.pkgver, latest, target.hold_reason)
    if missing:
        status = "uncertain"
        detail = f"latest {source_kind} is missing required assets: {', '.join(missing)}"
    elif required and source_kind == "tag":
        status = "uncertain"
        detail = "only a tag was found, so required release assets cannot be verified"
    elif newer_incomplete and ordered_releases and release is not ordered_releases[0]:
        newer_version, newer_missing = newer_incomplete
        detail = (
            f"newer release {newer_version} is missing required assets "
            f"({', '.join(newer_missing)}); newest compatible release is {latest}"
        )
        if target.required_assets_explicit:
            if status == "current":
                status = "intentionally_held"
        else:
            status = "uncertain"
            detail += "; inferred asset names require review before holding the package"
    elif (
        not target.required_assets
        and not target.selection_explicit
        and published_releases
        and version_releases
        and published_releases[0] is not version_releases[0]
        and compare_versions(
            release_version(published_releases[0]), release_version(version_releases[0])
        )
        == -1
    ):
        status = "uncertain"
        detail = (
            f"newest published release {release_version(published_releases[0])} sorts before "
            f"highest released version {release_version(version_releases[0])}; "
            "declare selection in audit-overrides.toml"
        )

    return Result(
        pkgbase=recipe.pkgbase,
        path=recipe.relative_path,
        pkgnames=recipe.pkgnames,
        channel=target.channel,
        current=recipe.pkgver,
        latest=latest,
        status=status,
        upstream=f"{target.channel}:{target.repo}",
        detail=detail,
        required_assets=required,
        missing_assets=missing,
        extractor=recipe.extractor,
    )


def audit_npm(recipe: Recipe, target: Target, http: HttpClient) -> Result:
    if not target.name:
        raise AuditError("npm package name could not be inferred")
    data = http.get_json(f"https://registry.npmjs.org/{quote(target.name, safe='@')}/latest")
    latest = str(data.get("version", "")) if isinstance(data, Mapping) else ""
    if not latest:
        raise AuditError("npm latest response has no version")
    status, detail = result_status(recipe.pkgver, latest, target.hold_reason)
    return Result(recipe.pkgbase, recipe.relative_path, recipe.pkgnames, "npm", recipe.pkgver, latest, status, f"npm:{target.name}", detail, extractor=recipe.extractor)


def audit_pypi(recipe: Recipe, target: Target, http: HttpClient) -> Result:
    if not target.name:
        raise AuditError("PyPI project name could not be inferred")
    data = http.get_json(f"https://pypi.org/pypi/{quote(target.name, safe='')}/json")
    info = data.get("info", {}) if isinstance(data, Mapping) else {}
    latest = str(info.get("version", "")) if isinstance(info, Mapping) else ""
    if not latest:
        raise AuditError("PyPI response has no info.version")
    status, detail = result_status(recipe.pkgver, latest, target.hold_reason)
    return Result(recipe.pkgbase, recipe.relative_path, recipe.pkgnames, "pypi", recipe.pkgver, latest, status, f"pypi:{target.name}", detail, extractor=recipe.extractor)


def vcs_parts(value: str) -> tuple[str, str, str]:
    original = source_url(value)
    alias = ""
    if "::" in value:
        alias = value.split("::", 1)[0].strip()
    fragment = urlparse(original).fragment
    parameters = dict(part.split("=", 1) for part in fragment.split("&") if "=" in part)
    url = clean_vcs_url(value)
    name = alias or Path(urlparse(url).path).name.removesuffix(".git")
    branch = parameters.get("branch", "")
    return url, name, branch


def compute_vcs_version(recipe: Recipe, target: Target) -> tuple[str, str]:
    git = shutil.which("git")
    bash = shutil.which("bash")
    if not git or not bash:
        raise AuditError("git and bash are required for VCS pkgver calculation")
    url, directory_name, branch = vcs_parts(target.source_url)
    if not url or not directory_name:
        raise AuditError("VCS source URL could not be parsed")
    with tempfile.TemporaryDirectory(prefix="aur-check-vcs-") as temporary:
        root = Path(temporary)
        destination = root / directory_name
        command = [git, "clone", "--quiet", "--filter=blob:none", "--no-checkout"]
        if branch:
            command.extend(["--single-branch", "--branch", branch])
        command.extend([url, str(destination)])
        clone = subprocess.run(
            command,
            cwd=root,
            env=isolated_env(root),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        if clone.returncode:
            raise AuditError(f"git clone failed: {clone.stderr.strip() or 'exit ' + str(clone.returncode)}")
        checkout = subprocess.run(
            [git, "checkout", "--quiet", branch or "HEAD"],
            cwd=destination,
            env=isolated_env(root),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        if checkout.returncode:
            raise AuditError(f"git checkout failed: {checkout.stderr.strip() or 'exit ' + str(checkout.returncode)}")
        head = subprocess.run(
            [git, "rev-parse", "HEAD"],
            cwd=destination,
            env=isolated_env(root),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        if head.returncode:
            raise AuditError(f"git rev-parse failed: {head.stderr.strip()}")
        script = r'''source "$1" >/dev/null
if ! declare -F pkgver >/dev/null; then
    printf 'PKGBUILD has no pkgver() function\n' >&2
    exit 2
fi
pkgver
'''
        version = subprocess.run(
            [bash, "--noprofile", "--norc", "-c", script, "bash", str(recipe.path.resolve())],
            cwd=root,
            env=isolated_env(root, srcdir=str(root), pkgdir=str(root / "pkg")),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        if version.returncode:
            raise AuditError(f"pkgver() failed: {version.stderr.strip() or 'exit ' + str(version.returncode)}")
        latest = version.stdout.strip().splitlines()[-1] if version.stdout.strip() else ""
        if re.search(r"\.r\d+\.g[0-9a-f]+$", recipe.pkgver):
            latest = re.sub(r"-(\d+)-g([0-9a-f]+)$", r".r\1.g\2", latest)
        if not latest:
            raise AuditError("pkgver() produced no version")
        return latest, head.stdout.strip()


def audit_vcs(recipe: Recipe, target: Target) -> Result:
    latest, head = compute_vcs_version(recipe, target)
    status = "current" if latest == recipe.pkgver else ("intentionally_held" if target.hold_reason else "outdated")
    detail = target.hold_reason if status == "intentionally_held" else f"upstream HEAD {head[:12]}"
    return Result(recipe.pkgbase, recipe.relative_path, recipe.pkgnames, "vcs", recipe.pkgver, latest, status, clean_vcs_url(target.source_url), detail, extractor=recipe.extractor)


def audit_pinned_head(recipe: Recipe, target: Target, http: HttpClient) -> Result:
    if not target.repo:
        raise AuditError("pinned GitHub repository could not be inferred")
    current = shell_assignment(recipe.raw, target.pinned_var)
    if not re.fullmatch(r"[0-9a-fA-F]{7,40}", current):
        raise AuditError(f"{target.pinned_var} is not a literal Git commit")
    repository = http.get_json(f"https://api.github.com/repos/{target.repo}")
    default_branch = str(repository.get("default_branch", "")) if isinstance(repository, Mapping) else ""
    if not default_branch:
        raise AuditError("GitHub repository response has no default_branch")
    data = http.get_json(f"https://api.github.com/repos/{target.repo}/commits/{quote(default_branch, safe='')}")
    latest = str(data.get("sha", "")) if isinstance(data, Mapping) else ""
    if not latest:
        raise AuditError("GitHub default-branch HEAD response has no sha")
    if latest.startswith(current) or current.startswith(latest):
        status, detail = "current", ""
    elif target.hold_reason:
        status, detail = "intentionally_held", target.hold_reason
    else:
        status, detail = "outdated", "pinned commit differs from repository HEAD"
    return Result(recipe.pkgbase, recipe.relative_path, recipe.pkgnames, "pinned-github-head", current, latest, status, f"github:{target.repo}", detail, extractor=recipe.extractor)


def current_checksum(recipe: Recipe, url: str) -> tuple[str, str]:
    algorithm_names = {
        "sha256sums": "sha256",
        "sha512sums": "sha512",
        "b2sums": "blake2b",
        "md5sums": "md5",
    }
    for source_key, value in recipe.sources():
        if source_url(value) != url:
            continue
        suffix = source_key[len("source") :]
        peers = recipe.fields.get(source_key, [])
        try:
            index = peers.index(value)
        except ValueError:
            continue
        for checksum_key in CHECKSUM_KEYS:
            checksums = recipe.fields.get(checksum_key + suffix, [])
            if index < len(checksums) and checksums[index].upper() != "SKIP":
                return algorithm_names[checksum_key], checksums[index]
    return "", ""


def audit_static(recipe: Recipe, target: Target, http: HttpClient) -> Result:
    if not target.source_url:
        raise AuditError("static source URL could not be inferred")
    algorithm, expected = current_checksum(recipe, target.source_url)
    if not algorithm or not expected:
        return Result(recipe.pkgbase, recipe.relative_path, recipe.pkgnames, "static-checksum", expected, "", "untrackable", target.source_url, "static source has no supported checksum", extractor=recipe.extractor)
    actual = http.digest(target.source_url, algorithm)
    if actual.lower() == expected.lower():
        status, detail = "current", f"downloaded content matches packaged {algorithm}"
    elif target.hold_reason:
        status, detail = "intentionally_held", target.hold_reason
    else:
        status, detail = "outdated", f"unversioned source content changed ({algorithm})"
    return Result(recipe.pkgbase, recipe.relative_path, recipe.pkgnames, "static-checksum", expected, actual, status, target.source_url, detail, extractor=recipe.extractor)


def audit_http_index(recipe: Recipe, target: Target, http: HttpClient) -> Result:
    if not target.index_url or not target.file_regex:
        raise AuditError("http-index requires index_url and file_regex")
    body = http.get_bytes(target.index_url).decode("utf-8", "replace")
    try:
        matches = re.findall(target.file_regex, body)
    except re.error as exc:
        raise AuditError(f"invalid http-index file_regex: {exc}") from exc
    versions = unique(match[0] if isinstance(match, tuple) else match for match in matches)
    versions = [version for version in versions if version and is_stable_tag(version)]
    if not versions:
        raise AuditError("http-index regex found no stable versions")
    latest = max(versions, key=natural_key)
    status, detail = result_status(recipe.pkgver, latest, target.hold_reason)
    return Result(recipe.pkgbase, recipe.relative_path, recipe.pkgnames, "http-index", recipe.pkgver, latest, status, target.index_url, detail, extractor=recipe.extractor)


def uncertain_result(recipe: Recipe, target: Target, message: str) -> Result:
    return Result(
        recipe.pkgbase,
        recipe.relative_path,
        recipe.pkgnames,
        target.channel or "unmapped",
        recipe.pkgver,
        "",
        "uncertain" if target.channel else "unmapped",
        target.repo or target.name or target.source_url or target.index_url,
        message,
        extractor=recipe.extractor,
    )


def audit_one(recipe: Recipe, target: Target, http: HttpClient) -> Result:
    if recipe.metadata_error and not recipe.fields:
        return uncertain_result(recipe, target, recipe.metadata_error)
    if not recipe.pkgver:
        return Result(recipe.pkgbase, recipe.relative_path, recipe.pkgnames, target.channel or "unmapped", "", "", "unmapped", "", "PKGBUILD metadata has no pkgver", extractor=recipe.extractor)
    if not target.channel:
        return Result(recipe.pkgbase, recipe.relative_path, recipe.pkgnames, "unmapped", recipe.pkgver, "", "unmapped", "", "no upstream channel could be inferred", extractor=recipe.extractor)
    try:
        if target.channel in {"github", "codeberg"}:
            return audit_forge(recipe, target, http)
        if target.channel == "npm":
            return audit_npm(recipe, target, http)
        if target.channel == "pypi":
            return audit_pypi(recipe, target, http)
        if target.channel == "vcs":
            return audit_vcs(recipe, target)
        if target.channel == "pinned-github-head":
            return audit_pinned_head(recipe, target, http)
        if target.channel == "static-checksum":
            return audit_static(recipe, target, http)
        if target.channel == "http-index":
            return audit_http_index(recipe, target, http)
        return Result(recipe.pkgbase, recipe.relative_path, recipe.pkgnames, target.channel, recipe.pkgver, "", "untrackable", "", f"unsupported configured channel: {target.channel}", extractor=recipe.extractor)
    except AuditError as exc:
        return uncertain_result(recipe, target, str(exc))
    except (OSError, subprocess.SubprocessError) as exc:
        return uncertain_result(recipe, target, str(exc))


def select_recipes(recipes: Sequence[Recipe], patterns: Sequence[str]) -> list[Recipe]:
    if not patterns:
        return list(recipes)
    selected: list[Recipe] = []
    for recipe in recipes:
        names = [recipe.pkgbase, recipe.relative_path, *recipe.pkgnames]
        if any(any(fnmatch.fnmatchcase(name, pattern) for name in names) for pattern in patterns):
            selected.append(recipe)
    return selected


def duplicate_groups(recipes: Sequence[Recipe]) -> dict[str, list[Recipe]]:
    groups: dict[str, list[Recipe]] = {}
    for recipe in recipes:
        groups.setdefault(recipe.pkgbase, []).append(recipe)
    return {pkgbase: group for pkgbase, group in groups.items() if len(group) > 1}


def inventory_records(recipes: Sequence[Recipe], overrides: Mapping[str, Mapping[str, Any]]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    duplicates = duplicate_groups(recipes)
    for recipe in recipes:
        if recipe.pkgbase in duplicates and recipe is not duplicates[recipe.pkgbase][0]:
            continue
        target = classify(recipe, overrides.get(recipe.pkgbase, {}))
        status = "mapped"
        detail = recipe.metadata_error
        paths = [item.relative_path for item in duplicates.get(recipe.pkgbase, [])]
        if paths:
            status = "unmapped"
            detail = "duplicate pkgbase: " + ", ".join(paths)
        elif not target.channel or not recipe.pkgver:
            status = "unmapped"
            detail = detail or "upstream channel or pkgver could not be inferred"
        if target.channel in {"npm", "pypi"}:
            upstream = target.name
        elif target.channel in {"github", "codeberg", "pinned-github-head"}:
            upstream = target.repo
        else:
            upstream = target.source_url or target.index_url or target.repo or target.name
        records.append(
            {
                "pkgbase": recipe.pkgbase,
                "path": recipe.relative_path,
                "pkgnames": recipe.pkgnames,
                "current": recipe.pkgver,
                "channel": target.channel or "unmapped",
                "upstream": upstream,
                "tag_prefix": target.tag_prefix,
                "required_assets": target.required_assets,
                "status": status,
                "detail": detail,
                "extractor": recipe.extractor,
                "duplicate_paths": paths,
            }
        )
    return records


def audit_records(recipes: Sequence[Recipe], overrides: Mapping[str, Mapping[str, Any]], workers: int) -> list[Result]:
    duplicates = duplicate_groups(recipes)
    unique_recipes = [recipe for recipe in recipes if recipe is duplicates.get(recipe.pkgbase, [recipe])[0]]
    http = HttpClient()
    results: dict[str, Result] = {}

    def run(recipe: Recipe) -> Result:
        if recipe.pkgbase in duplicates:
            paths = [item.relative_path for item in duplicates[recipe.pkgbase]]
            return Result(
                recipe.pkgbase,
                recipe.relative_path,
                recipe.pkgnames,
                "unmapped",
                recipe.pkgver,
                "",
                "unmapped",
                "",
                "duplicate pkgbase: " + ", ".join(paths),
                extractor=recipe.extractor,
                duplicate_paths=paths,
            )
        return audit_one(recipe, classify(recipe, overrides.get(recipe.pkgbase, {})), http)

    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(run, recipe): recipe for recipe in unique_recipes}
        for future in concurrent.futures.as_completed(futures):
            recipe = futures[future]
            try:
                result = future.result()
            except Exception as exc:  # keep ledger total even on an unexpected worker error
                result = Result(recipe.pkgbase, recipe.relative_path, recipe.pkgnames, "unmapped", recipe.pkgver, "", "uncertain", "", f"worker failed: {exc}", extractor=recipe.extractor)
            results[recipe.pkgbase] = result

    expected = {recipe.pkgbase for recipe in recipes}
    for missing in sorted(expected - set(results)):
        recipe = next(item for item in recipes if item.pkgbase == missing)
        results[missing] = Result(missing, recipe.relative_path, recipe.pkgnames, "unmapped", recipe.pkgver, "", "unmapped", "", "internal ledger omission", extractor=recipe.extractor)
    return [results[pkgbase] for pkgbase in sorted(results)]


def print_table(rows: Sequence[Mapping[str, Any]], columns: Sequence[tuple[str, str]], detail_key: str = "detail") -> None:
    rendered: list[list[str]] = []
    for row in rows:
        rendered.append([str(row.get(key, "")) for key, _ in columns])
    widths = [len(title) for _, title in columns]
    for row in rendered:
        for index, value in enumerate(row):
            widths[index] = min(max(widths[index], len(value)), 48)
    header = "  ".join(title.ljust(widths[index]) for index, (_, title) in enumerate(columns))
    print(header)
    print("  ".join("-" * width for width in widths))
    for original, row in zip(rows, rendered):
        clipped = [value if len(value) <= widths[index] else value[: widths[index] - 1] + "…" for index, value in enumerate(row)]
        print("  ".join(value.ljust(widths[index]) for index, value in enumerate(clipped)))
        detail = str(original.get(detail_key, ""))
        if detail:
            print(f"    {detail}")


def common_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--root", type=Path, default=Path.cwd(), help="workspace root (default: current directory)")
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    parser.add_argument("--workers", type=int, default=min(8, os.cpu_count() or 1), help="parallel audit workers (default: up to 8)")
    parser.add_argument("--only", action="append", default=[], metavar="GLOB", help="include matching pkgbase, pkgname, or relative path; repeatable")
    return parser


def build_parser() -> argparse.ArgumentParser:
    exits = "Exit codes: 0 = all selected packages mapped/current (or intentionally held); 1 = findings, UNMAPPED entries, duplicates, or incomplete ledger; 2 = CLI/config/discovery failure."
    parser = argparse.ArgumentParser(description=__doc__, epilog=exits)
    subparsers = parser.add_subparsers(dest="command", required=True)
    inventory = subparsers.add_parser("inventory", parents=[common_parser()], help="discover recipes and show inferred upstream channels", epilog=exits)
    inventory.set_defaults(command="inventory")
    audit = subparsers.add_parser("audit", parents=[common_parser()], help="query upstreams and compare versions/checksums", epilog=exits)
    audit.set_defaults(command="audit")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.workers < 1:
        print("error: --workers must be at least 1", file=sys.stderr)
        return 2
    root = args.root.expanduser().resolve()
    if not root.is_dir():
        print(f"error: workspace root is not a directory: {root}", file=sys.stderr)
        return 2
    override_path = Path(__file__).with_name("audit-overrides.toml")
    try:
        overrides = load_overrides(override_path)
        paths = discover_pkgbuilds(root)
        if not paths:
            raise AuditError(f"no non-vendored PKGBUILD found under {root}")
        recipes = [extract_metadata(path, root) for path in paths]
    except (AuditError, OSError, tomllib.TOMLDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    recipes = select_recipes(recipes, args.only)
    if args.only and not recipes:
        print("error: --only did not match any discovered package", file=sys.stderr)
        return 2

    stale_overrides = sorted(set(overrides) - {recipe.pkgbase for recipe in recipes if not args.only}) if not args.only else []
    if args.command == "inventory":
        records = inventory_records(recipes, overrides)
        payload = {
            "command": "inventory",
            "root": str(root),
            "count": len(records),
            "results": records,
            "ignored_stale_overrides": stale_overrides,
        }
        if args.json:
            print(json.dumps(payload, indent=2, sort_keys=True))
        else:
            print_table(records, (("pkgbase", "PACKAGE BASE"), ("path", "PATH"), ("current", "CURRENT"), ("channel", "CHANNEL"), ("upstream", "UPSTREAM"), ("status", "STATUS")))
            if stale_overrides:
                print("\nIgnored overrides for undiscovered packages: " + ", ".join(stale_overrides))
        return 1 if any(record["status"] == "unmapped" for record in records) else 0

    results = audit_records(recipes, overrides, args.workers)
    payload = {
        "command": "audit",
        "root": str(root),
        "count": len(results),
        "results": [result.as_dict() for result in results],
        "ignored_stale_overrides": stale_overrides,
    }
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        rows = [result.as_dict() for result in results]
        print_table(rows, (("pkgbase", "PACKAGE BASE"), ("current", "CURRENT"), ("latest", "LATEST"), ("channel", "CHANNEL"), ("status", "STATUS")))
        if stale_overrides:
            print("\nIgnored overrides for undiscovered packages: " + ", ".join(stale_overrides))
    return 1 if any(result.status in BAD_STATUSES for result in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
