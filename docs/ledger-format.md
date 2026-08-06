# 원장 포맷 — `specround.ledger/v0`

> 이 포맷이 계약이다. 코어·CLI·웹뷰는 갈아탈 수 있는 구현이고, 남는 것은 이 파일
> 스키마다(SPEC.md G5). 그래서 포맷 문서가 구현 문서보다 상위에 있다.

계약의 최소 문장: **한 줄 = 한 이벤트 = 하나의 JSON 객체.** 이 줄들을 쓸 수 있으면
어떤 언어·에디터·에이전트든 참여자이고, 읽는 도구가 없어도 `cat` 이 유효한 리더다.

## 1. 어디에 사는가

원장·스냅숏·`origin` 은 **스토어 디렉토리 하나**에 같이 산다. 기본 자리는 문서 곁이
아니라 **홈 중앙**이다.

```
$XDG_DATA_HOME/specround/docs/5a/cdd8cb…/
  origin                   ← 이 스토어가 무엇을 위해 만들어졌는지 (평문 한 줄)
  ledger.jsonl             ← 이벤트 로그 (append-only)
  objects/35/c081dd…       ← 문서 스냅숏 (내용주소, sha256)
```

- **키 = 문서 절대경로(resolve 후)의 sha256.** 상대표기도 심볼릭 링크도 같은 스토어로
  모인다 — 한 문서, 한 이력. 키가 경로라서 문서를 **고쳐도** 이력은 그 자리에 있다.
- **데이터 홈** = `$XDG_DATA_HOME`(절대경로일 때만) · 없으면 `~/.local/share`. 스토어는
  데이터다 — 캐시는 축출될 수 있고 설정은 손편집하는 것이라 둘 다 아니다. 플랫폼별 규약
  (macOS `~/Library/Application Support`) 대신 한 규칙을 쓴다: 사람이 외워서 칠 수 있는
  경로여야 "`cat` 이 유효한 리더" 가 성립한다.
- **문서 곁 `.specround/` 는 기본이 아니다.** git 트리 안에서 untracked 노이즈가 되고
  gitignore 숙제를 만들어서, 하필 그 자리에서 "git 없이 돈다"(G5·G10)가 거짓이 된다.
- **git 호출 0.** 원장·스냅숏은 평문 파일이다. 문서가 untracked 든 repo 밖이든 전 기능이
  돈다. 원장을 커밋하는 것은 공유·내구의 선택층일 뿐이고 기록의 전제가 아니다(G5·G10).

### 1.1 스토어를 repo 안으로 (옵트인)

팀이 이력을 공유하려면 되돌린다. 문서 디렉토리에서 위로 올라가며 찾은 **가장 가까운**
`.specround.json` 이 정한다(없으면 기본).

```json
{"store": {"mode": "beside"}}
{"store": {"mode": "path", "path": "review"}}
{"store": {"mode": "central"}}
```

| mode | 스토어 위치 | `doc` 키의 기준(base) |
|---|---|---|
| `central` | 중앙 (기본과 같음) | 그 문서의 폴더 |
| `beside` | `<문서 폴더>/.specround` | 문서 폴더 |
| `path` | `<설정파일 폴더>/<path>` (절대경로면 그대로) | 설정파일 폴더 |

`path` 가 repo 공유의 실제 답이다 — 여러 폴더의 문서가 원장 하나를 쓰고, 키가 설정파일
기준이라 clone 해도 같은 파일이 같은 뜻으로 읽힌다.

설정은 TOML 이 아니라 JSON 이다: `tomllib` 은 3.11 부터인데 이 패키지는 3.10 을 무의존으로
지원해서, TOML 은 의존성이나 "지원하는 인터프리터에서 사라지는 기능" 중 하나를 비용으로
문다. **모르는 키는 거부한다** — 원장과 같은 이유로, 조용히 무시되는 설정은 켜져 있다고
믿는 설정이다.

### 1.2 해석 우선순위 — 인자 > 설정 > 기본

| 계층 | 정하는 것 | base |
|---|---|---|
| 인자 | 호출자가 준 스토어 경로 | 같이 준 base, 없으면 **스토어의 부모** |
| 설정 | 가장 가까운 `.specround.json` | §1.1 표 |
| 기본 | 중앙 스토어 | 그 문서의 폴더 |

