# 원장 포맷 — `specround.ledger/v0`

> 이 포맷이 계약이다. 코어·CLI·웹뷰는 갈아탈 수 있는 구현이고, 남는 것은 이 파일
> 스키마다(SPEC.md G5). 그래서 포맷 문서가 구현 문서보다 상위에 있다.

계약의 최소 문장: **한 줄 = 한 이벤트 = 하나의 JSON 객체.** 이 줄들을 쓸 수 있으면
어떤 언어·에디터·에이전트든 참여자이고, 읽는 도구가 없어도 `cat` 이 유효한 리더다.

## 1. 어디에 사는가

```
<문서가 있는 디렉토리>/
  protocol.md              ← 리뷰 대상
  spec.md                  ← 같은 디렉토리의 다른 문서도 같은 원장을 쓴다
  .specround/
    ledger.jsonl           ← 이벤트 로그 (append-only)
    objects/35/c081dd…     ← 문서 스냅숏 (내용주소, sha256)
```

- 한 `.specround/` 가 **그 디렉토리의 문서 전부**를 담당한다. 이벤트는 자기 문서를
  `doc` 필드에 **`.specround` 의 부모 기준 상대 POSIX 경로**로 적는다 — 상대라서
  디렉토리를 옮기거나 clone 해도 원장이 그대로 유효하다.
- **git 호출 0.** 원장·스냅숏은 평문 파일이다. 문서가 untracked 든 repo 밖이든 전 기능이
  돈다. 원장을 커밋하는 것은 공유·내구의 선택층일 뿐이고 기록의 전제가 아니다(G5·G10).

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

**이벤트 종류도 같은 의미로 닫혀 있다** — 모르는 `type` 은 거부다. 그래서 종류를 늘리는
것은 원칙적으로 major 를 올리는 변경이고, `anchor.reanchor`·`anchor.orphan` 두 종이
v0 에 들어온 것은 예외가 아니라 **§10 이 비워 둔 자리를 채운 것**이다(그 항목이 "재앵커가
들어오면 앵커 갱신을 새 이벤트로 남긴다" 고 미리 적어 두었다). v0 은 아직 고정 전이고
이 두 종보다 앞선 v0 원장은 존재하지 않는다. **v0 이 고정된 뒤에 종류를 늘리려면 major 를
올린다** — 구버전 리더가 그 줄에서 파일 전체를 거부하기 때문이다.

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
`s` 제안 · `p` 회신 · `d` 처분 · `x` 라운드 닫기 · `a` 재앵커 · `o` 고아) +
`sha256(id 를 뺀 나머지 전체)` 앞
12자. 다이제스트가 `seq` 를 포함하므로 **내용이 같은 두 코멘트도 id 가 갈리고**, 같은
순서로 재생하면 같은 id 가 나온다.

## 4. 이벤트 8종

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

### `anchor.reanchor` — 앵커를 새 스냅숏에 다시 묶기 (G1)

문서가 개정되면 코멘트의 앵커를 개정본에서 다시 찾아 붙인다. **과거 줄은 고치지 않는다** —
코멘트가 만들어질 때의 앵커는 그 자리에 그대로 남고, "지금 어디에 있나" 는 이 이벤트의
최신값이다.

| 필드 | 필수 | 뜻 |
|---|---|---|
| `target` | ✓ | 코멘트·제안 id. **앵커가 있는 것**이어야 한다(I9) |
| `base` | ✓ | 이 앵커가 정합하는 스냅숏 참조 `sha256:<64 hex>` |
| `anchor` | ✓ | 새 앵커(§5). 그 스냅숏에서 잘라낸 것이다 |
| `strategy` | ✓ | 어느 단으로 찾았나 — `position`·`quote`·`normalized`·`fuzzy` |
| `ambiguous` | | `true` 면 같은 점수의 자리가 둘 이상이었고 위치 힌트가 골랐다 |

`strategy` 어휘는 **닫혀 있다**. 이 값이 있어야 읽는 쪽이 "그냥 밀려난 코멘트" 와
"본문이 다시 쓰인 코멘트" 를 구분한다 — 뒤쪽은 사람이 한 번 봐야 하는 것이고, 앞쪽은
아니다. `ambiguous` 도 같은 목적이다: **조용히 고르지 않는다**.

**점수(유사도 수치)는 싣지 않는다.** 줄은 언어와 무관하게 같은 바이트여야 하고(id 파생이
거기에 걸려 있다) 부동소수 표기는 그 약속이 깨지는 전형적인 자리다. 더 자세한 계측이
필요하면 `ext` 에 담는다.

라운드가 열려 있을 필요는 없다. 코멘트는 라운드보다 오래 살고, 본문을 움직이는 개정은
대개 라운드가 닫힌 **뒤**에 온다.

