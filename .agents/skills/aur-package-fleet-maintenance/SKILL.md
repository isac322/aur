---
name: aur-package-fleet-maintenance
description: >-
  이 AUR 워크스페이스의 패키지를 전수 수집해 공식 upstream, 변경 영향, 의존성과 패키징 계약을 감사하고,
  승인 범위 안에서 업데이트·검증·commit·AUR push하는 유일한 절차다. 버전업, 최신화, upstream 확인, AUR push 요청에 사용한다.
---

# AUR package fleet maintenance

이 스킬은 현재 워크스페이스의 canonical 실행 절차다. 과거 실행 결과와 upstream 응답 cache는 재사용하지 않지만, inventory 수집과 버전 판정 코드는 이 스킬의 `check.py`를 매 실행 재사용한다.

## 권위, 승인과 package notes

감사 시작 전에 workspace `AGENTS.md`를 읽는다. `AGENTS.md`는 다음 범위의 source of truth다.

- 패키지 수집 범위·upstream 그룹화와 공식 최신판 채널·GitHub stable release 필터·tag fallback·monorepo 규칙
- 변경 영향·의존성 감사 항목, `pkgver`/`pkgrel` 정책과 감사 전용 변경 전 보고 형식

이 스킬은 실행 상태, 안전 게이트, 검증·배포와 실행 후 완료 보고의 source of truth다. 위 범위 밖의 충돌은 이 스킬을 따르며, 특히 architecture별 checksum 규칙은 `AGENTS.md`의 일반 `updpkgsums` 허용보다 우선한다.
`AGENTS.md` 안의 `/releases/latest` 예시는 사용하지 않는다. stable release는 draft/prerelease를 제외하고 `published_at` 내림차순으로 고른다.

승인 게이트는 다음과 같다.

1. 최신판 확인, 영향·의존성 감사나 업데이트 후보만 요청받으면 전수 감사와 변경 전 보고만 하고 파일, Git과 원격을 바꾸지 않는다.
2. 특정 패키지나 fleet의 수정·버전업을 지시받으면 그 범위의 PKGBUILD 수정, 검증, 패키지별 원자적 commit, 모노레포 `origin` push와 각 AUR repository push까지 승인된 것으로 본다. commit/push를 다시 요청하거나 별도 확인을 기다리지 않는다.
3. 같은 요청에서 사용자가 commit이나 push를 명시적으로 금지하면 해당 범위만 배포하지 않고 `not_requested`로 보고한다.
4. 일괄 지시는 지정 fleet의 실행 승인이지만 요청하지 않은 cleanup, clone, 저장소·remote·권한 변경은 제외하며, 새 범위는 승인 범위만 끝내고 후보로 보고한다.

PKGBUILD 내용 규칙은 `references/arch-packaging-rules.md`가 규정한다. Step 3~5 전에 읽고 편집·검증에서 대조한다. 변수·함수 schema는 `PKGBUILD(5)`와 `makepkg --printsrcinfo`를 직접 쓴다.
대상이 `references/package-notes.md`에 있으면 감사 전에 읽는다. notes는 package별 비직관 계약의 출발점일 뿐 일반 규칙을 옮기지 않는다. 버전·checksum·asset·Git·remote·권한은 현재 PKGBUILD와 upstream에서 재검증한다.

## 재사용 감사 도구

inventory 수집과 upstream 최신판 확인은 반드시 `.agents/skills/aur-package-fleet-maintenance/check.py`로 시작한다. 같은 기능의 임시 Python·Shell script를 실행마다 다시 작성하지 않는다.