`<폴더>/.specround` 는 인자 규칙의 특수한 경우다(부모가 base) — 규칙이 둘로 갈리지 않게
하려고 이렇게 뒀다.

### 1.3 `doc` 키와 `origin`

- 이벤트의 `doc` 는 **스토어 base 기준 상대 POSIX 경로**다. 상대라서 in-tree 스토어는
  폴더를 옮기거나 clone 해도 원장이 그대로 유효하다.
- `origin` 은 스토어가 **무엇을 위해 만들어졌는지**를 평문 한 줄로 적는다. 중앙 스토어의
  이름은 다이제스트이고 해시는 편도라서, 이 줄이 없으면 이력 디렉토리가 자기 주인을 못
  말한다.

  ```json
  {"kind":"document","path":"/home/me/docs/spec.md","schema":"specround.origin/v0"}
  ```

  `kind` 는 `document`(중앙 — 한 문서) 또는 `directory`(in-tree — 그 폴더의 문서 전부).
  **한 번 쓰고 다시 쓰지 않는다** — 원 경로가 나중의 재결속(H10)이 출발할 유일한 지점이다.
  `origin` 이 없는 스토어는 이 파일이 생기기 전에 쓰인 것으로 보고 부모 폴더를 담당한다고
  읽는다.
- 문서를 옮기거나 개명하면 키가 갈려 **새 스토어**가 생긴다(H10, 미구현). 옛 이력은
  사라지지 않고 옛 스토어에 그대로 있으며 `origin` 이 원 경로를 계속 말한다.

## 2. 스키마 버전과 호환 규칙

모든 줄은 `schema` 필드를 갖는다. 형식은 `<이름>/v<major>`, 현재 값은
**`specround.ledger/v0`**.

| 상황 | 리더의 행동 |
|---|---|
| `schema` 없음·형식 위반 | 거부 |
| 이름이 다름 (`other.tool/v0`) | 거부 — 남의 원장이다 |
| major 가 다름 (`…/v1`) | **거부. 추측하지 않는다** |
| major 안에서 모르는 최상위 키 | 거부 |

major 안에서 필드집합은 **닫혀 있다**. 모르는 키를 조용히 통과시키면 그 순간 계약이
아니게 되므로, 필드를 늘리는 실험은 예약 필드 **`ext`**(객체)에 담는다. `ext` 안은
리더가 검사하지 않고 보존만 한다. `ext` 로 굴려본 뒤 승격할 때 major 를 올린다.

**스토어가 홈 중앙으로 옮겨간 개정이 major 를 올리지 않은 이유**: `doc` 의 규칙이
"`.specround` 의 부모 기준" 에서 "스토어 base 기준" 으로 **일반화**됐을 뿐이고, in-tree
스토어에서는 base 가 곧 `.specround` 의 부모라 기존 v0 원장의 모든 줄이 **글자 그대로 같은
뜻**으로 읽힌다. 필드도 늘지 않았다. major 는 "읽던 것이 다르게 읽히기 시작할 때" 올리고,
스토어가 어디에 있는지는 원장 **밖의** 사실이다. (`origin` 은 원장이 아니라 별도 파일이고
자기 스키마 `specround.origin/v0` 를 따로 갖는다.)

## 3. 공통 봉투

모든 이벤트가 공통으로 갖는다. 전부 필수.

| 필드 | 타입 | 뜻 |
|---|---|---|
| `schema` | 문자열 | `specround.ledger/v0` |
| `seq` | 0 이상 정수 | **파일에서의 0-기반 위치.** 줄 번호와 반드시 일치한다 |
| `ts` | 문자열 | UTC ISO8601 초 단위(`2026-02-01T09:00:00Z`). **순서에 쓰지 않는다** |
| `type` | 문자열 | 아래 6종 중 하나 |
| `id` | 문자열 | 이 이벤트의 식별자. 종류 접두 + 다이제스트 12자 |
| `author` | 문자열 | 주체. 사람이든 에이전트든 같은 칸(`alice`, `agent:reviewer`) — G4 |

