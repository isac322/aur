# Package-specific durable notes

이 문서는 공통 AUR 유지보수 절차만으로 놓치기 쉬운 비직관적 계약을 기록한다. 현재 버전, checksum, release commit, 임시 파일명처럼 빠르게 stale해지는 값은 기록하지 않는다.

각 항목은 감사의 출발점이다. 적용 전에 현재 PKGBUILD, upstream metadata와 release artifact에서 여전히 유효한지 재검증한다. 계약이 사라졌으면 이 문서에서도 제거한다.

## `opencode-desktop`

- v2는 desktop renderer만 빌드해서는 충분하지 않으며, source-built CLI를 먼저 만들고 desktop build에 `OPENCODE_CLI_DIST`로 그 dist 경로를 전달해야 한다.
- system Electron44 계약과 main process의 lazy chunks를 함께 검증한다. `packages/desktop/out/main` 산출물을 임의로 제거하거나 평탄화하지 않는다.
- electron-builder의 architecture별 unpacked output directory는 `$CARCH`에 따라 `linux-unpacked` 또는 `linux-arm64-unpacked`이므로 x86_64 경로를 고정 가정하지 않는다.
- v2 stable tag에는 GitHub Release object가 없고 게시된 Release는 v1에 남아 있으므로 `audit-overrides.toml`의 `selection = "tag"`는 의도된 설정이다.
- 이 항목에는 빠르게 stale해지는 v2 version/commit 상수를 기록하지 않는다. 현재 tag/ref와 packaging contract를 매번 upstream 및 PKGBUILD에서 재검증한다.
- upstream desktop build가 아이콘 파일명에서 `.desktop` 접미사를 제거(`ai.opencode.png`)할 수 있으므로 hicolor icon 수집 경로가 실제 생성 파일명과 일치하는지 확인한다.

## `codex-lb`

- Python package이지만 dashboard 정적 자산, CLI entry point, DB helper, systemd service, sysusers와 tmpfiles까지 하나의 패키징 계약을 이룬다. Python wheel만 성공했다고 전체 패키지가 검증된 것은 아니다.
- 일반 `makepkg`가 로컬 AUR dependency 부족으로 불가능할 수 있다. 이 경우 source checksum, 격리된 PEP 517 wheel build, wheel contents, console entry points, static dashboard assets, staged filesystem layout, service/sysusers/tmpfiles/env 파일을 각각 검증하고 실행하지 못한 전체 build 공백을 명시한다.
- upstream dependency metadata와 PKGBUILD dependency를 비교하되 package build backend 자체가 요구하는 dependency와 runtime dependency를 구분한다.

## `paseo-cli` / `paseo-cli-bun`

- npm package의 `node-pty` prebuild가 여러 OS와 아키텍처를 포함할 수 있다. target Linux 아키텍처에 필요한 prebuild만 남았는지 package staging에서 확인한다.
- Node runtime 변형과 Bun runtime 변형은 upstream tarball을 공유할 수 있지만 wrapper, dependency, conflicts/provides와 runtime 실행 경로가 다르므로 별도로 smoke test한다.
- pruning 규칙을 바꿀 때 현재 upstream `node-pty` 디렉터리 구조를 먼저 확인한다. 과거의 prebuild 이름을 고정 가정하지 않는다.

## `pi-coding-agent-bin`

- `source_x86_64`와 `source_aarch64`를 함께 제공하므로 x86_64 호스트의 `updpkgsums`에 의존하지 않는다. 새 tag의 `pi-linux-x64.tar.gz`와 `pi-linux-arm64.tar.gz`를 각각 직접 다운로드하고 SHA-256을 계산한 뒤 release의 `SHA256SUMS`와 대조한다.
- tag별 raw LICENSE도 직접 다시 다운로드해 해시한다. LICENSE가 이전 tag와 byte-identical하면 `sha256sums` 값은 바뀌지 않는 것이 맞으며, 동일 content/hash 근거를 남긴다.

## `senpi`

- upstream의 최소 Node runtime 요구사항을 package metadata에서 확인한다.
- clipboard와 TUI/PTY 계열 native asset이 architecture별로 올바르게 포함되는지 확인한다. npm package 자체 버전만 맞는 것으로 충분하지 않다.
- standalone/native payload는 strip 또는 재배치가 실행을 깨뜨릴 수 있으므로 package options와 실제 실행을 함께 검증한다.

## `cloudflare-wrangler`