- 전체 수집: `python3 .agents/skills/aur-package-fleet-maintenance/check.py inventory --json`
- 전체 최신판 감사: `python3 .agents/skills/aur-package-fleet-maintenance/check.py audit --json`
- 편집·commit/push 직전 재확인: `python3 .agents/skills/aur-package-fleet-maintenance/check.py audit --json --only <pkgbase>`
- 표준 package는 PKGBUILD의 source와 url에서 channel을 자동 추론하고, generic 판정이 틀리는 예외만 `audit-overrides.toml`에 선언한다.
- 도구는 non-vendored PKGBUILD를 실행 시점에 동적으로 전수 발견한다. 새 package는 자동 포함하고 삭제된 package는 자동 제외하며 고정 package roster나 과거 inventory를 유지하지 않는다.
- 새 package의 channel을 추론하지 못하면 `UNMAPPED`를 숨기지 않고 완료를 막는다. 해당 package만 임시로 우회하지 말고 generic detector나 최소 override를 보완한다.
- checker가 현재 package를 잘못 판정하거나 지원 채널이 부족하면 일회성 대체 script로 우회하지 않고 checker와 필요한 fixture를 고친다.
- 재사용 대상은 코드와 예외 규칙뿐이다. 네트워크 응답, latest version, release/tag/VCS HEAD와 checksum 결과는 저장하지 않고 매 실행 공식 upstream에서 새로 조회한다.

## Inventory와 run tally

실행 중 tally는 `SSH permission bases ∪ local PKGBUILD bases`의 package base마다 한 행을 둔다. 별도 ledger, summary, proof 등 증적·원장·요약 파일은 만들지 않는다.
각 행은 identity(`path/pkgbase/pkgnames/upstream_group`), repository(`repository_state/aur_remote`), version(`current/latest/status`),
impact(`dependency/packaging`), outcome(`validation/disposition/commit_sha/push_status/remote_sha/failure_reason`)을 가진다.
조사·수정·검증·push 결과를 즉시 같은 인메모리 tally에 반영하며 최종 숫자와 표를 기억이나 수기 목록으로 다시 만들지 않는다.

상태축은 서로 합치지 않는다.

- version: `current|outdated|uncertain|untrackable`
- repository: `git|no_git|local_missing`
- validation: `full|partial|blocked|failed`
- disposition: `updated|intentionally_held|validation_blocked|validation_failed`
- push: `pushed|permission_denied|no_repository|remote_conflict|not_requested`

`intentionally_held`는 영향 감사를 마쳤지만 upstream asset·호환성 등 확인된 이유로 변경하지 않은 상태다.
`repository_state=no_git`은 조사 시 Git metadata가 없다는 뜻이고, `push_status=no_repository`는 승인된 로컬 변경·검증 후 commit/push할 저장소가 없다는 최종 처분이다.
`validation`은 검증 강도·결과이고 `disposition`은 outdated 처리 결과이므로 `full|partial`을 disposition 합계에 더하지 않는다.

기본은 `check.py`가 수행하는 단일 프로세스의 bounded 병렬 조회다. batch별 base를 유일하게 배정해 tally에 직접 넣고 입력·출력 pkgbase set equality와 중복 없음을 검산한다.
조회 실패나 최신판 판별 불가는 현재 `pkgver`를 `latest_version`으로 상속하지 않고 값을 비운 채 `uncertain`과 사유를 기록한다.
검산이 틀린 batch만 재조회한다. 그 사이 나온 release, tag나 VCS HEAD는 새 finding으로 영향 감사부터 처리하며 이미 push한 값을 자동 복원하지 않는다.

## Step 1: 전체 대상 식별