`id` 는 호출자가 주지 않으면 도구가 파생한다: 접두(`r` 라운드 · `c` 코멘트 ·
`s` 제안 · `p` 회신 · `d` 처분 · `x` 라운드 닫기) + `sha256(id 를 뺀 나머지 전체)` 앞
12자. 다이제스트가 `seq` 를 포함하므로 **내용이 같은 두 코멘트도 id 가 갈리고**, 같은
순서로 재생하면 같은 id 가 나온다.

## 4. 이벤트 6종

### `round.open` — 라운드 열기

문서의 현재 내용을 **박제**하고 그 스냅숏을 base 로 삼는다. 이게 G2 의 핵심이다:
리뷰의 기준은 커밋이 아니라 도구가 박제한 스냅숏이므로, 리뷰를 열기 위해 stage·commit 할
일이 없다(G10).

| 필드 | 필수 | 뜻 |
|---|---|---|
| `doc` | ✓ | 문서 경로(상대 POSIX) |
| `base` | ✓ | 스냅숏 참조 `sha256:<64 hex>` |
| `title` | | 라운드 이름(빈 문자열 허용) |

### `comment.add` — 코멘트

| 필드 | 필수 | 뜻 |
|---|---|---|
| `round` | ✓ | 대상 라운드 id (**열려 있어야 한다**) |
| `body` | ✓ | 본문(빈 문자열 불가) |
| `anchor` | | 문서 앵커(§5). 없으면 문서 전체에 대한 코멘트 |

### `suggestion.add` — 제안 (G8)

본문이 패치인 코멘트. 처분 축은 코멘트와 같고(apply/기각), 실체가 diff 라서 필드가 갈린다.

| 필드 | 필수 | 뜻 |
|---|---|---|
| `round` | ✓ | 대상 라운드 id (열려 있어야 한다) |
| `patch` | ✓ | 패치 본문 |
| `body` | | 제안 사유(선택 — 패치가 substance 라서) |
| `anchor` | | 문서 앵커 |

### `reply` — 회신

| 필드 | 필수 | 뜻 |
|---|---|---|
| `target` | ✓ | 코멘트·제안 id. **평면 구조** — 회신에 회신하지 않는다 |
| `body` | ✓ | 본문 |

닫힌 라운드의 코멘트에도 회신할 수 있다(늦게 답하는 것은 정상이다).

### `disposition` — 처분 (G3)

| 필드 | 필수 | 뜻 |
|---|---|---|
| `target` | ✓ | 코멘트·제안 id |
| `verdict` | ✓ | `applied` 반영 · `rejected` 기각 · `answered` 답변 · `deferred` 보류 |
| `reason` | ✓ | 사유(빈 문자열 불가) — 네 판정 전부에서 필수 |

어휘는 **닫혀 있다**. `wontfix` 같은 임의 값은 거부한다.

### `round.close` — 라운드 닫기

| 필드 | 필수 | 뜻 |
|---|---|---|
| `round` | ✓ | 대상 라운드 id (열려 있어야 한다) |
| `unresolved` | 조건부 | 미처분으로 남기고 닫는 코멘트 id 목록(정렬). 미처분이 있으면 **필수** |
| `note` | | 닫는 메모 |

미처분을 남기고 닫는 것 자체는 막지 않는다. 막는 것은 **감추고 닫는 것**이다 — 남긴
목록이 실제 미처분 집합과 정확히 같지 않으면 리더가 거부한다(I6). 그래서 손으로 쓴
`round.close` 도 열린 코멘트를 조용히 지나칠 수 없다.

## 5. 앵커 (G1)

W3C Web Annotation 셀렉터 쌍이다 — 인용(`TextQuoteSelector`)과 위치
(`TextPositionSelector`)를 **같이** 싣는다. 하나만으로는 부실해서다: 위치는 위쪽이 한 자만
바뀌어도 죽고, 인용은 같은 구절이 반복되면 어디인지 모른다.

| 필드 | 필수 | 뜻 |
|---|---|---|
| `exact` | ✓ | 인용 문자열(빈 문자열 = 삽입 지점) |
| `start` / `end` | ✓ | 문자 offset. `end - start == len(exact)` |
| `prefix` / `suffix` | | 앞뒤 문맥 각 32자(문서 경계에서 잘림) |

**쓰기 시점 정합 불변식**: `text[start:end] == exact` 이고 문맥도 그 위치에서 맞아야
한다. 어긋난 앵커는 나중에 복구가 불가능하므로 append 전에 거부한다.

