#!/usr/bin/env bash
# verify-package.sh <pkgdir> [--arch x86_64|aarch64|all] [--smoke 'cmd ...']
#
# Deterministic pre-push gate for one AUR package, run from the macOS host via
# Docker Arch containers (x86_64: archlinux:base-devel, aarch64:
# menci/archlinuxarm:base-devel). Per arch it checks, in a clean container:
#   - `makepkg --printsrcinfo` is byte-identical to the committed .SRCINFO
#   - `makepkg -s -f` succeeds
#   - namcap output (informational only, saved per arch)
#   - the built package installs with `pacman -U`
#   - optional smoke command (--smoke or $VERIFY_SMOKE), run as an
#     unprivileged user with a fresh HOME after install; non-zero = FAIL
# The package directory is mounted read-only; nothing is written to the repo.
#
# The final output contains one stable, machine-readable line per arch:
#   VERIFY <pkgbase> <arch> PASS|FAIL <reason>
# Agents MUST quote these lines verbatim as validation evidence. Exit status is
# non-zero if any arch failed.
set -euo pipefail

usage() {
  echo "usage: $0 <pkgdir> [--arch x86_64|aarch64|all] [--smoke 'cmd ...']" >&2
  exit 2
}

[ $# -ge 1 ] || usage
pkgdir_arg=$1
shift
want_arch=all
smoke=${VERIFY_SMOKE:-}
while [ $# -gt 0 ]; do
  case $1 in
    --arch) [ $# -ge 2 ] || usage; want_arch=$2; shift 2 ;;
    --smoke) [ $# -ge 2 ] || usage; smoke=$2; shift 2 ;;
    *) usage ;;
  esac
done
case $want_arch in x86_64|aarch64|all) ;; *) usage ;; esac

pkgdir=$(cd "$pkgdir_arg" && pwd)
[ -f "$pkgdir/PKGBUILD" ] || { echo "error: $pkgdir/PKGBUILD not found" >&2; exit 2; }

# Host pre-check: PKGBUILD must parse.
if ! bash -n "$pkgdir/PKGBUILD"; then
  echo "error: bash -n PKGBUILD failed" >&2
  echo "VERIFY $(basename "$pkgdir") $want_arch FAIL pkgbuild-syntax"
  exit 1
fi

