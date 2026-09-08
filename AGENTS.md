# AUR 패키지 유지보수 워크스페이스

이 디렉터리는 사용자가 관리하는 AUR(Arch User Repository) 패키지들의 모음이다.
각 하위 폴더는 독립된 AUR 패키지로 `PKGBUILD`(필요시 `.SRCINFO`, `.install`,
패치 파일 등)를 가진다. 일부 폴더는 같은 upstream을 공유하는 `-bin` / 소스 빌드 /
`-git` 변형이다.

---

## 1. 유지보수 정기 작업: "의존성 최신화"

사용자가 "의존성 최신화", "버전 업데이트", "upstream 확인" 등을 요청하면 아래
프로토콜을 따른다. 이 프로토콜은 이 프로젝트에 한해 항상 적용한다.

### 1-1. 대상 식별

- `find . -maxdepth 3 -type f -name PKGBUILD -not -path '*/src/*'`로 모든 패키지를 스캔한다. 2단계 package container 아래의 maintained repository도 포함하되, `makepkg`가 만든 `*/src/*` 내부의 vendored/build-artifact PKGBUILD는 제외한다.
- 각 PKGBUILD에서 `pkgname`, `pkgver`, `pkgrel`, `url`, `source`(특히 `_pkgver` /
  `_pkgver_upstream` 등 보조 변수)를 추출한다.
- **같은 upstream을 공유하는 변형은 그룹화**한다(예: `chainsaw` /
  `chainsaw-bin`, `linear-cli-*` 계열, `opencode-*` 계열, `posthog-cli` /
  `posthog-cli-bin`, `python-tree-sitter-c` / `tree-sitter-c` 등). upstream 확인은
  그룹당 한 번만 한다.

### 1-2. Upstream 최신 버전 확인

가능하면 아래 우선순위로 자동화 가능한 채널을 사용한다.

1. GitHub releases / tags
   - **올바른 방법**: `gh api 'repos/<owner>/<repo>/releases?per_page=30' --jq '[.[] | select(.draft==false and .prerelease==false)] | sort_by(.published_at) | reverse | .[0]'`
     - `released?prerelease=false&draft=false` 필터 + `published_at` 내림차순 정렬을 항상 사용한다.
     - `/releases/latest`는 maintainer가 `make_latest`를 설정하지 않은 저장소에서 최신을 잘못 반환할 수 있다.
   - **`/tags` 응답을 절대 정렬 기준으로 사용하지 말 것**: GitHub의 `/tags`는 ref 이름순/생성순으로 반환되어 abandoned 태그(release 없는 태그)가 맨 앞에 올 수 있다. 예: `safishamsi/graphify`의 `v1.0.0`은 release 없는 abandoned tag이고 실제 최신은 `v0.7.10`이다.
   - release가 아예 없는 저장소에서만 최후 수단으로 `/tags`를 사용하되, 사용자에게 "release 없음(tag만 존재)"임을 명시한다.
   - 모노레포 컴포넌트 태그(예: `posthog-cli/v0.7.11`, `posthog-cli-v0.7.2`, `meridian-v1.42.1`)는 패턴이 다양하므로 `releases` 응답에서 `tag_name`을 정규식으로 필터링한다.
2. PyPI (Python 패키지)
   - `curl -s https://pypi.org/pypi/<name>/json | jq -r .info.version`
3. npm (JS CLI)
   - `npm view <name> version` (또는 `--json`).
4. crates.io / rubygems / hex.pm 등 패키지 매니저별 표준 API.
5. Codeberg, GitLab 등은 각 플랫폼의 API/`tags` 엔드포인트.
6. `-git` 패키지(`pkgver`가 `r<n>.<sha>` 형태)는 정해진 브랜치의 HEAD 커밋이
   바뀌었는지 + 의미 있는 변경이 있는지로 판단한다. 단순히 commit count가 1~2개
   증가했고 패키징에 영향이 없으면 업데이트 우선순위 낮음.

### 1-3. 변경 영향 분석 (각 out-of-date 패키지마다)

다음 항목을 수집한다.

- 현재 `pkgver` → 최신 upstream 버전.
- **CHANGELOG / Release Notes 요약**: 사용자에게 보일 때는 한국어로 한두 줄.
- **Breaking changes**: API/CLI 옵션 변경, 의존성 호환성 변경, 빌드 시스템 변경
  (예: meson→cmake, hatch→setuptools 등), Python/Node/Go/Rust 최소 버전 상향.
- **패키징에 대응해야 할 변경**:
  - `source` URL/파일 구조 변경 (tarball 레이아웃, GitHub asset 이름)
  - `sha256sums` 재생성 필요
  - 새 의존성 추가/제거(`depends`, `makedepends`, `optdepends`,
    `checkdepends`)
  - 빌드 명령 변경 (`build()` / `package()`)
  - 패치 파일이 더 이상 적용되지 않거나 불필요해진 경우(즉, **현재 패키징
    코드에서 제거할 항목**)
  - Python 패키지 메타(`pyproject.toml` 변경에 따른 `provides`/`replaces` 갱신)
