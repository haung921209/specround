# specround

Spec review rounds for humans and AI agents — comments that survive revisions,
edits that become suggestion diffs, and every disposition recorded in an
append-only ledger the tool keeps for you. No server, no git required.

사람과 에이전트가 스펙 문서를 사이에 두고 리뷰를 도는 도구. 코멘트는 개정을 넘어
살아남고(앵커), 편집은 제안 diff 로 접수되고, 모든 처분이 append-only 원장에 남는다.
원장은 **홈 중앙 스토어**에 문서 경로 키로 사는 게 기본이라 리뷰가 문서 폴더에 아무것도
남기지 않는다. 팀이 이력을 공유하려면 `.specround.json` 으로 repo 안에 되돌린다.
원장을 커밋하는 것은 공유·내구의 선택층이지 동작 전제가 아니다.

**status: 원장 코어 + CLI 착지 · 웹뷰 미착수.** 계약은 [SPEC.md](SPEC.md)(보장 G1~G11),
원장 포맷·스토어 위치는 [docs/ledger-format.md](docs/ledger-format.md).
이 도구의 첫 고객은 이 도구 자신의 스펙 리뷰다.

## 한 라운드

이 repo 안에서는 `uv run specround …`, 설치했으면 `specround …`.

```bash
# Freeze the document as this round's base. Nothing is staged, nothing is committed.
specround round open SPEC.md --title "first pass"

# Comment on a span of that base. A quote that repeats asks which one you mean.
specround comment SPEC.md --quote "30 seconds" --body "too short for the proxy"
specround comment SPEC.md --body-file - <<< "the whole retry section is missing"

specround comments SPEC.md            # a table; --json on any verb for an agent
specround round status SPEC.md        # rounds, counts, what is still outstanding

# Every comment gets a verdict and a reason: applied · rejected · answered · deferred.
specround dispose SPEC.md --comment c-d35c --as applied --why "raised to 60"
specround round close SPEC.md --allow-unresolved --note "retries move to round 2"

# After a revision: carry the comments over, and say which ones lost their text.
specround reanchor SPEC.md
```

`--author` (or `$SPECROUND_AUTHOR`) says who is speaking — a person or
`agent:reviewer`, same field, same commands (G4). 그래서 **판정은 종료코드로 한다**:
`0` 성공 · `2` 명령을 고쳐라(반복 인용 → `--occurrence`) · `3` 이력이 거부한다(열린
라운드 없음 → `round open`) · `1` 그 외. 성공 출력은 stdout, 오류는 stderr 라
`--json | jq` 가 오류 객체를 결과로 받지 않는다.

```bash
uv run pytest
```