# Read pkgbase and arch=() from the PKGBUILD (same sourcing makepkg does).
meta=$(cd "$pkgdir" && env -i PATH="$PATH" bash -c '
  set +u
  source ./PKGBUILD >/dev/null 2>&1 || exit 1
  printf "%s\n" "${pkgbase:-${pkgname[0]}}"
  printf "%s\n" "${arch[@]}"
') || { echo "error: sourcing PKGBUILD failed" >&2; exit 1; }
pkgbase=$(printf '%s\n' "$meta" | sed -n 1p)
declared=$(printf '%s\n' "$meta" | sed -n '2,$p')

arches=()
if printf '%s\n' "$declared" | grep -qx any; then
  arches=(x86_64)
else
  for a in x86_64 aarch64; do
    if printf '%s\n' "$declared" | grep -qx "$a"; then arches+=("$a"); fi
  done
fi
if [ "$want_arch" != all ]; then
  if printf '%s\n' "${arches[@]:-}" | grep -qx "$want_arch"; then
    arches=("$want_arch")
  else
    echo "error: arch $want_arch not declared in PKGBUILD arch=($declared)" >&2
    exit 2
  fi
fi
if [ ${#arches[@]} -eq 0 ]; then
  echo "error: PKGBUILD arch=() declares neither x86_64, aarch64 nor any" >&2
  exit 2
fi

scratch=$(mktemp -d)
trap 'rm -rf "$scratch"' EXIT

# Runs inside the container as root. Writes the failure reason to /out/reason.
read -r -d '' INNER <<'EOF' || true
set -euo pipefail
fail() { echo "$1" > /out/reason; echo "FAIL: $1" >&2; exit 1; }

sed -i 's/^DownloadUser/#DownloadUser/' /etc/pacman.conf
grep -q '^DisableSandbox' /etc/pacman.conf || sed -i '/^\[options\]/a DisableSandbox' /etc/pacman.conf
pacman -Syu --noconfirm --needed namcap jq >/dev/null || fail pacman-setup

useradd -m builder
echo 'builder ALL=(ALL) NOPASSWD: ALL' > /etc/sudoers.d/builder
chmod 0440 /etc/sudoers.d/builder

work=/home/builder/work
mkdir -p "$work" /home/builder/pkgout
cp -a /in/. "$work/"
rm -rf "$work/src" "$work/pkg" "$work/.git"
find "$work" -maxdepth 1 -type f \( -name '*.tar.*' -o -name '*.tgz' -o -name '*.zip' -o -name '*.gz' \) -delete
chown -R builder:builder /home/builder
cd "$work"

asb() { sudo -u builder -H env PKGDEST=/home/builder/pkgout "$@"; }

echo "== .SRCINFO check"
[ -f .SRCINFO ] || fail srcinfo-missing
asb makepkg --printsrcinfo > /tmp/srcinfo.gen || fail printsrcinfo
if ! cmp -s /tmp/srcinfo.gen .SRCINFO; then
  diff -u .SRCINFO /tmp/srcinfo.gen || true
  fail srcinfo-mismatch
fi

# Dependencies missing from the container's repos (e.g. electron on Arch Linux
# ARM) are built from their AUR provider, as an AUR helper would do.
aur_dep() {
  local dep=$1 name dir sub
  dep=${dep%%[<>=]*}
  pacman -T "$dep" >/dev/null && return 0
  if pacman -Sp "$dep" >/dev/null 2>&1; then
    pacman -S --noconfirm --needed "$dep" >/dev/null || return 1
    return 0
  fi
  dir=$(sudo -u builder mktemp -d)
  if [ -f "/repo/$dep/PKGBUILD" ]; then
    name=$dep
    echo "== AUR dependency $dep -> /repo/$name"
    mkdir -p "$dir/$name"
    cp -a "/repo/$name/." "$dir/$name/"
    rm -rf "$dir/$name/src" "$dir/$name/pkg" "$dir/$name/.git"
    chown -R builder:builder "$dir/$name"
  else
    name=$(curl -fsS --retry 3 --max-time 30 "https://aur.archlinux.org/rpc/v5/search/$dep?by=provides" |
      jq -r --arg d "$dep" '[.results[] | select(.OutOfDate == null)] |
        (map(select(.Name == $d)) + map(select(.Name == ($d + "-bin"))) + .)[0].Name // empty')
    [ -n "$name" ] || return 1
    echo "== AUR dependency $dep -> $name"
    sudo -u builder git -c http.lowSpeedLimit=1000 -c http.lowSpeedTime=20 clone -q --depth 1 "https://aur.archlinux.org/$name.git" "$dir/$name" || return 1
  fi
  while IFS= read -r sub; do
    aur_dep "$sub" || return 1
  done < <(cd "$dir/$name" && sudo -u builder makepkg --printsrcinfo | sed -nE "s/^\t(make|check)?depends(_$ARCH)? = ([^<>=]+).*/\3/p" | sort -u)
  (cd "$dir/$name" && sudo -u builder -H makepkg -si -A --noconfirm --needed)
}
pacman -S --noconfirm --needed git >/dev/null || fail pacman-setup
while IFS= read -r dep; do
  aur_dep "$dep" || fail "aur-dependency:$dep"
done < <(sed -nE "s/^\t(make|check)?depends(_$ARCH)? = ([^<>=]+).*/\3/p" /tmp/srcinfo.gen | sort -u)

echo "== source manifest snapshot"
rm -rf "$work/.verify-manifest-pre"
mkdir -p "$work/.verify-manifest-pre"
asb makepkg --nobuild --noprepare --noconfirm || fail source-extract
while IFS= read -r -d '' cfg; do
  rel=${cfg#"$work/src/"}
  mkdir -p "$work/.verify-manifest-pre/$(dirname "$rel")"
  cp -a "$cfg" "$work/.verify-manifest-pre/$rel"
done < <(find "$work/src" -type f \( \
  -name 'electron-builder*.cjs' -o -name 'electron-builder*.js' -o \
  -name 'electron-builder*.mjs' -o -name 'electron-builder*.ts' -o \
  -name 'electron-builder*.json' -o -name 'electron-builder*.yml' -o \
  -name 'electron-builder*.yaml' \) -print0)
chown -R builder:builder "$work/.verify-manifest-pre"

echo "== makepkg -s -f"
asb makepkg -s -f --noconfirm || fail build

shopt -s nullglob
pkgs=(/home/builder/pkgout/*.pkg.tar.*)
[ ${#pkgs[@]} -gt 0 ] || fail no-package-output

echo "== namcap (informational)"
{ namcap PKGBUILD; namcap "${pkgs[@]}"; } > "/out/namcap.$ARCH" 2>&1 || true
cat "/out/namcap.$ARCH"

for p in "${pkgs[@]}"; do
  echo "== $(basename "$p")"
  pacman -Qip "$p" || true
  pacman -Qlp "$p" | head -n 40 || true
done

echo "== pacman -U"
pacman -U --noconfirm "${pkgs[@]}" || fail install

mapfile -t pkg_names < <(pacman -Qqp "${pkgs[@]}" | sort -u)
[ ${#pkg_names[@]} -gt 0 ] || fail package-names
mapfile -t base_devel < <(
  {
    printf 'base-devel\ngit\nnamcap\njq\n'
    pacman -Sgq base-devel 2>/dev/null || true
    pacman -Qi base-devel 2>/dev/null | sed -n 's/^Depends On *: *//p' | tr ' ' '\n' | sed 's/[<>=].*//'
  } | awk 'NF && $0 != "None"' | sort -u
)
mapfile -t build_only < <(
  sed -nE "s/^\t(make|check)depends(_$ARCH)? = ([^<>=]+).*/\3/p" /tmp/srcinfo.gen |
    sed 's/[<>=].*//' | sort -u
)
for dep in "${build_only[@]}"; do
  [ -n "$dep" ] || continue
  if printf '%s\n' "${base_devel[@]}" | grep -Fxq "$dep"; then
    continue
  fi
  pacman -Rns --noconfirm "$dep" >/dev/null 2>&1 || true
done

pacman -Qlq "${pkg_names[@]}" | sort -u > /tmp/verify-package.files
cat > /tmp/verify-package-gate.py <<'PY'
#!/usr/bin/env python3
import json
import os
import posixpath
import re
import shlex
import subprocess
import sys


ARCH = sys.argv[1]
MANIFEST_PRE = sys.argv[2]
POST_SRC = sys.argv[3]
FILES = sys.argv[4]
SRCINFO = sys.argv[5] if len(sys.argv) > 5 else ""
VERIFY_ROOT = os.environ.get("VERIFY_PACKAGE_ROOT", "").rstrip("/")


def rooted(path):
    if VERIFY_ROOT and path.startswith("/"):
        return VERIFY_ROOT + path
    return path


def exists(path):
    return path in owned or os.path.exists(rooted(path))


def executable(path):
    return os.path.exists(rooted(path)) and os.access(rooted(path), os.X_OK)


def input_path(path):
    if os.path.exists(path) or not VERIFY_ROOT or not path.startswith("/"):
        return path
    return rooted(path)


def read_text(path):
    actual = rooted(path) if path.startswith(("/usr/", "/opt/")) else path
    try:
        with open(actual, "r", encoding="utf-8", errors="replace") as f:
            return f.read()
    except (OSError, UnicodeError):
        return ""

def logical_package_root(path):
    match = re.match(r"^(/usr/lib/[^/]+)", path)
    return match.group(1) if match else ""


def extract_extra_paths(text):
    pattern = re.compile(
        r"""(?:\bto\s*:\s*|["']to["']\s*:\s*)["']([^"']+)["']"""
    )
    return set(match.group(1) for match in pattern.finditer(text))


def add_failure(value):
    failures.add(value)


with open(input_path(FILES), "r", encoding="utf-8", errors="replace") as f:
    owned = {line.strip() for line in f if line.strip()}
failures = set()
optdepends_prefixes = set()
if SRCINFO and os.path.exists(input_path(SRCINFO)):
    for line in read_text(input_path(SRCINFO)).splitlines():
        match = re.match(r"^\s*optdepends(?:_\w+)?\s*=\s*([A-Za-z0-9@._+-]+)", line)
        if match:
            optdepends_prefixes.add(match.group(1))
pkg_roots = sorted(
    {logical_package_root(path) for path in owned if logical_package_root(path)}
)

# 1. Preserve literal extraResources/extraFiles destinations removed by prepare().
manifest_suffixes = (".cjs", ".js", ".mjs", ".ts", ".json", ".yml", ".yaml")
manifest_dir = input_path(MANIFEST_PRE)
post_src_dir = input_path(POST_SRC)
if os.path.isdir(manifest_dir):
    for root, _, names in os.walk(manifest_dir):
        for name in names:
            if not name.startswith("electron-builder") or not name.endswith(manifest_suffixes):
                continue
            pre_path = os.path.join(root, name)
            rel_cfg = os.path.relpath(pre_path, manifest_dir)
            post_path = os.path.join(post_src_dir, rel_cfg)
            before = extract_extra_paths(
                open(pre_path, "r", encoding="utf-8", errors="replace").read()
            )
            after = extract_extra_paths(read_text(post_path)) if os.path.exists(post_path) else set()
            for destination in sorted(before - after):
                destination = destination.lstrip("/")
                if not any(
                    exists(posixpath.normpath(posixpath.join(pkg, destination)))
                    or exists(posixpath.normpath(posixpath.join(pkg, "resources", destination)))
                    for pkg in pkg_roots
                ):
                    add_failure(
                        f"prepare-removed-extra-resource:{rel_cfg}:{destination}"
                    )

# 2. Resolve literal process.resourcesPath and /usr/lib joins in packaged code.
process_join = re.compile(
    r"""join\(\s*process\.resourcesPath\s*,\s*['"]([^'"]+)['"]"""
    r"""(?:\s*,\s*['"]([^'"]+)['"])?\s*\)"""
)
absolute_join = re.compile(
    r"""join\(\s*['"](/usr/lib/[^'"]+)['"]\s*,\s*['"]([^'"]+)['"]"""
    r"""(?:\s*,\s*['"]([^'"]+)['"])?\s*\)"""
)
code_paths = [
    path
    for path in sorted(owned)
    if path.endswith(".asar")
    or (
        path.endswith(".js")
        and logical_package_root(path)
        and "/node_modules/" not in path
    )
]
for origin in code_paths:
    text = read_text(origin)
    origin_root = logical_package_root(origin)
    for match in process_join.finditer(text):
        parts = [part for part in match.groups() if part is not None]
        if not origin_root:
            continue
        resolved = posixpath.normpath(
            posixpath.join(origin_root, "resources", *parts)
        )
        if not exists(resolved):
            add_failure(f"packaged-code-missing-resource:{origin}:{resolved}")
    for match in absolute_join.finditer(text):
        parts = [match.group(1), match.group(2)]
        if match.group(3) is not None:
            parts.append(match.group(3))
        resolved = posixpath.normpath(posixpath.join(*parts))
        if not exists(resolved):
            add_failure(f"packaged-code-missing-resource:{origin}:{resolved}")

# 3. Installed filesystem, wrappers, desktop entries, and systemd units.
for path in sorted(owned):
    actual = rooted(path)
    if os.path.islink(actual) and not os.path.exists(actual):
        add_failure(f"dangling-symlink:{path}")

    if (
        re.fullmatch(r"/usr/bin/[^/]+", path)
        or re.fullmatch(r"/usr/lib/[^/]+/bin/[^/]+", path)
    ) and os.path.isfile(actual) and not os.access(actual, os.X_OK):
        add_failure(f"non-executable:{path}")

    if (
        re.fullmatch(r"/usr/bin/[^/]+", path)
        or re.fullmatch(r"/usr/lib/[^/]+/[^/]+", path)
        or re.fullmatch(r"/usr/lib/[^/]+/bin/[^/]+", path)
    ) and os.path.isfile(actual):
        try:
            with open(actual, "rb") as f:
                first_line = f.readline().decode("utf-8", "replace")
                body = f.read().decode("utf-8", "replace")
        except OSError:
            first_line = body = ""
        if re.match(
            r"^#!\s*(?:/bin/sh|/bin/bash|/usr/bin/env\s+(?:sh|bash))\b",
            first_line,
        ):
            for line in (first_line + body).splitlines():
                if line.lstrip().startswith("#"):
                    continue
                if not line.startswith("exec ") and "/usr/" not in line and "/opt/" not in line:
                    continue
                for match in re.finditer(
                    r"/usr/[A-Za-z0-9_./+-]+|/opt/[A-Za-z0-9_./+-]+", line
                ):
                    token = match.group(0).rstrip(".")
                    before = line[match.start() - 1] if match.start() else ""
                    after = line[match.end()] if match.end() < len(line) else ""
                    if (before and before in "${}%*") or (after and after in "${}%*"):
                        continue
                    if not os.path.exists(rooted(token)):
                        add_failure(f"wrapper-path:{path}:{token}")

def desktop_command(value):
    try:
        tokens = shlex.split(value, posix=True)
    except ValueError:
        return ""
    if tokens and tokens[0] == "env":
        tokens.pop(0)
    while tokens and "=" in tokens[0] and not tokens[0].startswith("/"):
        tokens.pop(0)
    return tokens[0] if tokens else ""


def desktop_icon_exists(value):
    if value.startswith("/"):
        return exists(value)
    candidates = {
        f"/usr/share/pixmaps/{value}",
        f"/usr/share/icons/{value}",
    }
    for extension in (".png", ".svg", ".xpm"):
        candidates.add(f"/usr/share/pixmaps/{value}{extension}")
        candidates.add(f"/usr/share/icons/{value}{extension}")
    if any(exists(candidate) for candidate in candidates):
        return True
    return any(
        path.startswith("/usr/share/icons/")
        and os.path.splitext(posixpath.basename(path))[0] == value
        and exists(path)
        for path in owned
    )


for path in sorted(owned):
    if re.fullmatch(r"/usr/share/applications/[^/]+\.desktop", path):
        lines = read_text(path).splitlines()
        exec_value = next(
            (line.split("=", 1)[1].strip() for line in lines if line.startswith("Exec=")),
            "",
        )
        command = desktop_command(exec_value)
        if command:
            command_path = command if command.startswith("/") else f"/usr/bin/{command}"
            if not executable(command_path):
                add_failure(f"desktop-exec:{path}:{command}")
        icon_value = next(
            (line.split("=", 1)[1].strip() for line in lines if line.startswith("Icon=")),
            "",
        )
        if icon_value and not desktop_icon_exists(icon_value):
            add_failure(f"desktop-icon:{path}:{icon_value}")

    if re.fullmatch(r"/usr/lib/systemd/(?:system|user)/[^/]+\.service", path):
        for line in read_text(path).splitlines():
            if not line.startswith("ExecStart="):
                continue
            value = line.split("=", 1)[1].strip()
            try:
                tokens = shlex.split(value, posix=True)
            except ValueError:
                tokens = []
            if not tokens:
                continue
            while tokens and tokens[0] and tokens[0][0] in "-@+!":
                tokens[0] = tokens[0][1:]
            command = tokens[0] if tokens else ""
            if command in ("/bin/sh", "/bin/bash") and len(tokens) >= 3 and tokens[1] == "-c":
                match = re.search(r"/usr/[A-Za-z0-9_./+-]+|/opt/[A-Za-z0-9_./+-]+", " ".join(tokens[2:]))
                command = match.group(0) if match else ""
            elif command and not command.startswith("/"):
                command = f"/usr/bin/{command}"
            if command and not executable(command):
                base = posixpath.basename(command)
                if not (
                    command.startswith("/usr/bin/")
                    and command not in owned
                    and any(
                        base == opt or base.startswith(opt + "_") or base.startswith(opt + "-")
                        for opt in optdepends_prefixes
                    )
                ):
                    add_failure(f"systemd-exec:{path}:{command}")

# 4. ELF architecture, dynamic executable bit, and ldd closure.
MACHINE_BY_ARCH = {
    "x86_64": "Advanced Micro Devices X86-64",
    "aarch64": "AArch64",
}
ARCH_TOKEN_RE = re.compile(
    r"(?:^|[^A-Za-z0-9])(arm64|aarch64|x64|amd64|x86_64)(?:[^A-Za-z0-9]|$)",
    re.IGNORECASE,
)
ARCH_BY_TOKEN = {
    "arm64": "aarch64",
    "aarch64": "aarch64",
    "x64": "x86_64",
    "amd64": "x86_64",
    "x86_64": "x86_64",
}
elf_library_dirs = sorted(
    {
        os.path.dirname(rooted(path))
        for path in owned
        if ".so" in posixpath.basename(path)
        and os.path.isfile(rooted(path))
    }
)
for path in sorted(owned):
    actual = rooted(path)
    if not os.path.isfile(actual):
        continue
    try:
        with open(actual, "rb") as f:
            if f.read(4) != b"\x7fELF":
                continue
    except OSError:
        continue
    token_matches = [
        ARCH_BY_TOKEN[m.group(1).lower()]
        for m in ARCH_TOKEN_RE.finditer(path)
    ]
    expected_arch = token_matches[-1] if token_matches else ARCH
    expected_machine = MACHINE_BY_ARCH.get(expected_arch)
    if expected_machine:
        header = subprocess.run(
            ["readelf", "-h", actual],
            capture_output=True,
            text=True,
            errors="replace",
            check=False,
        ).stdout
        if expected_machine not in header:
            add_failure(f"elf-arch:{path}:{expected_arch}")
    program = subprocess.run(
        ["readelf", "-l", actual],
        capture_output=True,
        text=True,
        errors="replace",
        check=False,
    ).stdout
    basename = posixpath.basename(path)
    if re.search(r"\bINTERP\b", program) and not (
        basename.startswith("libc.so") or basename.startswith("ld-")
    ) and not os.access(actual, os.X_OK):
        add_failure(f"non-executable-elf:{path}")
    dynamic = subprocess.run(
        ["readelf", "-d", actual],
        capture_output=True,
        text=True,
        errors="replace",
        check=False,
    ).stdout
    if "NEEDED" in dynamic and expected_arch == ARCH:
        env = os.environ.copy()
        env["LD_LIBRARY_PATH"] = ":".join(elf_library_dirs + [rooted("/usr/lib")])
        ldd = subprocess.run(
            ["ldd", actual],
            capture_output=True,
            text=True,
            errors="replace",
            env=env,
            check=False,
        )
        for line in ldd.stdout.splitlines() + ldd.stderr.splitlines():
            if "=> not found" in line:
                add_failure(f"elf-not-found:{path}:{line.strip()}")

# 5. Top-level Node package manifests and declared dependency closure.
manifest_re = re.compile(
    r"^/usr/lib/[^/]+/package\.json$|"
    r"^/usr/lib/node_modules/(?:@[^/]+/)?[^/]+/package\.json$"
)
bare_import = re.compile(
    r"""(?:\bimport\s+(?:[^'"]+?\s+from\s+)?|\brequire\s*\(\s*)['"]([^'"]+)['"]"""
)
for package_json in sorted(owned):
    if not manifest_re.fullmatch(package_json) or package_json[:-len("/package.json")].count("/node_modules/") > 1:
        continue
    try:
        metadata = json.loads(read_text(package_json))
    except (TypeError, ValueError):
        continue
    dependencies = metadata.get("dependencies", {})
    if not isinstance(dependencies, dict):
        continue
    app_root = posixpath.dirname(package_json)
    local_node_modules = posixpath.join(app_root, "node_modules")
    has_local = exists(local_node_modules) or any(
        path.startswith(local_node_modules + "/") for path in owned
    )
    referenced = set()
    for path in owned:
        if not path.startswith(app_root + "/") or not path.endswith(".js"):
            continue
        for match in bare_import.finditer(read_text(path)):
            dep = match.group(1)
            if dep.startswith(".") or dep.startswith("/") or dep.startswith("node:"):
                continue
            dep = "/".join(dep.split("/")[:2]) if dep.startswith("@") else dep.split("/")[0]
            if dep in dependencies:
                referenced.add(dep)
    if not has_local and not referenced:
        continue
    for dep in sorted(dependencies):
        local_dep = posixpath.join(local_node_modules, dep)
        global_dep = posixpath.join("/usr/lib/node_modules", dep)
        if not exists(local_dep) and not exists(global_dep):
            add_failure(f"missing-node-dependency:{package_json}:{dep}")

if failures:
    for failure in sorted(failures):
        print(failure)
    sys.exit(1)
PY
chmod 0755 /tmp/verify-package-gate.py
python /tmp/verify-package-gate.py "$ARCH" "$work/.verify-manifest-pre" "$work/src" /tmp/verify-package.files /tmp/srcinfo.gen || fail runtime-gate

if [ -n "$SMOKE" ]; then
  echo "== smoke: $SMOKE"
  install -d -o builder -g builder /tmp/smoke-home
  sudo -u builder env -i PATH=/usr/local/bin:/usr/bin HOME=/tmp/smoke-home \
    bash -c "cd /tmp/smoke-home && $SMOKE" || fail smoke
fi
echo ok > /out/reason
EOF

results=()
failed=0
for a in "${arches[@]}"; do
  case $a in
    x86_64) image=archlinux:base-devel; platform=linux/amd64 ;;
    aarch64) image=menci/archlinuxarm:base-devel; platform=linux/arm64 ;;
  esac
  out="$scratch/$a"
  mkdir -p "$out"
  reporoot=$(cd "$pkgdir" && git rev-parse --show-toplevel 2>/dev/null || dirname "$pkgdir")
  cmd=(docker run --rm --platform "$platform"
    -v "$pkgdir:/in:ro" -v "$reporoot:/repo:ro" -v "$out:/out"
    -e "ARCH=$a" -e "SMOKE=$smoke"
    "$image" bash -c "$INNER")
  echo "==== $pkgbase $a ($image)"
  if "${cmd[@]}"; then
    results+=("VERIFY $pkgbase $a PASS ok")
  else
    reason=container-error
    [ -s "$out/reason" ] && reason=$(cat "$out/reason")
    results+=("VERIFY $pkgbase $a FAIL $reason")
    failed=1
  fi
done

echo
printf '%s\n' "${results[@]}"
exit "$failed"