검증 기준 텍스트는 **그 라운드의 base 스냅숏**이다 — 리뷰어가 읽은 텍스트가 그것이고,
그래서 문서가 그 뒤에 바뀌어도 코멘트는 정확한 자리에 붙는다. 개정된 문서에서 앵커를
다시 찾는 일(fuzzy 재앵커·고아 코멘트 처분)은 **H4** 이고 이 버전에 없다. 지금 구현은
추측하지 않고 stale 을 보고만 한다.

offset 이 바이트가 아니라 **문자**이고 스냅숏은 정규화 없이 원본 바이트를 보존한다
(CRLF·말미 개행 포함) — 스냅숏을 건드리면 앵커가 조용히 밀린다.

## 6. 불변식

리더가 강제한다. 위반은 예외이고, "그 줄만 건너뛰기" 같은 관용은 없다.

| id | 불변식 | 어기면 |
|---|---|---|
| I1 | **append-only.** 갱신·삭제 연산이 없다. 새 줄만 붙는다 | — |
| I2 | `seq` == 파일에서의 줄 위치 | 손으로 지우거나 순서를 바꾸면 **에러**(조용한 오답 아님) |
| I3 | `id` 는 원장 전체에서 유일 | 거부 |
| I4 | 코멘트·제안은 **열린** 라운드를 지목한다 | 거부(새 라운드를 열라) |
| I5 | 종결된 코멘트는 재처분 불가 | 거부 |
| I6 | `round.close` 의 `unresolved` == 실제 미처분 집합 | 거부 |
| I7 | 앵커는 라운드 base 와 정합 | 거부 |
| I8 | 회신·처분의 `target` 은 존재하는 코멘트·제안 | 거부 |

**읽는 코드가 곧 쓰는 게이트다.** 쓰기는 `prior + 새 레코드` 를 fold 해보고 통과할 때만
파일에 붙인다. 그래서 API 로 들어온 것과 손으로 쓴 것에 **같은 오라클**이 걸린다 —
검사 로직이 두 벌이면 반드시 갈라진다.

## 7. 처분 상태 모델

```
(처분 없음) ──deferred──► deferred ──applied/rejected/answered──► 종결
     │                        │
     └──applied/rejected/answered──────────────────────► 종결 (재처분 거부)
```

**`deferred`(보류)만 비종결이다.** 보류가 종결이면 보류한 항목이 "봐야 할 것" 목록에서
사라져 보류라는 판정을 둘 이유가 없어진다. 그래서:

- **미처분** = 처분이 없음 ∪ 최신 처분이 `deferred`
- `applied`·`rejected`·`answered` 는 최종. 뒤집으려면 새 라운드에서 새 코멘트를 단다
  (원장을 고쳐 과거를 바꾸지 않는다)
- 처분은 여러 번 append 될 수 있고 **최신이 현재 상태**다. 이력은 전부 남는다

## 8. fold 결정성

`fold` 는 원장만 읽어 현재를 계산한다 — 열린 라운드, 미처분 코멘트. 다른 어디에도 이
상태의 사본이 없다(어긋날 두 번째 사본을 만들지 않는다).

- **순수 함수**다. 시계·난수·파일시스템을 보지 않는다. 같은 줄 순서 → 같은 상태
- **순서는 `seq`, `ts` 는 데이터다.** 시계가 튀거나 뒤로 가도 결과가 변하지 않는다
- 줄이 정본이라 상태를 캐시하지 않는다. 재계산이 항상 답이다

## 9. 실제 원장 예시

아래는 도구가 실제로 낸 출력이다(손으로 쓴 예가 아니다). 라운드 하나에서 코멘트 ·
제안 · 회신 · 반영 · 보류 · 기각 · 미처분 1건을 남기고 닫기까지.

