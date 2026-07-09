# agent-relay packet schema v1

`schema` 必须为 `agent-relay/v1`。

## 必填字段

| 字段 | 类型 | 说明 |
|------|------|------|
| schema | string | 固定 `agent-relay/v1` |
| id | string | `YYYYMMDD-HHMMSS` |
| goal | string | 可机械判定的完成标准 |
| status | string | in_progress / blocked / ready_for_handoff / done |
| next_actions | string[] | 有序下一步 |
| files | object | primary / touched / do_not_touch |
| provenance | object | sources[] / env_detected / conflicts |
| routing | object | from_peer / to_peer / recommended_peer / reason / handoff_phrase |

## provenance.sources[]

```json
{ "peer": "claude|grok|zcode|…", "path": "…", "role": "primary|related" }
```

## 文件布局

```
~/.agents/relay/<project-slug>/<id>/
  packet.json
  HANDOFF.md
  sources.json
  result.json          # invoke/delegate 结束后
  invoke/LAST_JOB.json # 异步任务状态
```

## 相关 schema（旁路，非 packet 必填）

| schema | 用途 |
|--------|------|
| `agent-relay/goal-contract/v1` | `goal-lint` 输出 |
| `agent-relay/plan/v1` | `plan` 干跑 |
| `agent-relay/job/v1` | `job-status` 摘要 |
| `agent-relay/result/v1` | `result.json` |