1. `ssh aur@aur.archlinux.org list-repos` 결과를 push 권한 집합으로 쓴다. 조회 불가 시 권한은 `uncertain`이며 aurweb maintainer 검색만으로 확정하지 않는다.
2. `check.py inventory --json` 결과를 로컬 대상 집합으로 쓴다. checker는 깊이를 고정하지 않고 non-vendored `PKGBUILD`를 재귀적으로 발견하므로 package 추가·삭제나 중첩 경로 변경을 정적 roster 없이 반영한다.
3. 결과에 `openvpn3/openvpn3/PKGBUILD`와 `openvpn3/openvpn3-git/PKGBUILD`가 포함됐는지 canary로 확인한다. 빠졌으면 별도 `find` 결과로 우회하지 않고 checker의 discovery 제외 규칙을 고친다.
4. package base와 repository 경로로 식별하고 split package, `.SRCINFO`, Git 상태와 remote URL을 조사한다. `pkgname`만 대조 기준으로 쓰지 않는다.
5. 이름과 무관하게 URL이 `aur.archlinux.org/<pkgbase>.git`인 remote를 AUR remote로 식별해 fetch와 push에 재사용한다.
6. AUR master를 fetch한다. clean하고 HEAD가 원격의 단순 ancestor일 때만 branch 이름과 무관하게 fast-forward하며, 그 밖에는 자동 정리 없이 상태를 보존한다.
7. sync로 PKGBUILD나 `.SRCINFO`가 바뀌면 이전 inventory와 버전 판정을 폐기하고 다시 읽는다.
8. 두 집합을 permission-only `local_missing`, local-only no-permission, PKGBUILD가 있는 `no_git`, 동일 base/upstream 변형으로 구분한다.
9. 승인 없이 빠진 대상을 clone하거나 `no_git` 폴더를 init하고 remote를 추가하지 않는다. 승인된 로컬 검증은 가능하며 push는 `no_repository`다.
10. 수집한 모든 로컬 PKGBUILD가 tally에 정확히 한 번 있는지 set difference로 검산한다.

## Step 2: upstream 최신판과 freshness

공식 최신판은 `AGENTS.md`의 현재 채널과 필터를 따르되 과거 사례, API 응답 순서와 `/releases/latest`를 판정 근거로 쓰지 않는다.
같은 upstream의 source, `-bin`, `-git`은 조회를 공유하되 각 PKGBUILD의 source, architecture, build와 runtime 계약은 따로 판정한다.
VCS package는 target branch/ref, 원격 HEAD와 실제 `pkgver()`를 비교한다. 근거와 `current|outdated|uncertain|untrackable`을 tally에 남긴다.

1. AUR sync 후 `check.py audit --json` 결과를 기준으로 공식 최신판을 최초 판정한다.
2. 실제 편집 직전에 `check.py audit --json --only <pkgbase>`로 같은 공식 채널을 재조회한다. 새 release/ref면 기존 계획과 checksum을 폐기하고 영향 감사부터 반복한다.
3. commit/push 직전에 같은 `--only <pkgbase>` 감사를 마지막으로 실행한다. 새 release/ref면 오래된 변경을 push하지 않고 영향 감사부터 반복한다.

checksum 성공만으로 payload 버전을 증명하지 않는다. archive root, package metadata, source manifest나 binary `--version`으로 최종 `pkgver`와 일치함을 확인한다.
감사 전용 요청이면 Step 3까지 수행하고 `AGENTS.md`의 그룹별 변경 전 보고 형식으로 보고한 뒤 멈춘다.

## Step 3: 변경 영향과 dependency 감사

outdated마다 release notes/changelog, CLI·설정·API·기본 동작, 언어·runtime·toolchain 최소판, 지원 OS와 architecture의 변화를 확인한다.
source URL·asset·압축 형식·archive root, build system과 build/package/check, 생성 파일·vendored/native module·arch asset, patch/workaround의 유효성을 확인한다.
`depends`, `makedepends`, `checkdepends`, `optdepends`, 정적 binary의 CA bundle·데이터·외부 명령, ELF linkage·arch, service/timer/install/sysusers/tmpfiles를 확인한다.
license와 설치 경로 변화도 확인하며 기존 dependency 배열은 정규화해 중복을 찾는다. 버전업과 무관한 기존 중복은 고치지 않고 packaging issue로 보고한다.

dependency를 추가할 때 upstream 배포명만으로 Arch 이름을 추측하지 않는다.

1. upstream distribution과 실제 import/module/binary 이름
2. 필요한 runtime artifact와 ABI
3. Arch package의 설치 파일 또는 `provides`
4. 대상 OS와 architecture에서 실제 제공되는 기능

