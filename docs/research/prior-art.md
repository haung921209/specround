# 선행 사례 리서치 (H5) — "이미 있다" 반증 시도

조사일 2026-08-06. 프레임 = 반증: 우리는 만들려는 쪽으로 기울어 있으므로, 기존 도구가
G1~G9(SPEC.md §2) 조합을 이미 충족한다는 것을 증명하려 시도했다. 관대 해석 원칙 —
조합·설정으로 도달 가능하면 커버로 쳤다. 방법: 4개 축(git-native 분산 리뷰 / 리뷰
플랫폼·봇 / 산문 주석·앵커링 표준 / 에이전트-1급 신생 도구) 병렬 웹 조사, 활성도는
GitHub API·릴리스 페이지 실측. 심층 조사 약 25종 + 주변 스윕 15종.

표기: **O**=덮음 · **△**=부분(조합·설정으로 근사 가능 포함) · **X**=못 덮음.

## 0. 결론 먼저

**종합 판정 = (c) 부분.** 단일 도구도, 현실적 조합도 보장 전체를 못 덮는다 — 반증은
실패했고, 실패 지점이 곧 차별점이다:

- **차별점 = G1(산문 re-anchor) × G2(base 커밋 라운드) × G3(append-only 처분 원장)
  × G5(서버 0, git-only) × G6(렌더/raw 표면 수렴)의 동시 충족.** 이 다섯을 함께 덮는
  도구는 없고, 어느 조합으로 때워도 원장이 갈라져(사이드카 JSON / 인라인 마크 / 플랫폼
  서버) G3 의 "유실 0" 이 깨진다.
- 나머지 보장(G4·G7·G8·G9)은 전부 선례가 있다 — 발명이 아니라 **부품 조립**이다(§4.3).
- 개별 G 로 보면 모든 보장이 어딘가에는 존재한다. G1=Hypothesis/Gerrit ported comments,
  G2=Gerrit patchset, G3 절반=Reviewable disposition, G4=md-redline/markdown-review MCP,
  G5=git-appraise/git-bug, G8=CriticMarkup/GitHub suggestion. **조합이 신규성이다.**
- **리스크(정직 고지)**: 이 카테고리는 2025-06~2026-05 사이 신생 도구 9개가 밀집 생성될
  만큼 지금 급속히 채워지는 중이다(md-redline·Plannotator 7.5k★·spec-workflow-mcp 4.3k★).
  6~12개월 안에 상위 도구가 라운드·원장 축을 흡수할 개연성이 있다 — 만들 거면 빨리,
  그리고 원장 포맷(계약이 포맷에 있다는 우리 결정)을 차별화 축으로.

## 1. 판정 매트릭스

