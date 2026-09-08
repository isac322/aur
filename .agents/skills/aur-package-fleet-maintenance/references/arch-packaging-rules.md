# Arch/AUR 공식 패키징 규칙

`SKILL.md`가 절차를 규정하고 이 파일이 PKGBUILD 내용 규칙을 규정한다. 여기에는 이 fleet에서 실제로
위반됐거나 경계 판정이 반복 필요한 규칙만 둔다. ArchWiki 요약이나 변수·함수 목록은 두지 않는다.
변수·함수 schema는 `PKGBUILD(5)`와 `makepkg --printsrcinfo`가 executable source of truth다.

각 항목의 `[위반 N]`은 2026-09-07 기준 이 workspace 68개 PKGBUILD 실측값이다. 감사할 때 다시 센다.

## license

<https://wiki.archlinux.org/title/PKGBUILD#license>

- Arch는 SPDX 식별자를 쓴다. `license` 값은 SPDX 목록에 존재해야 하고 `/usr/share/licenses/`에
  대응 항목이 있어야 한다. `LGPL3`, `AGPL3`, `Apache`, `unknown` 같은 값은 식별자가 아니다.
  legacy 이름은 `/usr/share/licenses/common`이 있을 때만 허용되며 이 호스트에는 없다. `[위반 7]`
- `licenses` 패키지가 `/usr/share/licenses/spdx/`로 제공하는 식별자는 텍스트를 따로 설치하지 않는다.
  `Apache-2.0`, `LGPL-3.0-or-later`, `AGPL-3.0-only`, `GPL-3.0-or-later`가 여기 해당한다.
  `python -c "import os;print(os.listdir('/usr/share/licenses/spdx'))"`로 확인한다.
- MIT, BSD-*, ISC 등 license *계열*과 custom은 텍스트를 직접 설치해야 한다.
  `install -Dm644 LICENSE -t "$pkgdir/usr/share/licenses/$pkgname/"`
- 계열에 속하지 않는 custom은 `LicenseRef-<name>`을 쓴다. 맨 `custom`은 부적합하고 `custom:<name>`은
  ArchWiki가 허용하지만 `namcap`이 `unknown-spdx-license-identifier`로 거부한다. `namcap`은
  `LicenseRef-` 접두사만 예외 처리하므로 둘 다 만족하는 형태는 `LicenseRef-`다.
- 복합 license는 SPDX 식으로 한 문자열에 쓴다. 예: `'Apache-2.0 WITH LLVM-exception'`
- **upstream이 license 파일을 제공하지 않는 경우를 위반으로 처리하지 않는다.** upstream 부재를
  PKGBUILD 주석으로 문서화했으면 그것이 의도된 상태다. `meridian`이 이 경우이며 직접 확인 없이
  license 텍스트를 만들어 넣지 않는다. upstream에 license 자체가 없으면 유효한 SPDX 식별자가
  존재하지 않으므로 값을 바꾸기 전에 사용자에게 확인한다. `alias-tips-git`, `unegg`가 이 경우다.

## arch

<https://wiki.archlinux.org/title/PKGBUILD#arch>

- `any`는 빌드 결과가 architecture 독립일 때만 쓴다. 컴파일러나 native module을 쓰면 `any`가 아니다. `[위반 0]`
- native package는 지원하는 architecture를 모두 열거한다. AUR 기준은 `x86_64`이며 검증된 것만 추가한다.
- 같은 `arch` 값에서 build 결과가 빌드 호스트에 따라 달라지면 안 된다. `/proc/cpuinfo`, `uname`,
  host 설치 여부로 payload를 고르면 다른 호스트에서 SIGILL이나 오작동이 된다. microarchitecture
  변형이 필요하면 별도 package로 분리하거나 baseline을 고정한다. `[위반 1]`

## 이름과 변형

<https://wiki.archlinux.org/title/AUR_submission_guidelines>,
<https://wiki.archlinux.org/title/Nonfree_applications_package_guidelines#Package_naming>,
<https://wiki.archlinux.org/title/VCS_package_guidelines>

접미사 결정 규칙. 위에서 아래로 처음 맞는 것을 적용한다.

1. 고정 release의 source를 실제 빌드하면 접미사 없음.
2. branch/HEAD를 추적하면 `-git`. git URL이어도 `#tag=`/`#commit=`로 고정했으면 `-git`이 아니다.
3. source가 공개돼 있는데 upstream이 만든 주 실행 산출물을 내려받아 설치하면 `-bin`.
4. source 미공개 proprietary는 `-bin`을 붙이지 않는다. Java prebuilt JAR도 예외다.
5. npm registry `.tgz`는 Node 공식 guideline의 정상 source 형태다. tarball이라는 이유나 `build()`가
   없다는 이유로 `-bin`이 되지 않는다.
6. 주 명령이 JS이고 `.node`/esbuild 같은 native payload가 dependency나 resource면 접미사 없음.
   주 명령 자체가 arch별 prebuilt면 `-bin`.
7. `build()` 존재는 판정 기준이 아니다. prebuilt를 받고 completion만 생성하는 `build()`도 있다.

이름 충돌과 package 관계.

