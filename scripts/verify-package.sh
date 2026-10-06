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
  local dep=$1 name dir
  name=$(curl -fsS "https://aur.archlinux.org/rpc/v5/search/$dep?by=provides" |
    jq -r --arg d "$dep" '[.results[] | select(.OutOfDate == null)] |
      (map(select(.Name == $d)) + map(select(.Name == ($d + "-bin"))) + .)[0].Name // empty')
  [ -n "$name" ] || return 1
  echo "== AUR dependency $dep -> $name"
  dir=$(sudo -u builder mktemp -d)
  sudo -u builder git clone -q "https://aur.archlinux.org/$name.git" "$dir/$name" &&
    (cd "$dir/$name" && sudo -u builder -H makepkg -si --noconfirm --needed)
}
pacman -S --noconfirm --needed git >/dev/null || fail pacman-setup
while IFS= read -r dep; do
  dep=${dep%%[<>=]*}
  pacman -T "$dep" >/dev/null && continue
  pacman -Sp "$dep" >/dev/null 2>&1 && continue
  aur_dep "$dep" || fail "aur-dependency:$dep"
done < <(sed -nE "s/^\t(make|check)?depends(_$ARCH)? = //p" /tmp/srcinfo.gen | sort -u)

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
  cmd=(docker run --rm --platform "$platform"
    -v "$pkgdir:/in:ro" -v "$out:/out"
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