| 도구 | G1 앵커생존 | G2 라운드 | G3 처분원장 | G4 에이전트 | G5 git-only | G6 표면중립 | G7 CLI | G8 제안diff | G9 회수+처분 | 활성도 | 판정 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| **Gerrit** | O | O | △ | O | △(데이터만) | X | △ | O | O | 매우 활발 | 부분 부품 (최강 반증원) |
| **GitHub PR (+gh/prr)** | △ | △ | △ | O | X | X | O | O | △ | 상시 | 부분 대체 (현실적 최선 기성품) |
| **Reviewable** | O | O | O(의미론) | O | X | △ | △ | △ | △ | 매우 활발 | 부분 부품 |
| **md-redline** | △ | X | △ | O | O | △ | O | X | X | 신생·활발 | 근접 대체 |
| **Plannotator** | △ | △ | △ | O | X | △ | O | O | △ | 7.5k★·활발 | 부분 부품 |
| **spec-workflow-mcp** | △ | △ | △ | O | △ | X | △ | X | X | 4.3k★·유지보수 리스크 | 부분 부품 |
| **git-appraise** | X | △ | △ | O | O | X | O | X | X | 3년 휴면 | 부분 부품 (원장 선례) |
| **Radicle** | X | O | O/△ | O | △ | △ | O | △ | X | 매우 활발 | 부분 부품 |
| **git-bug** | X | X | △ | O | O | △ | O | X | X | 활발 | 부품 (원장 포맷) |
| **prr** | X/△ | △ | △ | △ | X | △ | O | △/O | △ | 저강도 활성 | 부품 (raw 표면 문법) |
| **reviewdog** | X | △ | X | △ | X | X | △ | △ | △ | 활발 | 부품 (교환 포맷) |
| **CriticMarkup (+Obsidian 계열)** | O | △ | △ | △ | O | X | X | O | △ | 스펙 휴면·생태계 생존 | 부분 부품 (문법) |
| **Hypothesis** | O(DOM) | X | X | △ | X | X | X | X | X | 활발 | 부품 (H4 정본) |
| **W3C Web Annotation** | O(스키마) | — | △(스키마) | O(스키마) | 호환 | — | — | — | — | REC 확정·안정 | 부품 (앵커 스키마) |
| **Commentary** | O | X | X | △ | △ | X | X | X | X | 신생·극초기 | 부품 (md 앵커 생존 실증) |
| **markdown-review** | △ | X | △ | O | △ | X~△ | X | X | X | 신생·극초기 | 부분 대체 |
| **md-annotator** | X~△ | △ | X | O | X | X | O | △ | △ | 신생·매우 활발 | 부분 대체 |
| **md-review-tool** | X | △ | X | △ | O | X | X | O | △ | 신생·극초기 | 부품 (라운드 기록 근사) |
| **md-review-plus** | △ | △ | △ | O | X | X | O | △ | △ | 신생·극초기 | 부품 (CLI 계약) |
| **graft** | O | X | X | X | O | X | X | X | X | 정체·5★ | 부품 (re-anchor+git 유일 결합) |
| **CodeRabbit/Greptile** | X | X | X | △ | X | X | △ | △ | △ | 매우 활발 | 무관 (참여자, 인프라 아님) |
| **spec-kit / OpenSpec** | X | X | X | △ | O | X | O | X | X | 125k★/64k★ | 무관 (리뷰 루프 부재) |

## 2. 도구별 상세

### 2.1 직접 경쟁 — 사람↔에이전트 markdown 리뷰 루프 (2025~2026 신생)

**md-redline** (dejuknow/md-redline · 30★ · 2026-03 생성, 2026-07-30 push)
markdown 전용 인라인 리뷰. 로컬 웹뷰에서 선택→코멘트, 코멘트가 `<!-- @comment{...} -->`
HTML 마커로 **md 파일 자체에** 저장(사이드카·DB 없음 → G5 O). MCP 도구 4종
(`mdr_request_review`/`mdr_review`/`mdr_ask`/`mdr_wait`)으로 에이전트가 리뷰를 요청하고
블로킹 질문까지 한다(G4 O). `mdr spec.md` 한 방(G7 O).
못 덮는 것: 라운드 없음(G2 X), 해소 시 마커가 삭제돼 이력이 문서에서 사라짐 — 원장이
git 커밋뿐(G3 △), 제안 diff 없음(G8/G9 X), 자동 re-anchor 없음(수동 드래그 핸들, G1 △).
판정: **근접 대체 — 이번 조사에서 가장 가까운 단일 도구.** "MCP 로 에이전트가 사람
리뷰를 요청하고 코멘트가 파일에 살아 git 으로 남는" 코어 루프는 이미 출시돼 있다.
그러나 처분 원장·라운드·제안 diff 세 축이 비어 있다.

**Plannotator** (backnotprop/plannotator · 7,542★ · 2025-12 생성, 2026-08-06 에도 push)
에이전트 plan·markdown·diff 를 브라우저에서 주석/승인하는 로컬 리뷰 표면. Claude Code
`ExitPlanMode` 훅으로 자동 개입, approve/deny+구조화 피드백 회신(G4 O), diff 인라인
suggestion·redline 마크업(G8 O).
못 덮는 것: **저장이 `~/.plannotator`(repo 밖) — G5 구조적 실패**(clone≠리뷰 이력),
라운드가 base 커밋 대비 아님, append-only 원장 없음.
판정: **부분 부품.** 이 카테고리의 수요가 7.5k★로 검증됐다는 증거이자, 신규 도구가
차별화 설명 부담을 지는 상대. 훅 개입 지점 설계는 차용 가치.