- 공식 repo와 같은 `pkgname`을 쓰지 않는다. extra feature를 제공하더라도 이름을 달리한다. `[위반 0]`
- `pkgname`은 자동으로 provide되므로 `provides`에 다시 넣지 않는다.
- `provides`에는 버전을 명시한다. `provides=("foo=$pkgver")`. 버전 없는 provide는 versioned
  dependency를 만족시키지 못한다.
- `-git` 등 VCS 변형은 canonical 이름을 versioned provide하고 stable counterpart를 conflict한다.
  VCS 도구를 `makedepends`에 넣는다. `[위반 6]`
- 같은 파일이나 명령을 설치하는 로컬 변형은 공통 virtual을 provide하고 모든 형제와 **양방향**
  conflict를 선언한다. 한쪽만 선언하면 설치는 막히지만 의도가 문서화되지 않는다. `[일방 3]`
- **relation을 추가하기 전에 대상 이름이 실재하는지 확인한다.** `aur.archlinux.org/rpc/v5/info`와
  `pacman -Si`로 조회한다. 존재하지 않으면 추가는 무해하다. 실재하고 파일이 겹치면 기존 사용자에게
  새 충돌이 생기므로 기계적으로 넣지 않고 파일 겹침과 `epoch`를 확인한 뒤 사용자에게 확인한다.
  `nimf-libhangul-git`은 AUR `nimf`(epoch `1:`)와 같은 파일을 설치하므로 이 경우다.
- `replaces`는 실제 rename 이력에만 쓴다. 대체 구현을 제공할 때는 `provides`/`conflicts`를 쓴다.
- output이나 ABI가 다르면 별도 family다. Python binding과 C grammar는 공존 가능하므로 conflict하지 않는다.

## dependency 선언

<https://wiki.archlinux.org/title/PKGBUILD#makedepends>

- `base-devel` 구성원을 `makedepends`나 `checkdepends`에 넣지 않는다. `makepkg`가 설치를 가정한다.
  현재 구성원은 `LC_ALL=C pacman -Si base-devel`로 확인한다. `[위반 2]`
- `depends`는 전이 설치 여부와 무관하게 1단계 직접 의존을 모두 선언한다. `glibc`나 이미 다른
  `python-*`를 통해 보장되는 `python`처럼 제거 불가능한 항목은 생략해도 된다.
- upstream 배포명을 Arch 이름으로 추측하지 않는다. 설치 파일이나 `provides`로 확인한다.
- `optdepends`는 `'package: feature'` 형식을 쓴다.

## 경로

<https://wiki.archlinux.org/title/Arch_package_guidelines>

- 단일 실행 파일은 `/usr/bin`, package data는 `/usr/share/$pkgname`에 둔다.
- `/opt/$pkgname` + `/usr/bin` symlink는 sibling asset을 실제 경로 기준으로 찾는 self-contained
  bundle에만 쓴다. 단일 script를 `/opt`에 두는 것은 예외에 해당하지 않는다. `[위반 1]`
- `/usr/local`은 쓰지 않는다. `backup`은 앞 `/` 없는 상대 경로만 받고 wildcard를 지원하지 않는다.
- pacman install hook은 `.install` 파일에만 정의한다. PKGBUILD 안의 `post_install()`은
  pacman에 전달되지 않으며 custom function은 `_` 접두사 규칙을 따른다. `[위반 1]`

## 재현 가능한 빌드

- 1회성 patch와 source 수정은 `build()`가 아니라 `prepare()`에서 한다. `[위반 1]`
- Rust는 `--locked`/`--frozen`/`--offline`으로 lockfile을 고정한다. Meson, CMake 같은 wrapper가
  `cargo`를 호출하는 경우도 포함이며 `cargo` 문자열 grep만으로는 놓친다. `[위반 1]`
- Python은 `python -m build --wheel --no-isolation` 뒤 `python -m installer --destdir="$pkgdir"`를 쓴다.
- Go는 `CGO_ENABLED`, PIE, trimpath, external link flag를 명시하고 `GOPATH`/cache를 격리한다.
- `check()`가 network를 요구하면 비활성화하거나 이유를 남긴다.

## 메타데이터 스타일

<https://wiki.archlinux.org/title/PKGBUILD#pkgdesc>

- `pkgdesc`는 80자 이하를 **권고**한다. mandatory가 아니므로 초과만으로 수정하지 않는다. 이 fleet의
  초과 11건은 변형 구분 목적이 확인됐다. `(prebuilt binary)`, `(Built from source)`, `support for
  baseline CPUs`처럼 같은 upstream의 형제 패키지를 구별하는 정보는 유지한다. `[의도적 초과 11]`
- `pkgdesc`에 package 이름을 자기지시형으로 넣지 않는다. 단 **애플리케이션 이름이 package 이름과
  다르면 예외**다. `nimf-libhangul-git`의 `Nimf is ...`와 `python-clikit-git`의 `CliKit is ...`는
  예외에 해당하므로 위반이 아니다. `[위반 0, 예외 3]`
- PKGBUILD 첫 주석 블록에 `# Maintainer: Name <email>`을 둔다. 이전 관리자는 `# Contributor:`로 남긴다. `[위반 2]`
- `pkgver`에 하이픈을 쓰지 않는다. upstream이 쓰면 `_`로 바꾼다.
- `pkgver` 변경 시 `pkgrel=1`. `epoch`는 버전 체계가 깨졌을 때만 쓴다.
