---
name: watchlist
description: Reassess conditional TODO Flow observations and connect them to action, a concrete wait or evidence-backed closure.
---

# watchlist

Use `todo-flow --state STATE watches` and the source track. First consider safe in-scope repair. Confirmed defects need work or a track registered through todo; do not dump them into watch. A watch needs observation, concrete deferral reason, trigger and next_action.

`signal TRIGGER --version VERSION` deduplicates a changed input; only active scope can spawn watch work. For inactive scope it records the need for selection. `watch-dispose WATCH_ID --status resolved|dismissed|promoted --evidence TEXT` records the disposition; promoted also requires --target TRACK_ID. Never close based solely on age or repeat count, and never call watch registration goal completion.

확인된 랜딩마다 track-triage가 원 트랙의 열린 Watch를 자동 재확인하고, 유지·해결·기각·기존/신규 TODO 승격을 근거와 함께 기록한다. 이는 별도 signal 없이 해당 랜딩 사이클 안에서 수행한다. 랜딩과 무관한 외부 조건 변화는 기존 signal/명시 검수 경로를 사용한다.