**spec-workflow-mcp** (Pimzino · 4,275★ · GPL-3.0 · README 에 "author taking a break")
스펙-드리븐 MCP 서버+웹 대시보드. 에이전트가 requirements/design/tasks 를 만들고 사람이
**텍스트 하이라이트 앵커 코멘트** + Approve/Request Changes/Reject 처분(3종 명시적).
데이터는 repo 안 `.spec-workflow/` 파일.
못 덮는 것: 대시보드 서버 프로세스 필요(G5 △), 승인 스냅숏 단위(개정 생존 계약 없음),
append-only 불명(approvals JSON 덮어쓰기로 추정), 리뷰어 편집 없음(G8/G9 X).
판정: **부분 부품.** "스펙 승인 라운드 + 앵커 코멘트 + repo 내 저장" 조합의 실존 증거.

**md-annotator** (konradmichalik · 2026-01 생성, 2026-08-04 push) — 렌더 뷰 주석→에이전트
반영→재오픈 라운드 루프가 워크플로에 내장(G4/G7 O). 저장이 로컬 서버 세션(repo 밖,
G5 X), 처분 원장 없음. 판정: 부분 대체 — specround UX 코어가 이미 활발히 개발 중이라는
증거.

**markdown-review** (jinqishen0725 · VS Code · 2★) — **사이드카 JSON 을 repo 에 커밋**하고
Copilot 도구 12종+MCP 로 에이전트가 사람과 같은 채널에서 회신·resolve(G4 를 문자
그대로 충족하는 실존 사례). 라운드·제안 diff·CLI 없음, append-only 아님(JSON RMW).
판정: 부분 대체.

**Commentary** (jaredhughes · VS Code · 2★) — 렌더 프리뷰에 코멘트, **3계층 앵커 폴백
(exact quote+전후 100자 문맥 → 문자 오프셋 → 최근접 헤딩+fuzzy)** 을 markdown 사이드카에서
이미 구현(G1 O — "어려운 부분이 어렵지 않다"는 증거). 처분·원장·라운드 전무, 에이전트로
단방향 송출뿐. 판정: 부품.

**md-review-tool** (LetitiaChan · VS Code · 4★) — 파일 변경 감지 시 `.review/` 에 리뷰
버전(v1/v2)을 자동 보존 — **전 조사 대상 중 유일하게 라운드가 기록에 남는 도구**(단
base=커밋이 아니라 파일 변경). 판정: 부품(G2 설계 레퍼런스).

**md-review-plus** (Seiraiyu · 2★) — 에이전트가 md 를 파이프하면 사람이 브라우저에서
처분(approve/reject/pending)·블로킹 질문·인라인 제안 편집 → **구조화 markdown 이 stdout,
exit code 0/1**. 저장이 세션(G5 X). 판정: 부품(블로킹 CLI 리뷰 계약).

### 2.2 코드 리뷰 인프라 — git-native·플랫폼

**Gerrit** (3.13.1 2025-11 · 매우 활발 · 4.0 로드맵 진행 중) — **최강 반증원.**
리뷰 메타데이터 전부(코멘트·투표·resolved)를 NoteDb = **코드와 같은 git repo 의 refs**
(`refs/changes/{id}/meta`, patchset SHA 키 NoteMap 의 JSON)에 저장 — 데이터 층은 100%
git(clone+refs fetch=전체 이력). **"ported comments"** 가 구 patchset 의 미해소 코멘트를
새 revision 의 올바른 위치로 이식한다 — specround 가 만들려는 re-anchor 그 자체(G1 O).
patchset=라운드(G2 O), "suggest fix"+`fix_suggestions` 구조화 필드+REST "Apply Stored
Fixes"(G8/G9 O), 봇=일반 계정+REST 전체(G4 O).
못 덮는 것: **운영에 Java 서버 데몬 필수**(개인 스펙 리뷰에 과대 — G5 의 "서버 없음"
불충족), 리뷰가 소스 diff 라인 전용 — 렌더 markdown 표면 없음(G6 X), 처분이 이진
resolve(타입 구분 없음).
판정: **부분 부품.** "보장의 심장부(G1 re-anchor·G2·G8/G9 구조화 제안+기계 apply)는
전부 Gerrit 이 이미 구현했고 저장까지 git 이다. 새로 발명할 것은 '서버 제거 + 산문
표면' 둘뿐" — 이것이 반증 측 최강 논거이고, 동시에 그 둘이 우리 G5·G6 다.