- npm tarball에 LICENSE가 없으므로 npm provenance attestation의 source commit을 검증하고 해당 commit의 dual-license 파일을 고정해서 가져온다.
- 번들된 workerd, esbuild, sharp native payload의 target architecture와 실제 ELF dependency를 확인한다. Node.js의 전이 의존성만 믿지 말고 직접 필요한 `glibc`/`gcc-libs`를 PKGBUILD에 선언한다.
- npm이 install script를 차단했다는 경고만으로 실패를 단정하지 않는다. staged package의 `wrangler`, workerd와 esbuild를 직접 실행해 번들 payload가 유효한지 확인한다.

## `python-claude-agent-sdk`

- upstream `pyproject.toml`의 runtime dependency를 기준으로 감사한다. `typing_extensions`는 Python version marker를 확인하고, 예제나 optional backend의 `trio` import를 무조건 runtime dependency로 승격하지 않는다.
- `namcap`의 Python import 판정은 conditional/optional import를 구분하지 못할 수 있다. wheel metadata와 실제 import guard를 함께 확인한다.

## `python-ouroboros-ai`

- upstream project metadata에 누락된 runtime dependency가 있을 수 있다. CLI/update-notice source import와 staged package의 `namcap` 결과를 함께 확인한다.
- `packaging.version`처럼 CLI 실행 경로에서 직접 import되는 모듈은 upstream metadata 누락 여부와 무관하게 직접 runtime dependency로 선언한다.

## `posthog-cli` / `posthog-cli-bin`

- `posthog-cli api`는 Node.js로 실행되는 MCP/API bundle을 요구한다. release binary는 bundle을 embed하지만 source tag archive에는 생성물이 없으므로, source package는 upstream `services/mcp`의 `build:cli:release`를 먼저 실행한 뒤 Rust binary를 빌드한다.
- MCP bundle 생성용 `pnpm install`은 `--ignore-scripts`로 제한해 CLI bundle에 불필요한 monorepo native install script를 실행하지 않는다. 생성된 binary를 staged package에서 `posthog-cli api --agent-help`로 검증한다.
- 일반 명령은 Node.js 없이 동작하므로 `nodejs`는 `api` subcommand용 optional dependency로 유지한다.

## `vercel-node`

- 이 AUR package의 추적 대상은 npm package `vercel` CLI다. 이름이 비슷한 `@vercel/node` builder package로 잘못 전환하지 않는다.
- package가 제공하는 `vercel`과 `vc` command를 모두 확인한다.
- optional native helper와 local runtime emulation dependency는 실제 포함 파일 및 fallback 동작을 기준으로 감사한다.

## `python-tcafe-attending-bot`

- upstream Python build backend와 source distribution metadata를 기준으로 PEP 517 build dependency를 확인한다. 과거 setuptools 가정을 유지하지 않는다.
- XDG 경로 dependency는 upstream import와 metadata를 함께 확인한다. 모듈 교체 시 Arch package 이름도 다시 매핑한다.
- Python package뿐 아니라 함께 설치되는 systemd service와 timer의 command, path와 실행 계약을 보존한다.

## `graphify`

- upstream Python extra의 dependency 이름을 Arch package 이름으로 직접 매핑하지 않는다.
- upstream의 `tree-sitter-commonlisp`는 Python binding을 요구하지만, AUR의 동명 `tree-sitter-commonlisp` package는 C grammar library이며 Python module을 제공하지 않는다.
- `python-tree-sitter-commonlisp`처럼 실제로 존재하지 않는 package 이름을 추측해 추가하지 않는다. 적합한 binding package가 확인될 때까지 해당 optional dependency는 unmapped 상태로 보고한다.

## `openvpn3-git`

- VCS package의 stale 여부는 AUR에 기록된 `pkgver` 문자열과 upstream branch HEAD를 비교해 판정한다.
- upstream의 tag가 branch HEAD보다 뒤처질 수 있다. PKGBUILD의 실제 `pkgver()` 공식을 target checkout에서 실행해 예상 버전을 재계산한다.
- 기존 `pkgver`의 commit suffix가 현재 upstream history에 존재하는지 확인하고 branch 변경이나 history rewrite와 단순 stale 상태를 구분한다.
- upstream `gdbuspp` 및 AUR `gdbuspp-git`이 `-m64` 컴파일러 체크로 `x86_64`만 지원하므로 architecture 선언 정합성을 유지하며, 라이브 시스템 D-Bus 데몬이 없는 컨테이너 환경의 `check()`는 `--suite standalone` 테스트 스위트를 실행한다.