- **AUR 측 노트**: 동일 패키지의 공식 / 다른 메인테이너 PKGBUILD가 있으면 참고
  (`paru -Si`, `aurweb` JSON RPC 등). 단순 참고용이며 강제는 아님.

### 1-4. 산출물 형식

사용자에게 보고할 때는 패키지 그룹 단위로 다음 형식을 따른다.

```
### <group-name>  (현재 X.Y.Z → 최신 A.B.C)
- 영향 받는 폴더: <dir1>, <dir2>
- 주요 변경: <한국어 요약>
- Breaking change: <있음/없음 + 내용>
- 패키징 액션:
  - [ ] sha256sums 재생성
  - [ ] depends: <추가/삭제>
  - [ ] 패치 <name.patch> 제거 (upstream merge됨)
  - [ ] build() 단계 변경: <설명>
- 제거 대상: <PKGBUILD 안에서 더 이상 필요 없는 코드/파일>
```

먼저 **목록만** 보여주고, 사용자가 구체적인 패키지의 실제 변경을 지시할 때만
PKGBUILD를 수정한다.

### 1-5. 변경 적용 시 규칙

- 수정 시 항상 `pkgver` 변경 → `pkgrel=1`로 리셋. 같은 `pkgver`에서 재빌드 사유면
  `pkgrel`만 증가.
- `updpkgsums`(가능하면) 또는 수동으로 `sha256sums` 재계산.
- `.SRCINFO`가 있으면 `makepkg --printsrcinfo > .SRCINFO`로 갱신.
- 가능하면 `makepkg --syncdeps --noconfirm` 또는 최소 `makepkg -od`로 빌드/추출
  단계까지 검증한다. 검증 결과를 보고에 포함한다.
- `-bin` 패키지는 빌드를 건너뛰고 다운로드 + `package()` 동작을 검증한다.

### 1-6. 안전/스타일

- 패치/소스 URL은 가급적 GitHub release tarball 또는 archive를 사용. raw
  master/main URL은 안정성이 낮다.
- AUR 호환성: PKGBUILD는 `bash` 호환, 외부 명령은 `coreutils`/`base-devel` 범위.
- 한국어 주석/메시지를 PKGBUILD에 새로 추가하지 않는다(기존 영문 스타일 유지).
- 사용자와의 대화는 한국어. agent 간 메시지는 영어 권장.

---

## 2. 자주 쓰는 명령

```bash
# 모든 PKGBUILD에서 현재 버전 추출
for f in $(find . -maxdepth 3 -type f -name PKGBUILD -not -path '*/src/*' | sort); do
  d=$(dirname "$f" | sed 's|^\./||')
  v=$(grep -E '^pkgver=' "$f" | head -1 | cut -d= -f2- | tr -d "'\"")
  r=$(grep -E '^pkgrel=' "$f" | head -1 | cut -d= -f2- | tr -d "'\"")
  printf "%-45s ver=%-22s rel=%s\n" "$d" "$v" "$r"
done

# GitHub 최신 stable release (1-2의 규칙: draft/prerelease 제외 + published_at 내림차순)
gh api 'repos/<owner>/<repo>/releases?per_page=30' --jq '[.[] | select(.draft==false and .prerelease==false)] | sort_by(.published_at) | reverse | .[0] | .tag_name + "\t" + .published_at'

# 특정 release의 body 요약
gh release view <tag> --repo <owner>/<repo> --json body -q '.body'
```

## 3. 그룹/카테고리 (참고)

- **단순 binary 미러**: `*-bin` (adguardhome-bin, chainsaw-bin, agent-browser-bin,
  posthog-cli-bin, stripe-cli-bin 등) – upstream release asset 다운로드.
- **Go/Rust/Node 소스 빌드**: chainsaw, axiom, langfuse-cli, posthog-cli,
  meridian, promtool 등.
- **Python 패키지**: python-* (대부분 `pyproject.toml` 기반).
- **tree-sitter 그래머**: python-tree-sitter-* / tree-sitter-* – 거의 동일한
  패턴, generate 스크립트(`scripts/generate_tree_sitters.py`,
  `fix_tree_sitters.py`)가 있다. 일괄 처리 가능.
- **VCS (`-git`)**: alias-tips-git, fluent-decoration-git, opencode-git,
  python-clikit-git, zsh-history-search-multi-word-git 등.
- **기타**: ttf-segoe-ui(폰트), pi-hole-whitelist(데이터), pinentry-kwallet,
  unegg, kwin-scripts-dynamic-workspaces, gnome-shell-extension-* 등.
