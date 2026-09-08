# Package-specific durable notes

이 문서는 공통 AUR 유지보수 절차만으로 놓치기 쉬운 비직관적 계약을 기록한다. 현재 버전, checksum, release commit, 임시 파일명처럼 빠르게 stale해지는 값은 기록하지 않는다.

각 항목은 감사의 출발점이다. 적용 전에 현재 PKGBUILD, upstream metadata와 release artifact에서 여전히 유효한지 재검증한다. 계약이 사라졌으면 이 문서에서도 제거한다.

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

## `stably-orca`

- upstream release source에는 Electron/Node native dependency와 architecture-specific resource가 포함될 수 있다. package manager lockfile만 보고 dependency 변경을 단정하지 않는다.
- source archive root, package manager version, native/prebuilt resource와 package staging을 새 release마다 다시 확인한다.
- 원격 AUR 변경과 로컬 packaging 변경이 갈라져 있을 수 있으므로 배포 전에 양쪽 diff를 비교하고 검증된 packaging 변경을 보존한다.
- packaged CLI는 별도 system Node가 아니라 `ELECTRON_RUN_AS_NODE=1`로 system Electron runtime을 사용한다. `namcap`의 `node` shebang 감지는 wrapper 실행 계약을 확인한 뒤 false positive 여부를 판정한다.
- 번들된 node-pty, parcel watcher, sherpa-onnx와 browser binary의 ELF dependency 때문에 `glibc`와 `gcc-libs`를 직접 runtime dependency로 유지한다.

## 정적 Go `-bin` 패키지

특정 package base가 아니라 반복되는 특수 유형이다.

- `file`/ELF NEEDED로 정적 링크 여부를 확인한다.
- upstream source의 외부 명령 실행 경로를 조사해 필수 dependency와 optional integration을 구분한다.
- HTTPS가 핵심 기능이면 정적 ELF라도 시스템 CA bundle 요구사항을 확인한다.
- `namcap`만으로 외부 명령과 런타임 데이터 dependency를 판정하지 않는다.
