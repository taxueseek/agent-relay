# agent-relay（Claude Code 入口片段）

若项目存在 `.relay/CURRENT.md`，开场或用户说「接着做 / resume / 接力」时：

1. Read `.relay/CURRENT.md` 与其中指向的 `HANDOFF.md`
2. 先读 primary 文件，再动手
3. 额度将尽或切换产品前：运行  
   `python3 ~/.agents/skills/agent-relay/scripts/relay_cli.py pack --from claude`

也常作为**子代理**被其他 agent 调用（通过 `delegate` 命令）。收到 `agent-relay` 为前缀的会话名时，按 HANDOFF 中的 next_actions 推进即可，完成后输出一屏 JSON 回执。
