# specround

Spec review rounds for humans and AI agents — comments that survive revisions,
edits that become suggestion diffs, and every disposition recorded in an
append-only ledger beside the document. No server, no git required.

사람과 에이전트가 스펙 문서를 사이에 두고 리뷰를 도는 도구. 코멘트는 개정을 넘어
살아남고(앵커), 편집은 제안 diff 로 접수되고, 모든 처분이 문서 곁 원장에 남는다.
원장을 커밋하는 것은 공유·내구의 선택층이지 동작 전제가 아니다.

**status: 원장 코어 착지 · CLI·웹뷰 미착수.** 계약은 [SPEC.md](SPEC.md)(보장 G1~G10),
원장 포맷은 [docs/ledger-format.md](docs/ledger-format.md).
이 도구의 첫 고객은 이 도구 자신의 스펙 리뷰다.

```bash
uv run pytest
```