적합한 Arch package가 없으면 이름을 만들지 않고 `unmapped upstream dependency`와 영향받는 optional 기능을 보고한다.

## Step 4: 패키징 업데이트

실행 승인된 outdated만 수정한다. `pkgver` 변경 시 `pkgrel=1`, 같은 버전의 수정은 `pkgrel`만 올리며, immutable source의 URL·파일명을 먼저 바꾼 뒤 checksum을 계산한다.
architecture별 source는 모든 선언 arch의 asset 존재, SHA-256과 payload architecture를 직접 검증하고 upstream manifest와 대조한다.
이 규칙은 `updpkgsums`만으로 대체할 수 없다. 공통 source도 새 ref에서 다시 해시하며 byte-identical이면 근거를 tally에 남기고 값을 유지한다.
확인된 dependency만 반영하고 근거가 사라진 patch, workaround, stale 변수·파일을 제거한다.
build, package, check, completion, service/install을 새 계약에 맞추고 기존 스타일과 Arch 규칙을 지킨 뒤 최종 PKGBUILD에서 `.SRCINFO`를 다시 생성한다.

## Step 5: 패키지 검증

### 공통

- PKGBUILD 문법, source·checksum, patch·prepare와 `makepkg --printsrcinfo` 대 `.SRCINFO`의 byte-identical 일치
- 생성 package metadata와 dependency, archive의 예상 밖 파일·architecture 부재
- `references/arch-packaging-rules.md` 대조. 이번 변경이 새로 만든 위반은 고치고, 기존 위반은 버전업과 무관하면 packaging issue로 보고한다

### Source package

- 실제 build와 package 단계
- 가능한 upstream test 또는 package smoke test, 산출물 경로와 실행 가능성

### Binary package

- 모든 선언 architecture의 asset 존재, archive layout과 설치 파일
- ELF architecture, linkage와 동적 dependency
- packaged binary의 기본 실행 또는 version 출력

### 서비스와 통합 파일

- service/timer/install/sysusers/tmpfiles 설치 경로, unit 문법과 실행 계약
- shell completion과 보조 실행 파일

dependency, compiler, toolchain, Meson/CMake나 `PKG_CONFIG_PATH` 등 환경을 바꿨다면 새 checkout, cleanbuild, `setup --wipe` 등 clean state에서 재검증한다.
일부 dependency archive만 푼 partial root에 전역 `PKG_CONFIG_SYSROOT_DIR`를 설정하지 않는다.

검증 환경은 다음 순서로 선택한다.

1. Arch clean chroot/devtools
2. 필요한 공식/AUR dependency를 정상 설치한 disposable root
3. 완전한 sysroot
4. 불가능하면 source/checksum/metadata/prepare와 가능한 staged smoke만 수행하고 공백을 `partial` 또는 `blocked`로 기록

Python은 archive 안 `site-packages`를 `PYTHONPATH`에 넣고 entry point를 실행한다. Node와 native package도 실제 staged 경로로 smoke test해 host 설치본이나 harness 문제를 결함으로 오인하지 않는다.
`namcap`은 return code로 판정하지 않고 `actionable|known false positive|intentional tradeoff|pre-existing|unresolved`로 분류한다. 핵심 계약 실패나 unresolved runtime dependency가 있으면 push하지 않는다.

## Step 6: Git 동기화와 배포

수정·버전업 승인을 받아 검증이 완료된 package는 별도 commit/push 요청을 기다리지 않고 모두 배포한다. 사용자가 같은 요청에서 commit이나 push를 명시적으로 금지한 package만 제외한다.