### `anchor.orphan` — 앵커를 못 찾았다 (G1 × G3)

재앵커가 실패하면 코멘트를 조용히 떨어뜨리는 대신 **못 찾았다는 사실을 기록**한다.
이것이 유실 0 이 개정 축에서 뜻하는 바다.

| 필드 | 필수 | 뜻 |
|---|---|---|
| `target` | ✓ | 코멘트·제안 id. 앵커가 있는 것이어야 한다(I9) |
| `base` | ✓ | 못 찾은 그 스냅숏 |
| `reason` | ✓ | 왜 못 찾았는지(빈 문자열 불가) |

고아는 **처분이 아니다**. 처분은 "이 지적을 어떻게 했나" 고 고아는 "이 코멘트를 아직
문서 위에 놓을 수 있나" 다 — 축이 다르다. 반영된 코멘트도 고아일 수 있고(반영하면서
본문을 지웠으니 오히려 흔한 경우다), 고아면서 미처분일 수도 있다.

**고아는 앵커를 잃지 않는다.** 마지막으로 성공한 앵커가 그대로 현재 앵커고, 이후 개정에서
그 본문이 돌아오면 `anchor.reanchor` 가 다시 붙는다(append 라 되살아나는 것이 자연스럽다).

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

검증 기준 텍스트는 **그 앵커가 명명한 스냅숏**이다. `comment.add`·`suggestion.add` 면
그 라운드의 base(리뷰어가 읽은 텍스트), `anchor.reanchor` 면 그 이벤트의 `base` 다.

offset 이 바이트가 아니라 **문자**이고 스냅숏은 정규화 없이 원본 바이트를 보존한다
(CRLF·말미 개행 포함) — 스냅숏을 건드리면 앵커가 조용히 밀린다.

### 5.1 개정을 건너는 재앵커 (H4)

개정본에서 앵커를 다시 찾는 규칙이다. 네 단을 순서대로 시도하고, **어느 단의 결과든
개정본에서 다시 잘라낸다** — 그래서 기록되는 앵커는 자기가 명명한 스냅숏에 대해 항상
정합한다. 옛 인용·옛 문맥을 그대로 들고 가지 않는다.

| 단 | `strategy` | 무엇을 잡나 |
|---|---|---|
| 1 | `position` | offset 이 그대로 맞는다 — 위쪽이 안 움직였다 |
| 2 | `quote` | 인용이 그대로 있다 — 윗줄 삽입·문단 이동 |
| 3 | `normalized` | 따옴표·대시·공백 런을 접으면 같다 — 리플로우·타이포그래피 교정 |
| 4 | `fuzzy` | 인용 자체가 고쳐졌다 — 근사 정렬 + 유사도 하한 |

같은 인용이 여러 번 나오면 **문맥과 옛 위치**가 고른다(그래서 앵커가 문맥을 싣는다).
점수가 같은 자리가 둘 이상이면 위치가 결정하되 `ambiguous` 로 표시한다. 네 단이 모두
하한을 못 넘으면 `anchor.orphan` 이다 — **추측해서 아무 데나 붙이지 않는다.**

비용은 문서 크기가 아니라 상수로 묶여 있다(후보 상한·정렬 창 고정). 근사 매칭은 2차라서
그 상한이 없으면 짧고 흔한 인용 + 긴 문서에서 수 초씩 멈춘다 — Hypothesis 가 실측한
실패모드다.

