# agent-relay

跨产品 Agent 任务接力 skill。CLI：`scripts/relay_cli.py`。

- 目的：额度切换、换工具、换设备后任务仍可接上  
- 环境适配：`core/env_map.py`（内置，不强制 session-digger）  
- 信任闭环：goal-lint → plan → pack/delegate → job-status / VERIFY  
- 静默 invoke 或 `--visible` 可见协作  
- 勿把密钥写入 packet；小任务勿开多 peer  

详见 `README.md`、`SKILL.md`。
