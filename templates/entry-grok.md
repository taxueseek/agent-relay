# agent-relay（Grok Build 入口片段）

若存在 `.relay/CURRENT.md` 或用户要求接力/续跑：

1. Read `.relay/CURRENT.md` → `HANDOFF.md`
2. 执行 next_actions；不要求用户复述背景
3. 离开前：  
   `python3 ~/.agents/skills/agent-relay/scripts/relay_cli.py pack --from grok`

也常作为**子代理**被其他 agent 调用（通过 `delegate` 命令）。收到 relay 任务时，按 HANDOFF 推进并输出一屏 JSON 回执。