```jsonl
{"author":"alice","base":"sha256:35c081dd8b8aea1c491c9b6e76eb6ae8e7675e7cfceb679fc5ca2652ba8ff8e5","doc":"protocol.md","id":"r-59add8920c91","schema":"specround.ledger/v0","seq":0,"title":"round 1","ts":"2026-02-01T09:00:00Z","type":"round.open"}
{"anchor":{"end":42,"exact":"30 seconds","prefix":"# Widget protocol\n\nTimeouts are ","start":32,"suffix":". Retries are not specified yet."},"author":"bob","body":"too short for the proxy","id":"c-d35c1ebd2b14","round":"r-59add8920c91","schema":"specround.ledger/v0","seq":1,"ts":"2026-02-01T09:03:00Z","type":"comment.add"}
{"anchor":{"end":42,"exact":"30 seconds","prefix":"# Widget protocol\n\nTimeouts are ","start":32,"suffix":". Retries are not specified yet."},"author":"agent:reviewer","id":"s-086c5beb81f0","patch":"@@\n-Timeouts are 30 seconds.\n+Timeouts are 60 seconds.\n","round":"r-59add8920c91","schema":"specround.ledger/v0","seq":2,"ts":"2026-02-01T09:06:00Z","type":"suggestion.add"}
{"author":"alice","body":"the proxy caps at 45s","id":"p-1f41667adcfb","schema":"specround.ledger/v0","seq":3,"target":"c-d35c1ebd2b14","ts":"2026-02-01T09:09:00Z","type":"reply"}
{"author":"alice","id":"d-41174ba7d147","reason":"raised to 60 in revision 2","schema":"specround.ledger/v0","seq":4,"target":"c-d35c1ebd2b14","ts":"2026-02-01T09:12:00Z","type":"disposition","verdict":"applied"}
{"author":"bob","body":"retry policy is still missing","id":"c-7863abd8f91e","round":"r-59add8920c91","schema":"specround.ledger/v0","seq":5,"ts":"2026-02-01T09:15:00Z","type":"comment.add"}
{"author":"alice","id":"d-9fcf73b0ed21","reason":"waiting on the retry spec","schema":"specround.ledger/v0","seq":6,"target":"c-7863abd8f91e","ts":"2026-02-01T09:18:00Z","type":"disposition","verdict":"deferred"}
{"author":"alice","id":"d-5fa8d28ebf7d","reason":"superseded by the comment above","schema":"specround.ledger/v0","seq":7,"target":"s-086c5beb81f0","ts":"2026-02-01T09:21:00Z","type":"disposition","verdict":"rejected"}
{"author":"alice","id":"x-f0c5b47ca4e9","note":"retries move to round 2","round":"r-59add8920c91","schema":"specround.ledger/v0","seq":8,"ts":"2026-02-01T09:24:00Z","type":"round.close","unresolved":["c-7863abd8f91e"]}
```

이 원장을 fold 하면: 열린 라운드 0개, 미처분 1건(`c-7863abd8f91e`, `deferred`).
보류한 코멘트가 라운드를 넘어 살아남는 것이 G3 이 말하는 유실 0 이다.

줄은 **키 정렬 + 공백 없는** canonical JSON 이다(같은 레코드 → 항상 같은 바이트, 그래서
id 파생이 성립하고 파일 비교가 의미를 갖는다). 한글은 `\u` 이스케이프하지 않는다 — 사람이
`cat` 으로 읽을 수 있어야 한다(G4).

## 10. 이 포맷이 아직 정하지 않은 것

포맷에 자리는 있지만 규칙이 없는 것들. 예측으로 파지 않고 결정이 막힐 때 채운다.

- **H4 재앵커** — 개정 후 앵커를 다시 찾는 매칭 규칙과 실패 시 고아 코멘트 처분. 앵커
  필드는 이미 4단 폴백(위치 힌트 → verbatim → 정규화 → fuzzy)에 필요한 정보를 다 싣고
  있고, 재앵커가 들어오면 `anchor` 갱신을 **새 이벤트**로 남긴다(과거 줄은 고치지 않는다).
- **H8 제안의 stale** — 라운드 head 가 움직였을 때 제안을 최신에 옮겨 적용할지, 라운드를
  동결할지. 재앵커 계열로 푼 선례가 있다(Gerrit 3.11+).
- **H9 외부 코멘트 흡수** — 다른 diff 코멘트 저장소를 이 원장으로 변환하는 어댑터의 경계.
  변환기 입력 포맷 후보는 rdjson.
- **원장 병합** — 두 사람이 각자 append 한 원장을 합치는 규칙(현재는 파일 하나 · 같은
  디렉토리 · 락 기준). git 으로 공유하면 정렬·중복제거 병합이 필요해진다.
