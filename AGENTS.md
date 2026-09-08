# AUR 패키지 유지보수 모노레포

이 디렉터리는 사용자가 관리하는 AUR(Arch User Repository) 패키지들의 **통합 Git 모노레포(Monorepo)**다.
모든 패키지와 관리 스킬·설정이 단일 Git 저장소로 통합 관리되며, 어떤 머신에서든 단 한 번의 `git clone`으로 동일한 에이전트 작업 환경이 복원된다.

---

## 1. 아키텍처: Git Subtree 모노레포

루트의 단일 `.git`이 전체 워크스페이스의 히스토리와 설정을 중앙 관리하며, 하위 패키지 디렉터리에는 별도의 `.git`이 존재하지 않는다.

```text
aur/ (루트 .git 1개로 전체 관리, GitHub 원격 미러)
├── AGENTS.md                                (프로젝트 규범 및 아키텍처 정의)
├── .agents/skills/                          (에이전트 유지보수 스킬 및 패키징 규칙)
├── scripts/                                 (도구 및 템플릿 생성기)
├── senpi/                                   (일반 폴더로 추적, 개별 .git 없음)
│   ├── PKGBUILD
│   └── .SRCINFO
├── gajae-code-bin/
└── openvpn3/                                (중첩 컨테이너 디렉터리)
    ├── openvpn3/
    └── openvpn3-git/
```

- **이식성(Portability)**: 새 머신이나 CI 환경에서 `git clone <repo-url>` 한 번으로 모든 패키지 소스, 과거 히스토리, 에이전트 스킬, 룰셋이 즉시 복제된다.
- **히스토리 보존**: 63개 모든 패키지의 과거 AUR 커밋 히스토리가 서브트리로 보존되어 있으며, 커밋 SHA가 일치한다.

---

## 2. 작업 및 커밋 규칙 (Commit Hygiene)

1. **패키지 단위 원자적 커밋**:
   - 패키지를 수정할 때는 반드시 해당 패키지의 파일만 스테이징하여 독립 커밋을 만든다.
   - 예: `git add senpi/PKGBUILD senpi/.SRCINFO && git commit -m "Update senpi to 2026.9.7_2"`
   - **여러 패키지의 수정을 하나의 커밋에 묶지 않는다.** 패키지별로 커밋이 분리되어 있어야 `git subtree split` 시 각 패키지의 독립 히스토리가 순수하게 유지된다.
2. **커밋 메시지 규칙**:
   - 평문 본문만 사용하며, 자동 attribution/co-author footer는 넣지 않는다.

---

## 3. AUR 배포 프로토콜: `split & push`

루트에 수십 개의 리모트를 등록하지 않고, **AUR 직접 URL(Direct URL)**을 사용하는 2단계 `split & push` 방식을 적용한다.

```text
aur_url="ssh://aur@aur.archlinux.org/<pkgbase>.git"
```

### 배포 절차

```bash
# 1. 원격 최신 상태 fetch 및 계승 확인
git fetch "$aur_url" master

# 2. 해당 패키지 서브트리 분리 (루트가 PKGBUILD인 가상 커밋 생성)
split_sha=$(git subtree split --prefix=<path>)

# 3. 배포 전 로컬 검증 게이트 (안전성 검사)
git ls-tree "$split_sha"  # PKGBUILD와 .SRCINFO가 루트(/)에 존재하는지 검사
git merge-base --is-ancestor FETCH_HEAD "$split_sha"  # fast-forward 계승 여부 검사

# 4. AUR 원격으로 직접 푸시
git push "$aur_url" "$split_sha:master"
```

- 중첩 경로(예: `openvpn3/openvpn3`, `openvpn3/openvpn3-git`)는 `--prefix=openvpn3/openvpn3`와 같이 상대 경로 전체를 지정한다.

---

## 4. 관리 시 항상 고려해야 할 핵심 주의사항 (Gotchas & Invariants)

1. **Fast-forward 강제 (No Force Push)**:
   - AUR 서버는 non-fast-forward 푸시(`--force`)를 원천 거부한다.
   - 따라서 split된 커밋은 반드시 원격 AUR `master`의 직계 자손이어야 한다.
2. **빌드 잔여물 정리 (예외 없음)**:
   - 패키지 폴더 내 빌드 산출물(`src/`, `pkg/`, `*.pkg.tar.*`, 다운로드 바이너리 및 아카이브 등)은 절대 모노레포에 커밋하지 않는다.
   - 검증 후 예외 없이 전수 삭제하여 작업 트리를 항상 100% clean 상태로 유지한다.
3. **외부 원격 변경 동기화 (Remote Ahead)**:
   - AUR 원격에 외부 변경(CoMaintainer 수정 등)이 있을 경우, 원격을 임시 fetch하여 변경분을 검토한 뒤 모노레포 작업 트리에 필드별로 반영 후 커밋한다.
4. **신규 패키지 추가 시**:
   - 신규 패키지 폴더를 생성하고 `PKGBUILD`, `.SRCINFO`를 작성한 뒤 모노레포 루트에서 일반 커밋하면, 추후 `git subtree split`이 자동으로 루트 트리로 분리하여 AUR에 첫 푸시할 수 있다.

---

## 5. 유지보수 정기 작업: "의존성 최신화"

사용자가 "의존성 최신화", "버전 업데이트", "upstream 확인" 등을 요청하면 아래 프로토콜을 따른다.

### 5-1. 대상 식별
- `find . -maxdepth 3 -type f -name PKGBUILD -not -path '*/src/*'`로 모든 패키지를 스캔한다.
- 같은 upstream을 공유하는 변형은 그룹화한다.

### 5-2. Upstream 최신 버전 확인
1. GitHub releases / tags:
   - `gh api 'repos/<owner>/<repo>/releases?per_page=30' --jq '[.[] | select(.draft==false and .prerelease==false)] | sort_by(.published_at) | reverse | .[0]'`
   - `/releases/latest`는 비권장 (make_latest 미설정 이슈).
2. PyPI: `https://pypi.org/pypi/<name>/json`
3. npm: `npm view <name> version`
4. crates.io, Codeberg, VCS HEAD 등 플랫폼별 표준 채널.

### 5-3. 변경 영향 분석 및 사전 보고
- 감사 전용 요청 시에는 `AGENTS.md` 5-4 형식으로 그룹별 사전 계획 목록만 보고하고 멈춘다.
- 실행 지시가 포함된 경우 `SKILL.md` 절차에 따라 패키징 수정, 격리 빌드 검증, split & push, 사후 보고를 완수한다.

### 5-4. 사전 보고 형식 (감사 전용)
```
### <group-name>  (현재 X.Y.Z → 최신 A.B.C)
- 영향 받는 폴더: <dir1>, <dir2>
- 주요 변경: <한국어 요약>
- Breaking change: <있음/없음 + 내용>
- 패키징 액션:
  - [ ] sha256sums 재생성
  - [ ] depends: <추가/삭제>
- 제거 대상: <PKGBUILD 안에서 더 이상 필요 없는 코드/파일>
```