1. 모노레포 루트에서 패키지 파일만 독립 커밋한다: `git add <pkg>/PKGBUILD <pkg>/.SRCINFO && git commit -m "..."`
2. commit 메시지에는 자동 attribution footer를 넣지 않는다. 여러 패키지를 한 커밋에 묶지 않는다.
3. 승인 범위의 package별 commit을 마치면 현재 모노레포 branch를 `origin`에 non-force push한다.
4. 각 AUR 원격 최신 상태를 fetch한다: `git fetch "ssh://aur@aur.archlinux.org/<pkgbase>.git" master`
5. 서브트리를 분리해 배포용 가상 커밋을 생성한다: `sha=$(git subtree split --prefix=<path>)`
6. 사전 검증: `git ls-tree "$sha"`로 루트 위치 확인, `git merge-base --is-ancestor FETCH_HEAD "$sha"`로 fast-forward 계승을 확인한다.
7. 검증된 커밋을 AUR 원격으로 직접 푸시한다: `git push "ssh://aur@aur.archlinux.org/<pkgbase>.git" "$sha:master"`
8. package별 실패를 독립 처리해 한 실패 때문에 다른 검증 완료 package를 누락하지 않는다.

## Step 7: 원격 반영 확인

배포마다 local HEAD, 모노레포 `origin` branch, AUR master, 원격 `.SRCINFO`와 aurweb 표시를 대조한다. 모두 일치하면 RPC 이전 값은 cache delay로 구분하며 일부만 일치하면 성공으로 단정하지 않는다.

## Step 8: 임시 데이터 및 빌드 잔여물 정리

실행 및 검증이 끝나면 임시 데이터와 패키지 폴더 내 빌드 잔여물을 예외 없이 전수 정리한다.

- 이번 실행이 만든 `/tmp` 격리 build tree, 캐시, package archive와 임시 로그를 즉시 제거한다.
- 패키지 폴더 내 이전 빌드 잔여물을 예외 없이 전수 삭제한다: `src/`, `pkg/`, 구버전 다운로드 바이너리 및 아카이브(`*.tar.*`, `*.zip`, `*.tgz`, `*.gz`), 구버전 `LICENSE-*` 파일.
- 빌드 중 클론된 내부 소스 트리 디렉터리와 IDE/빌드 보조 아티팩트(`.verify-*`, `.idea`, `*.iml` 등)도 함께 정리한다.
- 보존 대상: `PKGBUILD`, `.SRCINFO`, Git 추적 파일 및 현재 PKGBUILD가 직접 참조하는 로컬 패키징 소스 파일(`*.patch`, `*.install`, `*.service`, `*.conf` 등)만 엄격히 보존한다.

## 완료 검산

최종 보고 전에 tally로 다음 식을 계산하고 불일치하면 누락 행을 고친다.

- 전체 PKGBUILD = `current + outdated + uncertain + untrackable`
- 시작 시 outdated = `updated + intentionally_held + validation_blocked + validation_failed`
- updated = `pushed + permission_denied + no_repository + remote_conflict + not_requested`
- pushed ⇔ `local HEAD == AUR master`이고 remote `.SRCINFO`가 예상 version

`full|partial`은 validation 축이므로 disposition 합계에 더하지 않는다. package base와 PKGBUILD 수, 표 행 수를 혼용하지 않고 각각 대조한다.

## 결과 보고

`AGENTS.md` 형식은 감사 전용 변경 전 보고에만 쓴다. 이 형식은 실행 후 사후 보고이며 먼저 다음 집계를 그대로 출력한다.

```text
Inventory
- PKGBUILD:
- package bases:
- current:
- outdated:
- uncertain:
- untrackable:
Outdated disposition
- updated:
- intentionally_held:
- validation blocked:
- validation failed:
Updated push status
- pushed:
- permission denied:
- no repository:
- remote conflict:
- not requested:
```

각 outdated package는 다음 열을 그대로 사용한다.

```text
package | old → new | dependency/packaging impact | validation | disposition | commit | push | remote verification
```

추가로 current·uncertain·untrackable·`intentionally_held` 이름, 실행·미실행 검증, `namcap` unresolved, 원격 불일치·cache delay, 제거·보존 항목을 보고한다. 로컬 수정·validation·commit·push·AUR 반영을 합치거나 최신·제외 package를 빼지 않는다.
