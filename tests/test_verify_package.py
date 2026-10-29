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
            else
              echo 'Machine: Advanced Micro Devices X86-64'
            fi
            ;;
          -l)
            if [ "$name" = paseo-cli ]; then echo 'INTERP /lib64/ld-linux-x86-64.so.2'; fi
            ;;
          -d)
            case "$name" in
              not-found|paseo-cli|native.node) echo 'Dynamic section'; echo 'NEEDED';;
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
                "x86_64",
                str(pre),
                post_arg,
                str(file_list),
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


if __name__ == "__main__":
    unittest.main()