## `stably-orca`

- upstream release source에는 Electron/Node native dependency와 architecture-specific resource가 포함될 수 있다. package manager lockfile만 보고 dependency 변경을 단정하지 않는다.
- upstream은 release 게시 전후로 같은 버전 tag를 다른 commit으로 다시 찍은 적이 있다(1.4.211, 1.4.216). source는 `_commit`으로 고정한 `/archive/<commit>` archive만 쓰며 archive root는 `orca-<full commit>`이다. checker의 기본 GitHub archive pin 판정이 tag archive 회귀와 재태깅을 막는다.
- 새 버전은 release `target_commitish`, tag의 peeled commit과 archive 안 `package.json` version이 모두 일치할 때만 pin한다.
- source archive root, package manager version, native/prebuilt resource와 package staging을 새 release마다 다시 확인한다.
- 원격 AUR 변경과 로컬 packaging 변경이 갈라져 있을 수 있으므로 배포 전에 양쪽 diff를 비교하고 검증된 packaging 변경을 보존한다.
- packaged CLI는 별도 system Node가 아니라 `ELECTRON_RUN_AS_NODE=1`로 system Electron runtime을 사용한다. `namcap`의 `node` shebang 감지는 wrapper 실행 계약을 확인한 뒤 false positive 여부를 판정한다.
- 번들된 node-pty, parcel watcher, sherpa-onnx와 browser binary의 ELF dependency 때문에 `glibc`와 `gcc-libs`를 직접 runtime dependency로 유지한다.
- upstream `build:cli` 등 package script가 bare `pnpm`을 호출한다. `build()`가 고정한 `$srcdir/pnpm`을 `PATH` 앞에 두지 않으면 pnpm이 없는 clean container에서 `pnpm: command not found`로 실패한다. host에 설치된 pnpm으로 통과한 빌드는 검증으로 인정하지 않는다.
- Arch Linux ARM repo에는 `electron43`이 없다. aarch64 검증은 `scripts/verify-package.sh`가 AUR provider(`electron43-bin`)를 container 안에서 빌드해 충족한다.
- upstream은 commit archive(비-git 디렉터리)에서 `git rev-parse HEAD`를 호출하는 `build-source-commit.mjs`와 AppImage/glibc floor/Wayland clipboard 검증 훅, `build:web:from-renderer` 빌드 단계를 사용한다. 버전업 시 고정된 `_commit` 주입과 신규 `electron-builder` 훅 및 web bundle 타겟 누락 여부를 함께 확인한다.
- Linux Wayland text copy(terminal copy-on-select 및 editor copy 포함)는 `src/main/window/clipboard-text-publisher.ts`에서 `/usr/lib/stably-orca/bin/orca-wayland-clipboard`를 spawn하며 Electron fallback이 없다. 따라서 `wayland`(`libwayland-client.so.0` 및 `<wayland-client.h>`를 제공하는 패키지)는 직접 dependency로 유지한다.
- `build-wayland-clipboard.mjs`의 Docker는 Ubuntu 20.04 AppImage glibc 2.31 floor를 강제하기 위한 것이다. Arch `prepare()`에서는 `ensureBundledWaylandClipboard`, `verifyLinuxGlibcFloor`, `appImageNodeRuntime`만 우회할 수 있지만, `finalizePackagedWaylandClipboard(resourcesDir)`와 `extraResources`의 `native/wayland-clipboard/.build/${arch}/orca-wayland-clipboard` → `bin/orca-wayland-clipboard` 매핑은 반드시 유지한다.
- `build()`는 host `gcc`로 `wayland-clipboard.c`와 `x11-clipboard-publisher.c`를 컴파일해 `native/wayland-clipboard/.build/${wayland_arch}/orca-wayland-clipboard`를 생성한다. `check()`는 `native/wayland-clipboard/protocol_test.py`를 실행하고, `package()`는 `/usr/lib/stably-orca/bin/orca-wayland-clipboard`가 executable인지 assert한다.

## 정적 Go `-bin` 패키지

특정 package base가 아니라 반복되는 특수 유형이다.

- `file`/ELF NEEDED로 정적 링크 여부를 확인한다.
- upstream source의 외부 명령 실행 경로를 조사해 필수 dependency와 optional integration을 구분한다.
- HTTPS가 핵심 기능이면 정적 ELF라도 시스템 CA bundle 요구사항을 확인한다.
- `namcap`만으로 외부 명령과 런타임 데이터 dependency를 판정하지 않는다.
