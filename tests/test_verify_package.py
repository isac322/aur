#!/usr/bin/env python3
"""Deterministic, Docker-free tests for the installed-package verifier gates."""

from __future__ import annotations

import os
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
VERIFY_SH = ROOT / "scripts" / "verify-package.sh"


class VerifyPackageGateTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        script = VERIFY_SH.read_text(encoding="utf-8")
        marker = "cat > /tmp/verify-package-gate.py <<'PY'\n"
        start = script.index(marker) + len(marker)
        end = script.index("\nPY\n", start)
        cls.gate_source = script[start:end]

    def _write(self, root: Path, virtual: str, content: str | bytes, mode: int = 0o644) -> None:
        path = root / virtual.lstrip("/")
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, str):
            path.write_text(content, encoding="utf-8")
        else:
            path.write_bytes(content)
        path.chmod(mode)

    def _fake_tools(self, root: Path, *, elf_outputs: bool = True) -> Path:
        bindir = root / "fake-bin"
        bindir.mkdir()
        readelf = """#!/bin/sh
        file=$2
        name=$(basename "$file")
        case "$1" in
          -h)
            if [ "$name" = wrong-arch ]; then
              echo 'Machine: AArch64'
            elif [ "$name" = valid-i686 ] || [ "$name" = ldd-error ]; then
              echo 'Machine: Intel 80386'
            else
              echo 'Machine: Advanced Micro Devices X86-64'
            fi
            ;;
          -l)
            if [ "$name" = paseo-cli ]; then echo 'INTERP /lib64/ld-linux-x86-64.so.2'; fi
            ;;
          -d)
            case "$name" in
              not-found|paseo-cli|native.node|ldd-error) echo 'Dynamic section'; echo 'NEEDED';;
            esac
            ;;
          -Ws)
            ;;
        esac
        """
        self._write(root, "/fake-bin/readelf", textwrap.dedent(readelf), 0o755)
        ldd = """#!/bin/sh
        case "$(basename "$1")" in
          not-found) echo 'libmissing.so.1 => not found' ;;
          ldd-error) echo 'invalid ELF header' >&2; exit 1 ;;
        esac
        """
        self._write(root, "/fake-bin/ldd", textwrap.dedent(ldd), 0o755)
        strings = "#!/bin/sh\nexit 0\n"
        self._write(root, "/fake-bin/strings", strings, 0o755)
        return bindir

    def _run_gate(
        self,
        root: Path,
        owned: list[str],
        *,
        pre_files: dict[str, str] | None = None,
        post_files: dict[str, str] | None = None,
        modes: dict[str, int] | None = None,
        symlinks: dict[str, str] | None = None,
        fake_tools: bool = False,
        package_info: str = "",
        arch: str = "x86_64",
    ) -> subprocess.CompletedProcess[str]:
        pre = root / "manifest-pre"
        post = root / "home/builder/work/src"
        post_arg = "/home/builder/work/src"
        pre.mkdir(parents=True, exist_ok=True)
        post.mkdir(parents=True, exist_ok=True)
        for relative, content in (pre_files or {}).items():
            path = pre / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        for relative, content in (post_files or {}).items():
            path = post / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")

        modes = modes or {}
        symlink_names = set(symlinks or {})
        for virtual in owned:
            if virtual in symlink_names:
                continue
            path = root / virtual.lstrip("/")
            if path.exists() or path.is_symlink():
                continue
            self._write(
                root,
                virtual,
                "fixture\n",
                0o755 if virtual.startswith("/usr/bin/") else 0o644,
            )
        for virtual, target in (symlinks or {}).items():
            path = root / virtual.lstrip("/")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.symlink_to(target)
        for virtual, mode in modes.items():
            self._write(root, virtual, "fixture\n", mode)

        file_list = root / "owned-files"
        file_list.write_text("\n".join(owned) + "\n", encoding="utf-8")
        info = root / "package-info"
        info.write_text(package_info, encoding="utf-8")
        gate = root / "verify-package-gate.py"
        gate.write_text(self.gate_source, encoding="utf-8")
        gate.chmod(0o755)

        env = os.environ.copy()
        env["VERIFY_PACKAGE_ROOT"] = str(root)
        if fake_tools:
            fake = self._fake_tools(root)
            env["PATH"] = f"{fake}:{env.get('PATH', '')}"
        return subprocess.run(
            [
                "python3",
                str(gate),
                arch,
                str(pre),
                post_arg,
                str(file_list),
                str(info),
            ],
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )

    def _assert_failure(self, result: subprocess.CompletedProcess[str], reason: str) -> None:
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn(reason, result.stdout)

    def _assert_pass(self, result: subprocess.CompletedProcess[str]) -> None:
        self.assertEqual(result.returncode, 0, result.stdout)

    def test_shell_script_is_valid_bash(self) -> None:
        result = subprocess.run(["bash", "-n", str(VERIFY_SH)], text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_all_rejects_unsupported_declared_architecture_before_docker(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            package = root / "fixture"
            package.mkdir()
            (package / "PKGBUILD").write_text(
                "pkgname=fixture\npkgver=1\npkgrel=1\narch=(x86_64 riscv64)\n",
                encoding="utf-8",
            )
            docker = root / "docker"
            marker = root / "docker-ran"
            docker.write_text('#!/bin/sh\n: > "$DOCKER_MARKER"\nexit 99\n', encoding="utf-8")
            docker.chmod(0o755)
            env = os.environ.copy()
            env["PATH"] = f"{root}:{env.get('PATH', '')}"
            env["DOCKER_MARKER"] = str(marker)
            result = subprocess.run(
                ["bash", str(VERIFY_SH), str(package), "--arch", "all"],
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                check=False,
            )
            self.assertEqual(result.returncode, 2, result.stdout)
            self.assertFalse(marker.exists(), "unsupported architecture must fail before Docker starts")

    def test_prepare_removed_linux_resource_fails_without_file(self) -> None:
        pre = {"electron-builder.config.cjs": "extraResources: [{ to: 'bin/orca-wayland-clipboard' }]\n"}
        post = {"electron-builder.config.cjs": "extraResources: []\n"}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = self._run_gate(
                root,
                ["/usr/lib/stably-orca/placeholder"],
                pre_files=pre,
                post_files=post,
            )
            self._assert_failure(result, "prepare-removed-extra-resource")

    def test_prepare_removed_linux_resource_passes_when_file_exists(self) -> None:
        pre = {"electron-builder.config.cjs": "extraResources: [{ to: 'bin/orca-wayland-clipboard' }]\n"}
        post = {"electron-builder.config.cjs": "extraResources: []\n"}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = self._run_gate(
                root,
                [
                    "/usr/lib/stably-orca/placeholder",
                    "/usr/lib/stably-orca/bin/orca-wayland-clipboard",
                ],
                pre_files=pre,
                post_files=post,
                modes={"/usr/lib/stably-orca/bin/orca-wayland-clipboard": 0o755},
            )
            self._assert_pass(result)

    def test_kept_windows_and_macos_resources_do_not_false_positive(self) -> None:
        config = """win: { extraResources: [{ to: 'bin/orca.exe' }] }
        mac: { extraResources: [{ to: 'bin/orca.exe' }] }
        """
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = self._run_gate(
                root,
                ["/usr/lib/stably-orca/placeholder"],
                pre_files={"electron-builder.config.cjs": config},
                post_files={"electron-builder.config.cjs": config},
            )
            self._assert_pass(result)

    def test_packaged_code_missing_resource_and_present_control(self) -> None:
        source_variants = (
            "join('/usr/lib/stably-orca', 'bin', 'orca-wayland-clipboard')",
            "join(process.resourcesPath, 'bin', 'orca-wayland-clipboard')",
        )
        for source in source_variants:
            with self.subTest(source=source), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                owned = ["/usr/lib/stably-orca/out/main/index.js"]
                self._write(root, owned[0], source + "\n", 0o644)
                result = self._run_gate(root, owned)
                self._assert_failure(result, "packaged-code-missing-resource")

                helper = (
                    "/usr/lib/stably-orca/resources/bin/orca-wayland-clipboard"
                    if "process.resourcesPath" in source
                    else "/usr/lib/stably-orca/bin/orca-wayland-clipboard"
                )
                owned.append(helper)
                result = self._run_gate(
                    root,
                    owned,
                    modes={helper: 0o755},
                )
                self._assert_pass(result)

    def test_filesystem_wrapper_desktop_and_systemd_failures(self) -> None:
        owned = [
            "/usr/bin/foo",
            "/usr/bin/wrapper",
            "/usr/share/applications/missing-exec.desktop",
            "/usr/share/applications/missing-icon.desktop",
            "/usr/share/icons/hicolor/48x48/apps/ok.png",
            "/usr/bin/desktop-ok",
            "/usr/lib/systemd/system/foo.service",
        ]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write(root, "/usr/bin/wrapper", "#!/bin/sh\nexec /usr/lib/foo/missing.asar\n", 0o755)
            self._write(root, "/usr/bin/desktop-ok", "#!/bin/sh\nexit 0\n", 0o755)
            self._write(root, "/usr/share/applications/missing-exec.desktop", "[Desktop Entry]\nExec=not-installed\nIcon=ok\n")
            self._write(root, "/usr/share/applications/missing-icon.desktop", "[Desktop Entry]\nExec=desktop-ok\nIcon=missing-icon\n")
            self._write(root, "/usr/lib/systemd/system/foo.service", "[Service]\nExecStart=/usr/lib/foo/missing.asar\n")
            result = self._run_gate(root, owned, modes={"/usr/bin/foo": 0o644})
            for reason in ("non-executable", "wrapper-path", "desktop-exec", "desktop-icon", "systemd-exec"):
                self.assertIn(reason, result.stdout, result.stdout)

            result = self._run_gate(root, owned + ["/usr/lib/foo/dangling"], symlinks={"/usr/lib/foo/dangling": "missing-target"})
            self._assert_failure(result, "dangling-symlink")

    def test_filesystem_integrity_passing_control(self) -> None:
        owned = [
            "/usr/lib/foo/helper",
            "/usr/bin/foo",
            "/usr/bin/wrapper",
            "/usr/share/applications/ok.desktop",
            "/usr/share/icons/hicolor/48x48/apps/ok.png",
            "/usr/lib/systemd/system/foo.service",
        ]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write(root, "/usr/lib/foo/helper", "fixture\n", 0o755)
            self._write(root, "/usr/bin/foo", "#!/bin/sh\nexit 0\n", 0o755)
            self._write(root, "/usr/bin/wrapper", "#!/bin/sh\nexec /usr/lib/foo/helper\n", 0o755)
            self._write(
                root,
                "/usr/share/applications/ok.desktop",
                "[Desktop Entry]\nExec=foo\nIcon=ok\n",
            )
            self._write(
                root,
                "/usr/lib/systemd/system/foo.service",
                "[Service]\nExecStart=/usr/lib/foo/helper\n",
            )
            result = self._run_gate(root, owned)
            self._assert_pass(result)

    def test_elf_arch_non_executable_elf_and_linkage_failures(self) -> None:
        cases = (
            ("wrong-arch", 0o755, "elf-arch"),
            ("paseo-cli", 0o644, "non-executable-elf"),
            ("not-found", 0o755, "elf-not-found"),
        )
        for name, mode, reason in cases:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                virtual = f"/usr/lib/stably-orca/bin/{name}"
                self._write(root, virtual, b"\x7fELF\x02\x01\x01", mode)
                result = self._run_gate(root, [virtual], fake_tools=True)
                self._assert_failure(result, reason)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            virtual = "/usr/lib/stably-orca/node_modules/native.node"
            self._write(root, virtual, b"\x7fELF\x02\x01\x01", 0o644)
            result = self._run_gate(root, [virtual], fake_tools=True)
            self._assert_pass(result)

    def test_i686_rejects_wrong_elf_class_machine_and_unresolved_loader(self) -> None:
        cases = (
            ("valid-i686", b"\x01", None),
            ("valid-i686", b"\x02", "elf-class"),
            ("wrong-machine", b"\x01", "elf-arch"),
            ("ldd-error", b"\x01", "elf-loader"),
        )
        for name, elf_class, reason in cases:
            with self.subTest(name=name, elf_class=elf_class), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                virtual = f"/usr/lib/axiom/{name}"
                self._write(root, virtual, b"\x7fELF" + elf_class + b"\x01\x01", 0o755)
                result = self._run_gate(root, [virtual], fake_tools=True, arch="i686")
                if reason is None:
                    self._assert_pass(result)
                else:
                    self._assert_failure(result, reason)

    def test_missing_node_dependency_and_present_control(self) -> None:
        package_json = "/usr/lib/node_modules/@code-yeongyu/senpi/package.json"
        chunk = "/usr/lib/node_modules/@code-yeongyu/senpi/dist/bundle/chunk.js"
        owned = [package_json, chunk]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write(root, package_json, '{"dependencies": {"@earendil-works/pi-pty": "^1.0.0"}}\n')
            self._write(root, chunk, 'import { open } from "@earendil-works/pi-pty";\n')
            result = self._run_gate(root, owned)
            self._assert_failure(result, "missing-node-dependency")

            dep = "/usr/lib/node_modules/@earendil-works/pi-pty/package.json"
            self._write(root, dep, "{}\n")
            result = self._run_gate(root, owned + [dep])
            self._assert_pass(result)

    def test_third_party_node_modules_and_optdepends_systemd_and_arch_labeled_elf(self) -> None:
        owned = [
            "/usr/lib/stably-orca/node_modules/electron-updater/out/NsisUpdater.js",
            "/usr/lib/node_modules/vercel/node_modules/@vercel/fun/dist/src/runtimes/nodejs/bootstrap",
            "/usr/lib/systemd/user/stably-orca-kwin-virtual@.service",
            "/usr/lib/stably-orca/ripgrep/linux-arm64/wrong-arch",
        ]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write(
                root,
                owned[0],
                "join(process.resourcesPath, 'elevate.exe')\n",
            )
            self._write(
                root,
                owned[1],
                "#!/bin/sh\nexport NODE_PATH=/opt/nodejs/node_modules\n",
                0o755,
            )
            self._write(
                root,
                owned[2],
                "[Service]\nExecStart=/usr/bin/kwin_wayland --virtual\n",
            )
            self._write(root, owned[3], b"\x7fELF\x02\x01\x01", 0o755)
            result = self._run_gate(
                root,
                owned,
                fake_tools=True,
                package_info="\toptdepends = kwin: optional KWin virtual Wayland compositor service\n",
            )
            self._assert_pass(result)


class VerifyPackageDriverTest(unittest.TestCase):
    def _make_pkg(self, root: Path, arch_decl: str = "('any')") -> Path:
        pkg = root / "demo"
        pkg.mkdir(parents=True, exist_ok=True)
        (pkg / "PKGBUILD").write_text(
            textwrap.dedent(
                f"""\
                pkgname=demo
                pkgver=1.0.0
                pkgrel=1
                pkgdesc='Demo package'
                arch={arch_decl}
                url='https://example.com'
                license=('MIT')
                package() {{ :; }}
                """
            ),
            encoding="utf-8",
        )
        return pkg

    def _make_fake_docker(self, root: Path, behavior_body: str) -> tuple[Path, Path]:
        bindir = root / "bin"
        bindir.mkdir(parents=True, exist_ok=True)
        calls_log = root / "docker-calls.log"
        docker = bindir / "docker"
        docker.write_text(
            "#!/usr/bin/env python3\n"
            "import json, os, pathlib, sys\n"
            f"log = pathlib.Path({str(calls_log)!r})\n"
            "args = sys.argv[1:]\n"
            "arch = ''\n"
            "out_dir = ''\n"
            "cache_dir = ''\n"
            "for i, a in enumerate(args):\n"
            "    if a == '-e' and i + 1 < len(args) and args[i + 1].startswith('ARCH='):\n"
            "        arch = args[i + 1].split('=', 1)[1]\n"
            "    if a == '-v' and i + 1 < len(args):\n"
            "        src, dst = args[i + 1].split(':', 1)\n"
            "        if dst == '/out': out_dir = src\n"
            "        if dst == '/cache': cache_dir = src\n"
            "with log.open('a', encoding='utf-8') as f:\n"
            "    f.write(json.dumps({'arch': arch, 'cache': cache_dir, 'args': args}) + '\\n')\n"
            + behavior_body,
            encoding="utf-8",
        )
        docker.chmod(0o755)
        return bindir, calls_log

    def test_inner_container_script_is_valid_bash_and_orders_cachedir(self) -> None:
        script = VERIFY_SH.read_text(encoding="utf-8")
        marker = "read -r -d '' INNER <<'EOF' || true\n"
        start = script.index(marker) + len(marker)
        end = script.index("\nEOF\n", start)
        inner = script[start:end]
        result = subprocess.run(
            ["bash", "-n"],
            input=inner,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(
            r"CacheDir = /var/cache/pacman/pkg/\nCacheDir = /cache/pacman/",
            inner,
        )
        self.assertIn('mv -T "$tmp_entry" "$cache_entry"', inner)

    def test_any_arch_host_selection_and_explicit_arch_override(self) -> None:
        import json

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pkg = self._make_pkg(root, "('any')")
            bindir, calls_log = self._make_fake_docker(
                root,
                "pathlib.Path(out_dir, 'reason').write_text('ok\\n')\nsys.exit(0)\n",
            )
            cache_dir = root / "cache"
            base_env = {
                **os.environ,
                "PATH": f"{bindir}:{os.environ.get('PATH', '')}",
                "VERIFY_PACKAGE_CACHE_DIR": str(cache_dir),
            }

            for host_arch, expected_arch in (("arm64", "aarch64"), ("x86_64", "x86_64")):
                calls_log.unlink(missing_ok=True)
                res = subprocess.run(
                    ["bash", str(VERIFY_SH), str(pkg)],
                    env={**base_env, "VERIFY_HOST_ARCH": host_arch},
                    text=True,
                    capture_output=True,
                    check=False,
                )
                self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
                self.assertIn(f"VERIFY demo {expected_arch} PASS ok", res.stdout)
                calls = [json.loads(line) for line in calls_log.read_text().splitlines()]
                self.assertEqual([c["arch"] for c in calls], [expected_arch])

            for explicit in ("x86_64", "aarch64"):
                calls_log.unlink(missing_ok=True)
                res = subprocess.run(
                    ["bash", str(VERIFY_SH), str(pkg), "--arch", explicit],
                    env={**base_env, "VERIFY_HOST_ARCH": "arm64"},
                    text=True,
                    capture_output=True,
                    check=False,
                )
                self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
                self.assertIn(f"VERIFY demo {explicit} PASS ok", res.stdout)

    def test_any_arch_fallback_only_on_aur_dependency_failure(self) -> None:
        import json

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pkg = self._make_pkg(root, "('any')")
            bindir, calls_log = self._make_fake_docker(
                root,
                "mode = os.environ['FAKE_MODE']\n"
                "if mode == 'fallback_ok' and arch == 'aarch64':\n"
                "    pathlib.Path(out_dir, 'reason').write_text('aur-dependency:only-x86\\n')\n"
                "    sys.exit(1)\n"
                "if mode == 'fallback_ok' and arch == 'x86_64':\n"
                "    pathlib.Path(out_dir, 'reason').write_text('ok\\n')\n"
                "    sys.exit(0)\n"
                "if mode == 'build_fail':\n"
                "    pathlib.Path(out_dir, 'reason').write_text('build\\n')\n"
                "    sys.exit(1)\n"
                "pathlib.Path(out_dir, 'reason').write_text('aur-dependency:missing-everywhere\\n')\n"
                "sys.exit(1)\n",
            )
            env = {
                **os.environ,
                "PATH": f"{bindir}:{os.environ.get('PATH', '')}",
                "VERIFY_HOST_ARCH": "arm64",
                "VERIFY_PACKAGE_NO_CACHE": "1",
            }

            res = subprocess.run(
                ["bash", str(VERIFY_SH), str(pkg)],
                env={**env, "FAKE_MODE": "fallback_ok"},
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
            self.assertIn("VERIFY demo aarch64 SKIP aur-dependency:only-x86 (fallback x86_64)", res.stdout)
            self.assertIn("VERIFY demo x86_64 PASS ok", res.stdout)
            self.assertNotIn("FAIL", res.stdout)

            calls_log.unlink(missing_ok=True)
            res_build = subprocess.run(
                ["bash", str(VERIFY_SH), str(pkg)],
                env={**env, "FAKE_MODE": "build_fail"},
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(res_build.returncode, 1)
            calls = [json.loads(line) for line in calls_log.read_text().splitlines()]
            self.assertEqual([c["arch"] for c in calls], ["aarch64"])
            self.assertIn("VERIFY demo aarch64 FAIL build", res_build.stdout)

            res_both = subprocess.run(
                ["bash", str(VERIFY_SH), str(pkg)],
                env={**env, "FAKE_MODE": "both_fail"},
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(res_both.returncode, 1)
            self.assertIn("VERIFY demo x86_64 FAIL aur-dependency:missing-everywhere", res_both.stdout)

    def test_cache_mount_isolation_and_no_cache_controls(self) -> None:
        import json

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pkg = self._make_pkg(root, "('x86_64' 'i686')")
            bindir, calls_log = self._make_fake_docker(
                root,
                "pathlib.Path(out_dir, 'reason').write_text('ok\\n')\nsys.exit(0)\n",
            )
            cache_dir = root / "shared-cache"
            env = {
                **os.environ,
                "PATH": f"{bindir}:{os.environ.get('PATH', '')}",
                "VERIFY_PACKAGE_CACHE_DIR": str(cache_dir),
            }
            res = subprocess.run(
                ["bash", str(VERIFY_SH), str(pkg)],
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
            calls = [json.loads(line) for line in calls_log.read_text().splitlines()]
            self.assertEqual([c["arch"] for c in calls], ["x86_64", "i686"])
            self.assertTrue(calls[0]["cache"].endswith("/shared-cache/x86_64"))
            self.assertTrue(calls[1]["cache"].endswith("/shared-cache/i686"))
            self.assertTrue((cache_dir / "x86_64" / "pacman").is_dir())
            self.assertTrue((cache_dir / "i686" / "aur-pkgs").is_dir())

            calls_log.unlink(missing_ok=True)
            res_nc = subprocess.run(
                ["bash", str(VERIFY_SH), str(pkg), "--no-cache"],
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(res_nc.returncode, 0)
            nc_calls = [json.loads(line) for line in calls_log.read_text().splitlines()]
            self.assertEqual([c["cache"] for c in nc_calls], ["", ""])

    def test_sync_pacman_cache_aur_dep_key_and_pruning_behaviors(self) -> None:
        import time

        script = VERIFY_SH.read_text(encoding="utf-8")
        marker = "read -r -d '' INNER <<'EOF' || true\n"
        start = script.index(marker) + len(marker)
        end = script.index("\nEOF\n", start)
        inner = script[start:end]

        sync_fn = inner[
            inner.index("sync_pacman_cache() {\n") : inner.index("\ntrap 'rc=$?; sync_pacman_cache")
        ]
        key_fns = inner[
            inner.index("resolve_installed_dep_versions() {\n") : inner.index("\naur_dep() {\n")
        ]

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            src_pkg = root / "pacman-pkg"
            dst_pkg = root / "cache-pacman"
            src_pkg.mkdir()
            dst_pkg.mkdir()
            (src_pkg / "foo-1.0-1-aarch64.pkg.tar.xz").write_bytes(b"pkgdata")
            (src_pkg / "foo-1.0-1-aarch64.pkg.tar.xz.sig").write_bytes(b"sigdata")
            (src_pkg / "bar-1.0-1-aarch64.pkg.tar.xz.part").write_bytes(b"partial")
            (src_pkg / "orphan-1.0-1-aarch64.pkg.tar.xz.sig").write_bytes(b"orphansig")

            adapted_sync = sync_fn.replace("/var/cache/pacman/pkg", str(src_pkg)).replace(
                "/cache/pacman", str(dst_pkg)
            )
            res = subprocess.run(
                ["bash", "-c", f"set -euo pipefail\ncache_enabled=1\n{adapted_sync}\nsync_pacman_cache"],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(res.returncode, 0, res.stderr)
            self.assertEqual(
                sorted(p.name for p in dst_pkg.iterdir()),
                ["foo-1.0-1-aarch64.pkg.tar.xz", "foo-1.0-1-aarch64.pkg.tar.xz.sig"],
            )

            fake_bin = root / "fake-pacman"
            fake_bin.mkdir()
            pacman_stub = fake_bin / "pacman"
            qi_file = root / "pacman-qi.txt"
            qi_file.write_text(
                "Name            : bash\n"
                "Version         : 5.3.0-1\n"
                "Provides        : sh\n\n"
                "Name            : python\n"
                "Version         : 3.14.1-1\n"
                "Provides        : None\n",
                encoding="utf-8",
            )
            pacman_stub.write_text(
                f"#!/bin/sh\ncat {str(qi_file)!r}\n",
                encoding="utf-8",
            )
            pacman_stub.chmod(0o755)

            dep_dir = root / "aur-dep"
            dep_dir.mkdir()
            (dep_dir / "PKGBUILD").write_text("pkgname=dep\npkgver=1.0\n", encoding="utf-8")

            resolved_map = subprocess.run(
                ["bash", "-c", f"set -euo pipefail\n{key_fns}\nresolve_installed_dep_versions"],
                env={**os.environ, "PATH": f"{fake_bin}:{os.environ.get('PATH', '')}"},
                text=True,
                capture_output=True,
                check=True,
            ).stdout
            self.assertIn("sh\tbash=5.3.0-1\n", resolved_map)

            def compute_key(deps: str = "sh python") -> str:
                out = subprocess.run(
                    [
                        "bash",
                        "-c",
                        f"set -euo pipefail\nARCH=aarch64\n{key_fns}\naur_dep_cache_key {str(dep_dir)!r} {deps}",
                    ],
                    env={**os.environ, "PATH": f"{fake_bin}:{os.environ.get('PATH', '')}"},
                    text=True,
                    capture_output=True,
                    check=True,
                )
                return out.stdout.strip()

            k1 = compute_key()
            k1_sh = compute_key("sh")
            self.assertEqual(len(k1), 64)
            self.assertEqual(k1, compute_key())

            # Bumping ONLY the provider ('bash') of virtual dependency 'sh' invalidates key
            qi_file.write_text(
                "Name            : bash\n"
                "Version         : 5.3.1-1\n"
                "Provides        : sh\n\n"
                "Name            : python\n"
                "Version         : 3.14.1-1\n"
                "Provides        : None\n",
                encoding="utf-8",
            )
            self.assertNotEqual(k1_sh, compute_key("sh"))
            k_virtual = compute_key()
            self.assertNotEqual(k1, k_virtual)

            # Direct dependency ('python') version upgrade also invalidates key
            qi_file.write_text(
                "Name            : bash\n"
                "Version         : 5.3.1-1\n"
                "Provides        : sh\n\n"
                "Name            : python\n"
                "Version         : 3.15.0-1\n"
                "Provides        : None\n",
                encoding="utf-8",
            )
            k2 = compute_key()
            self.assertNotEqual(k_virtual, k2)

            # Changing PKGBUILD invalidates key
            (dep_dir / "PKGBUILD").write_text("pkgname=dep\npkgver=1.1\n", encoding="utf-8")
            k3 = compute_key()
            self.assertNotEqual(k2, k3)

            # VCS pkgver() and SKIP checksum exclusion regex
            guard = r"""! grep -Eq '^[[:space:]]*pkgver[[:space:]]*\(\)|'\''SKIP'\''|"SKIP"' "$1" """
            for bad_content in ("pkgname=x\npkgver() { echo 1; }\n", "sha256sums=('SKIP')\n", 'sha256sums=("SKIP")\n'):
                p = root / "check-pkgbuild"
                p.write_text(bad_content, encoding="utf-8")
                chk = subprocess.run(["bash", "-c", guard, "_", str(p)], check=False)
                self.assertNotEqual(chk.returncode, 0, bad_content)
            (root / "check-pkgbuild").write_text("pkgname=ok\nsha256sums=('abc123')\n", encoding="utf-8")
            chk_ok = subprocess.run(["bash", "-c", guard, "_", str(root / "check-pkgbuild")], check=False)
            self.assertEqual(chk_ok.returncode, 0)

            # Host-side pruning of >120m .tmp.* and >30d entries while preserving fresh entries
            pkg = self._make_pkg(root, "('x86_64')")
            bindir, _ = self._make_fake_docker(
                root,
                "pathlib.Path(out_dir, 'reason').write_text('ok\\n')\nsys.exit(0)\n",
            )
            prune_cache = root / "prune-cache"
            pac_dir = prune_cache / "x86_64" / "pacman"
            aur_dir = prune_cache / "x86_64" / "aur-pkgs"
            pac_dir.mkdir(parents=True)
            aur_dir.mkdir(parents=True)
            old_tmp = pac_dir / ".tmp.123.stale"
            old_tmp.write_text("x", encoding="utf-8")
            old_pkg = pac_dir / "old-1.0-1-x86_64.pkg.tar.zst"
            old_pkg.write_text("x", encoding="utf-8")
            fresh_pkg = pac_dir / "fresh-1.0-1-x86_64.pkg.tar.zst"
            fresh_pkg.write_text("x", encoding="utf-8")
            old_aur = aur_dir / "oldkey"
            old_aur.mkdir()
            fresh_aur = aur_dir / "freshkey"
            fresh_aur.mkdir()
            three_hours_ago = time.time() - 3 * 3600
            thirty_one_days_ago = time.time() - 31 * 86400
            os.utime(old_tmp, (three_hours_ago, three_hours_ago))
            os.utime(old_pkg, (thirty_one_days_ago, thirty_one_days_ago))
            os.utime(old_aur, (thirty_one_days_ago, thirty_one_days_ago))

            prune_res = subprocess.run(
                ["bash", str(VERIFY_SH), str(pkg)],
                env={
                    **os.environ,
                    "PATH": f"{bindir}:{os.environ.get('PATH', '')}",
                    "VERIFY_PACKAGE_CACHE_DIR": str(prune_cache),
                },
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(prune_res.returncode, 0, prune_res.stdout + prune_res.stderr)
            self.assertFalse(old_tmp.exists())
            self.assertFalse(old_pkg.exists())
            self.assertFalse(old_aur.exists())
            self.assertTrue(fresh_pkg.exists())
            self.assertTrue(fresh_aur.exists())



if __name__ == "__main__":
    unittest.main()
