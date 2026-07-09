# agent-relay

跨产品 Agent 任务接力 skill。CLI 入口：`scripts/relay_cli.py`。

- 证据层依赖可选的 [session-digger](https://github.com/taxueseek/session-digger)
- 真源：磁盘文件 + git；packet 是导航
- 信任闭环：goal-lint → plan → pack/delegate → job-status / VERIFY
- 勿把密钥写入 packet；小任务勿开多 peer 舰队

详见 `SKILL.md`、`README.md`。