이 규칙은 **포맷이 아니라 도구의 것**이다. 원장이 요구하는 것은 `strategy` 어휘와
"기록된 앵커는 자기 `base` 에 정합한다"(I7) 뿐이고, 하한·후보 생성 방식을 바꾸는 것은
스키마 변경이 아니다. 그래서 점수를 안 싣는다 — 튜닝값이 계약에 새면 못 바꾼다.

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
| I7 | 앵커는 자기가 명명한 스냅숏과 정합(코멘트=라운드 base · 재앵커=그 이벤트의 `base`) | 거부 |
| I8 | 회신·처분의 `target` 은 존재하는 코멘트·제안 | 거부 |
| I9 | 재앵커·고아의 `target` 은 **앵커를 가진** 코멘트·제안 | 거부(문서 전체 코멘트는 옮길 자리가 없다) |

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
제안 · 회신 · 반영 · 보류 · 기각 · 미처분 1건을 남기고 닫은 뒤, 개정 두 번을 건너간다 —
2차 개정에서 재앵커, 3차 개정에서 고아.

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
{"anchor":{"end":42,"exact":"60 seconds","prefix":"# Widget protocol\n\nTimeouts are ","start":32,"suffix":". Retries are not specified yet."},"author":"agent:reanchor","base":"sha256:3c4fa2b65eaa07b84f7d1892bd487159215dd7bfd89171c8b3f0e518bd3dc7c9","id":"a-b06a968813bd","schema":"specround.ledger/v0","seq":9,"strategy":"fuzzy","target":"c-d35c1ebd2b14","ts":"2026-02-01T09:30:00Z","type":"anchor.reanchor"}
{"anchor":{"end":42,"exact":"60 seconds","prefix":"# Widget protocol\n\nTimeouts are ","start":32,"suffix":". Retries are not specified yet."},"author":"agent:reanchor","base":"sha256:3c4fa2b65eaa07b84f7d1892bd487159215dd7bfd89171c8b3f0e518bd3dc7c9","id":"a-03e8060eff2c","schema":"specround.ledger/v0","seq":10,"strategy":"fuzzy","target":"s-086c5beb81f0","ts":"2026-02-01T09:30:00Z","type":"anchor.reanchor"}
{"author":"agent:reanchor","base":"sha256:934db591c7f03e4dd37fca8750eb99403e89debcc09730413c7e7aba38e1f80e","id":"o-7762e917b458","reason":"quote '60 seconds' is not in the revised text, and no span reaches 0.70 similarity","schema":"specround.ledger/v0","seq":11,"target":"c-d35c1ebd2b14","ts":"2026-02-01T10:00:00Z","type":"anchor.orphan"}
{"author":"agent:reanchor","base":"sha256:934db591c7f03e4dd37fca8750eb99403e89debcc09730413c7e7aba38e1f80e","id":"o-c0c577e2d9db","reason":"quote '60 seconds' is not in the revised text, and no span reaches 0.70 similarity","schema":"specround.ledger/v0","seq":12,"target":"s-086c5beb81f0","ts":"2026-02-01T10:00:00Z","type":"anchor.orphan"}
```

이 원장을 fold 하면: 열린 라운드 0개, 미처분 1건(`c-7863abd8f91e`, `deferred`),
고아 2건(`c-d35c1ebd2b14`·`s-086c5beb81f0`). 보류한 코멘트가 라운드를 넘어 살아남고,
본문이 사라진 코멘트가 조용히 없어지는 대신 고아로 남는 것이 G3 이 말하는 유실 0 이다.

두 축이 따로 논다는 것도 이 예시에 있다: `c-d35c1ebd2b14` 는 `applied` 로 **종결**됐지만
3차 개정에서 **고아**다(반영하면서 그 문장을 지웠으니 당연하다). 처분이 끝난 것과
문서 위에 놓을 수 있는 것은 다른 질문이다.

seq 9·10 의 `strategy` 가 `fuzzy` 인 것도 읽을 거리다 — "30 seconds" 가 "60 seconds" 로
바뀌었으니 인용이 그대로 있는 것이 아니라 **본문이 고쳐진** 경우이고, 사람이 한 번 볼
값어치가 있다는 뜻이다.

줄은 **키 정렬 + 공백 없는** canonical JSON 이다(같은 레코드 → 항상 같은 바이트, 그래서
id 파생이 성립하고 파일 비교가 의미를 갖는다). 한글은 `\u` 이스케이프하지 않는다 — 사람이
`cat` 으로 읽을 수 있어야 한다(G4).

## 10. 이 포맷이 아직 정하지 않은 것

포맷에 자리는 있지만 규칙이 없는 것들. 예측으로 파지 않고 결정이 막힐 때 채운다.

- **H8 제안의 stale** — 재앵커가 들어왔으니 **기계는 갖췄다**: 제안의 앵커도 코멘트와
  똑같이 개정을 건넌다(§5.1). 남은 것은 포맷이 아니라 정책이다 — 옮겨간 앵커에 옛 패치를
  그대로 적용해도 되는지, `fuzzy` 로 옮겨간 제안은 사람 확인을 받아야 하는지. 라운드 동결이
  아니라 재앵커 계열로 푼 선례가 있다(Gerrit 3.11+).
- **고아·모호 재앵커의 처리 정책** — 원장은 `anchor.orphan` 과 `ambiguous` 로 **보고**만
  한다. 그것을 누가 언제 보고 무엇을 해야 하는지(다음 라운드에서 다시 묻는가, 고아를
  문서 말미에 모아 보이는가)는 뷰·CLI 의 몫이고 아직 안 정했다.
- **H9 외부 코멘트 흡수** — 다른 diff 코멘트 저장소를 이 원장으로 변환하는 어댑터의 경계.
  변환기 입력 포맷 후보는 rdjson.
- **원장 병합** — 두 사람이 각자 append 한 원장을 합치는 규칙(현재는 파일 하나 · 같은
  디렉토리 · 락 기준). git 으로 공유하면 정렬·중복제거 병합이 필요해진다.