**GitHub PR + gh CLI (+prr)** — 현실적 최선의 기성품. markdown 스펙을 PR 로 리뷰하는
것은 업계 표준 관행: suggestion 블록(G8 O), gh/REST/GraphQL 에이전트 참여(G4 O — 전용
확장 `gh-pr-review` 까지), review 제출=라운드 근사(G2 △), 스레드 resolve(G3 △).
못 덮는 것(치명): **G5 — 정본이 남의 서버**(clone·오프라인·이관 불가, GHES 도 서버+DB).
**G6 — rich diff(렌더 뷰)에서 인라인 코멘트 불가**(커뮤니티가 브라우저 확장으로 때우는
중 — 수요 실존의 방증). **G1 — 산문 개정=rewrite 가 잦은데 정확히 rewrite 에서 앵커를
잃는다**(outdated 강등, force-push 시 문맥 복원 불가). suggestion apply 는 웹 UI 전용
(REST 없음 — G9 △, 에이전트가 body 파싱→직접 커밋으로 우회 가능). 코멘트 수정·삭제
가능=append-only 아님(G3).
판정: **부분 대체.** "팀이 이미 GitHub 에 산다면 G5 를 경성 요구로 유지할 근거를 먼저
증명하라"가 반증 측 논거 — 우리 답: G1(산문 rewrite)과 G6(렌더 표면)가 GitHub 에서
구조적으로 안 풀리고, 원장 이관 불가가 에이전트 루프(로컬 실행)와 마찰한다.

**Reviewable** (SaaS · 2026-08 까지 연속 업데이트) — 코멘트가 revision 을 넘어 생존
(G1 O), disposition 모델(Blocking/Discussing/Working/Satisfied/Informing + 해소 판정식
"blocker 0 + satisfied ≥1")이 우리 4처분보다 정교(G3 O 의미론), **2026-06 에이전트 전용
신원+CLI/MCP 출시**(G4 O). 못 덮는 것: 저장이 통째로 Firebase SaaS(G5 X), GitHub PR
(코드) 전제. 판정: 부분 부품 — 처분 어휘 체계와 해소 판정식을 차용.

**git-appraise** (Google · 5,304★ · 마지막 커밋 2023-08 — **3년 휴면**) — git notes 에
한 줄 JSON(`refs/notes/devtools/*`), `cat_sort_uniq` 병합으로 서버 없이 동기화(G5 O —
이 도구의 대표 보장). `git appraise show -json`(G4 O).
못 덮는 것: **location 이 commit 해시 고정 라인 범위 — re-anchor 전무**(정확히 G1 이
배제하는 diff 스냅숏 모델), 제안 diff 없음, 처분이 resolved bool, 유지보수 사망.
판정: **부분 부품(원장 선례).** "JSON 원장을 notes ref 에 두고 무충돌 병합"은 specround
저장층 그 자체이고 산문 .md 에도 그대로 동작한다 — H3 의 git notes 옵션이 실증돼 있다.

**Radicle** (heartwood · 2026-08-05 커밋 · 팀 유지보수) — Patch=Revision(라운드) 목록,
Revision 마다 base+델타(G2 O — 정확히 우리 라운드 모델), resolve/unresolve 연산 +
**`Revision.resolves`(새 리비전이 어떤 리뷰 코멘트를 닫는지 선언 — 라운드×처분 교차
링크)** + append-only CRDT(G3 O/△), `rad cob show` JSON plumbing(G4 O). 공식 예제가
산문(MENU.txt)을 리뷰한다.
못 덮는 것: CodeLocation 이 commit Oid 고정(G1 X), suggestion 1급 개념 없음(G8 △/G9 X),
COB 가 Radicle 네임스페이스에 살아 일반 `git clone` 으로 안 따라옴 — P2P 노드+DID 스택
통째 입양 필요(G5 △).
판정: 부분 부품 — `Revision.resolves` 패턴은 G2×G3 상호작용의 모범.

**git-bug** (9,963★ · 활발) — 리뷰 도구 아님(앵커·라운드 없음). 단 `doc/spec/dag-entity.md`
는 서명·Lamport 순서·무충돌 병합을 갖춘 **형식 명세 있는 범용 "git 내장 엔티티" 포맷**
— "review-comment" 엔티티를 정의하면 G3+G5 가 검증된 구현으로 공짜(Go 의존). 판정:
부품(H3 의 세 번째 옵션).

**prr** (danobi · 409★ · 저강도 활성) — GitHub PR 을 로컬 "리뷰 파일"로 내려 에디터에서
마크업(인라인/span/파일/PR 레벨 코멘트 + `@prr approve|reject` 디렉티브 + suggestion
fence) 후 제출. **공식 예제가 손자병법 산문 리뷰** — raw 문서 입력 표면(G6 의 절반)이
설계돼 있다. 정본이 GitHub(G5 X). 판정: 부품 — raw 표면 마크업 문법 차용.

**reviewdog** (v0.21.0 2025-09 · 활발) — linter 출력→리뷰 코멘트 게시 파이프. 루프가
아니라 단방향(저장·처분·수확 없음). 판정: 무관 — 단 **RDFormat(rdjson): 멀티라인 범위
+severity+suggestion diff 내장 구조화 리뷰 코멘트 교환 포맷**은 G4 출력 스키마로 차용
가치.

**CodeRabbit / Greptile** (AI PR 리뷰어 · 매우 활발) — 리뷰 인프라가 아니라 참여자.
자체 저장·앵커 모델 없음(호스트 플랫폼 상속). 판정: 무관 — 단 생태계 논거가 유효:
**채널이 표준적이면 기성 AI 리뷰어가 꽂힌다. specround 가 독자 채널을 만들면 이 생태계와
단절된다** → G4 채널 설계 시 완전 독자 포맷보다 기성 포맷(RDFormat·suggestion 펜스)
차용이 유리. CodeRabbit CLI 의 `--prompt-only`/`--plain` 이중 출력(같은 결과의 사람용/
에이전트용 두 표면)은 G4 설계 참고.

### 2.3 산문 주석·앵커링 — 표준·알고리즘 (부품 광맥)

**CriticMarkup** (스펙 2013 고정·toolkit 2021 휴면 — 생태계는 생존: MultiMarkdown-6
네이티브, PyMdown, pancritic, Obsidian 계열 2026 활발) — 산문 인라인 편집 추적 문법
5종: `{++추가++}` `{--삭제--}` `{~~old~>new~~}` `{>>코멘트<<}` `{==하이라이트==}`.
앵커=본문 내 물리 위치라 개정과 함께 이동(re-anchor 문제가 구조적으로 소거, G1 O),
문서=저장(G5 O), `{~~~>~~}` 가 곧 앵커에 붙는 제안 diff(G8 O — 13년 전에 풀린 문제).
못 덮는 것: 저자·타임스탬프·스레드·처분 필드가 스펙에 없음(Obsidian Commentator 가
자체 확장으로 얹음 — 선행 사례), 처분=마크 제거라 이력이 git diff 에만 남음(G3 △),
**코멘트가 문서를 오염** — 리뷰 중 문서가 clean 소스가 아니게 됨(우리 G6 결정의 "raw
인라인 주석→수확기가 흡수" 흐름과는 맞물림).
판정: **부분 부품(강력).** SPEC §3 이 이미 "인라인 주석(CriticMarkup 류)을 raw 에 치면
수확기가 흡수"로 참조하는 그 문법 — raw 표면 입력 문법으로 채택하되, 원장 필드는 별도
(인라인 확장 아님)가 맞다는 것이 Obsidian Commentator 의 베타 경고("텍스트 유실 위험
non-zero")가 주는 교훈.

**Hypothesis fuzzy anchoring** (활발 운영) — **H4(앵커 생존)의 정본.** 주석 생성 시
셀렉터 3종 동시 캡처(RangeSelector / TextPositionSelector / TextQuoteSelector=exact+
전후 32자 prefix/suffix), 재앵커링 4단 폴백:
① Range 직적용 → 산출 텍스트를 quote 의 exact 와 **대조 검증**(모든 전략의 결과를
quote 로 검증하는 것이 핵심 설계) ② TextPosition 직적용→같은 검증 ③ position 을 탐색
힌트로 prefix/suffix fuzzy 2상 매칭(수용 임계치) ④ 문서 전체 fuzzy 전문 검색.
엔진=수정판 diff-match-patch(매칭 Bitap, 비교 Myers). **전부 실패 시 orphan 으로 분류해
보존(삭제 아님)** — 우리 H4 의 "실패 시 처분(고아 코멘트)" 질문에 대한 선례 답.
성능 함정 실측: 짧고 흔한 quote+긴 문서에서 수 초~수십 초 블로킹(client#3919) — 후속작
anchor-quote(robertknight)가 13.3s→0.94s 개선(maxErrorRate 파라미터·정규화 내장, 단
2022 아카이브). 독립 모듈: dom-anchor-text-quote(2023 휴면).
플랫폼 자체는 G5 X(서버+Postgres+ES) · G3/G8/G9 X. 판정: **부품(H4 정본).** raw markdown
대상이면 DOM 셀렉터를 버리고 "position 힌트 + quote 검증 + bitap fuzzy + orphan 보존"
만 이식하면 된다.

**W3C Web Annotation Data Model** (REC 2017 확정·안정) — 앵커 셀렉터의 표준 스키마:
TextQuoteSelector(`exact`/`prefix`/`suffix`) · TextPositionSelector(`start`/`end`) ·
`refinedBy` 체이닝 · 한 타깃에 복수 셀렉터(정밀도 다른 대안들, 소비자가 택일 —
Hypothesis 3종 동시 캡처가 이 패턴). motivation 어휘(commenting/editing/questioning/
replying)는 있으나 **"반영됨/기각됨" 처분 상태 필드는 표준에 없다** — 우리가 확장할
지점이 명확. 참조 구현 Apache Annotator 는 아카이브(2024). 판정: **부품(원장 스키마의
앵커 부분).** 표준 채택 시 Hypothesis/Readium 계열 코드·경험이 호환.

**graft** (tkjaer · 5★ · 2026-02 이후 활동 없음) — exact→fuzzy prefix/suffix 재해석 +
orphan branch(`graft-comments`)에 JSON 저장. **"G1 자동 re-anchor + G5 git 저장" 결합의
유일한 발견 사례** — 단 웹앱+GitHub 로그인 필수+에이전트 통합 0. 판정: 부품(알고리즘
레퍼런스). 이 결합이 5★ 짜리 실험 하나뿐이라는 것이 공백 증명이기도 하다.

**Semiont write-time reconcile** (The-AI-Alliance) — LLM 이 exact+prefix/suffix 를 내면
쓰기 시점에 ①verbatim 검색 ②결정적 정규화(스마트쿼트·공백) ③Levenshtein 5% 허용 순으로
위치 확정, position/quote 쌍을 상호 정합(`content.substring(start,end)===exact` 불변식),
다중 매치는 조용히 고르지 않고 `first-of-many` 플래그. **에이전트가 주석을 생산하는
시대의 G4×G1 선행 사례** — fuzzy 는 쓰기 쪽에만, 읽기 쪽은 verbatim 회복만. 판정: 부품.

### 2.4 조사했으나 무관 판정

- **spec-kit**(GitHub · 125k★) / **OpenSpec**(64k★): 스펙 생성·정제 워크플로 거인.
  앵커 코멘트·처분·라운드 기록이 없다 — 리뷰는 채팅에서 일어나고 흔적은 스펙 diff 뿐.
  이 거인들의 워크플로에서 "리뷰 단계"가 빈칸이라는 것은 포지셔닝에 유리한 데이터이자
  흡수 리스크.
- **difit**(3k★·활발)/diffx/diffity: 로컬 diff 뷰+라인 코멘트→에이전트 프롬프트 복사.
  코멘트가 diff 스냅숏에 사는 — 정확히 우리가 부정하는 모델(G1 X 정의상 확정). specround
  의 G1 차별화를 오히려 확인해 줌.
- **beads**(Steve Yegge): 이슈 트래커지만 "git=DB, append-only JSONL, 머지 충돌 무해,
  에이전트 1급"을 대규모 실증 — G3/G5 구현 전략 레퍼런스로만.
- **git-dit**(준휴면)·**sit**(2018 사망)·**picosh/git-pr**(서버 전제)·**git-revue**(설계
  노트만, 미구현 — 이 틈새가 비어 있다는 방증): 계열 스윕 결과 git-appraise 이후 이
  계열 신규 활성 도구 없음. 살아있는 계보는 Radicle(COB) 하나.
- **HumanLayer/gotoHuman**(SaaS 승인 채널)·**vscode-code-review**(CSV·코드용)·
  **MD Review**(라인 앵커·AI 통합 없음)·**itssan14/md-review**(클립보드 최소형): 무관.
- **Editorially(2014 폐업)/Draft/Penflip**: markdown 협업 리뷰 SaaS 세대 — 전부 서버
  모델이었고 전부 소멸/휴면. "서버 모델의 수명" 반례(G5 방향 지지 데이터).

## 3. 부재 증명 — 전 계열 공백 4축

네 조사 축이 독립적으로 같은 공백에 수렴했다:

1. **G1×산문 자동 re-anchor + G5 git 저장의 결합** — 코드 리뷰 계열은 전부 앵커가
   commit 고정(git-appraise·Radicle·GitHub·prr), 앵커 생존을 가진 쪽(Hypothesis·
   Reviewable)은 전부 서버 저장. 결합 시도는 graft(5★ 실험) 하나.
2. **G3+G5: append-only 처분 원장이 repo 안** — 처분 의미론이 가장 정교한 Reviewable 은
   Firebase, repo 안에 원장을 두는 쪽(md-redline·CriticMarkup)은 처분 시 마커를 지워
   이력이 소멸. append-only 원장 선례(git-appraise notes·beads JSONL)는 산문 리뷰가 아님.
3. **G6: 렌더/raw 두 표면 → 한 원장 수렴** — 전 도구 X. GitHub 은 렌더 뷰 코멘트 자체가
   불가(커뮤니티 브라우저 확장으로 보완 중). 유일한 부분 해는 CriticMarkup 인라인(문서=
   원장이라 표면 무관)이나 처분·스레드가 없다.
4. **G2: base 커밋 기준 라운드 기록** — Gerrit patchset·Radicle Revision 이 코드 쪽 선례.
   markdown 리뷰 신생 도구 중에는 md-review-tool 의 파일 변경 스냅숏이 최선(커밋 기준
   아님).

## 4. 종합 판정

### 4.1 판정: (c) 부분 — 만들 근거 성립, 단 조립식으로

- **(a) "이미 있다" 는 기각.** 가장 근접한 단일 도구 md-redline 도 처분 원장·라운드·
  제안 diff 가 없고, 가장 근접한 조합(GitHub PR+prr+gh)도 G5(정본이 남의 서버)·G6(렌더
  표면)·G1(rewrite 시 anchor 사망)에서 구조적으로 깨진다. Gerrit 은 기능 축을 거의 다
  갖췄지만 서버 데몬+코드 diff 표면이라는 정체성이 우리 용도와 반대다.
- **(b) 차별점의 정확한 보장 조합 = G1(산문 re-anchor) × G2(라운드) × G3(append-only
  처분 원장) × G5(서버 0) × G6(표면 중립).** 이 중 어느 하나를 빼면 기존물이 있다:
  G5 를 빼면 Gerrit/Reviewable, G1·G6 을 빼면 GitHub PR, G2·G3 을 빼면 md-redline.
  다섯이 함께 있어야 신규다. G4·G7·G8·G9 는 차별점이 아니라 **시장 입장권**이다(신생
  도구들이 이미 다 하고 있음).
- **개별 기술 난제는 없다.** H4(앵커 생존)는 12년 전에 공개 알고리즘+구현으로 풀렸고
  (Hypothesis), 원장은 선례 스키마가 셋이나 있다(§4.3). 신규성은 조합이고, 경쟁 리스크는
  시간이다(§0 리스크).

### 4.2 열린 항목(H3~H9)에 주는 답

- **H3 (원장 저장소: jsonl vs git notes)**: 선례 셋 — git-appraise(notes+한 줄 JSON+
  cat_sort_uniq), git-bug(dag-entity: 서명·Lamport·형식 명세, Go 의존), beads(평파일
  JSONL in repo). notes 는 "clone 에 안 따라오는 ref" 문제(fetch 설정 필요)가 있고,
  평파일 JSONL 은 도구 없이도 읽힌다 — G4(에이전트가 그냥 cat 해서 읽음)와 G5 계약
  단순성 기준으로는 JSONL 이 유리하다는 근거가 이번 조사에서 보강됨(결정은 스펙 몫).
- **H4 (앵커 생존 알고리즘)**: 조립 처방이 명확해짐 —
  앵커 스키마 = W3C TextQuoteSelector(exact+prefix/suffix 32자)+TextPositionSelector 쌍,
  쓰기 시 상호 정합 불변식(`substring(start,end)===exact`, Semiont).
  재앵커 = position 힌트 → verbatim → 정규화 → bitap fuzzy(모든 단계 quote 검증) 순
  4단 폴백(Hypothesis), 실패 시 **orphan 을 삭제가 아니라 처분 대기 상태로 원장에 보존**.
  성능: 짧은 quote+긴 문서의 fuzzy 블로킹 실측 있음 — anchor-quote 식 개선(오차율
  파라미터·배치) 참고.
- **H8 (제안 diff 의 stale)**: Gerrit 3.11+ 가 "구 patchset 의 제안을 최신에 적용(patch
  변환)" 을 이미 한다 — 라운드 잠금이 아니라 re-anchor 계열로 푼 선례.
- **H9 (기존 diff 코멘트 UI 흡수)**: reviewdog RDFormat 이 "외부 생산자→우리 원장"
  변환기의 입력 포맷 후보.

### 4.3 가져다 쓸 부품 목록

| 부품 | 출처 | 쓸 곳 |
|---|---|---|
| 앵커 셀렉터 스키마 (exact+prefix/suffix+position, refinedBy) | W3C Web Annotation | 원장의 anchor 필드 |
| 재앵커 4단 폴백 + quote 검증 + orphan 보존 | Hypothesis fuzzy anchoring | H4 |
| 쓰기 시점 정합 불변식·`first-of-many` | Semiont | 에이전트가 코멘트 만들 때 |
| bitap fuzzy 매칭 (성능 개선판) | diff-match-patch / anchor-quote | H4 엔진 |
| git 원장: notes+JSON+cat_sort_uniq / dag-entity / JSONL | git-appraise / git-bug / beads | H3 |
| 원장 append 체인=이력 (meta ref 커밋 체인) | Gerrit NoteDb | H3 |
| 처분 어휘 + 해소 판정식 ("blocker 0 + satisfied ≥1") | Reviewable | G3 상태 모델 |
| 라운드가 처분을 흡수하는 교차 링크 (`Revision.resolves`) | Radicle | G2×G3 |
| 제안 diff 인라인 문법 `{~~old~>new~~}` | CriticMarkup | G8 raw 표면 |
| suggestion 을 코멘트의 구조화 필드로 (`fix_suggestions`) + 기계 apply | Gerrit | G9 |
| ```suggestion 펜스 (사람·LLM 이 이미 학습한 사실상 표준) | GitHub | G8/G9 와이어 포맷 |
| apply 커밋에 제안자 co-author (처분의 git 원장화) | GitHub 관행 | G9 |
| raw 리뷰 파일 마크업 (quote+interleave+span+디렉티브) | prr | G6 raw 표면 |
| 구조화 리뷰 코멘트 교환 포맷 (rdjson) | reviewdog RDFormat | G4 출력·H9 입력 |
| MCP 도구 표면 (request_review/ask/wait) | md-redline | G4 채널 |
| 훅 개입 지점 (ExitPlanMode 등) | Plannotator | 에이전트 통합 |
| 블로킹 CLI 계약 (stdout 구조화+exit code) | md-review-plus | G7×G4 |
| 사람용/에이전트용 이중 출력 (`--plain`/`--prompt-only`) | CodeRabbit CLI | G4 |

## 5. 주요 출처

각 판정에 인라인 병기. 핵심: Gerrit NoteDb·ported comments·suggest edits 공식 문서 /
Reviewable docs+CHANGELOG(2026-06 에이전트 지원) / reviewdog repo / GitHub suggestion·
커뮤니티 논의(#23138·#142466·#186730) / google/git-appraise(스키마 원문) / git-bug
dag-entity 명세 / radicle heartwood 소스(patch.rs·common.rs) / danobi/prr book /
Hypothesis "Fuzzy Anchoring" 블로그·client#3919 / W3C annotation-model / CriticMarkup
toolkit / dejuknow/md-redline / backnotprop/plannotator / Pimzino/spec-workflow-mcp /
konradmichalik/md-annotator / jinqishen0725/markdown-review / jaredhughes/commentary /
LetitiaChan/md-review-tool / Seiraiyu/md-review-plus / tkjaer/graft / Semiont
W3C-SELECTORS / Fevol/obsidian-criticmarkup / philphilphil/obsidian-track-changes /
github/spec-kit / Fission-AI/OpenSpec / steveyegge beads. 활성도 수치는 2026-08-06
GitHub API 조회 기준.
