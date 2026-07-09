---
name: agent-relay
description: |
  跨产品 Agent 任务接力、委派与协作。额度切换、压缩续跑、子代理调用（Claude Code / Grok Build / Kimi Code / Codex 等）。
  触发：接力、交接、handoff、delegate、委派、resume、接着做、额度没了、换环境继续、跨 Agent、子代理、bridge、启动协作、pack 会话、goal-lint、先 plan。
  不负责：会话分析本体（用 session-digger）、在线多 Agent 通信（CommHub）、Codex 舰队编排（用 codex-workflows）。
version: 0.1.3
---

# agent-relay

> 产品无关的任务接力与委派：用 session-digger 证据组装 packet，通过 CLI invoke 交给子代理或任意 peer 读 HANDOFF 续跑。

## CLI

```bash
RELAY=~/.agents/skills/agent-relay/scripts/relay_cli.py
python3 "$RELAY" peers
python3 "$RELAY" envs                         # 本机有哪些 Agent 环境
python3 "$RELAY" workspace --project /path    # 任意工作区 → 各环境落盘目录
python3 "$RELAY" doctor                       # 含 workspace map
python3 "$RELAY" init
# 信任闭环前半：硬化目标 + 干跑（不花 peer token）
python3 "$RELAY" goal-lint --goal "…" [--verify-cmd '…']
python3 "$RELAY" plan --task "…" [--to PEER] [--action delegate|pack|handoff|bridge]
python3 "$RELAY" pack [--from auto|claude|grok|zcode] [--to PEER] [--session PATH] [--goal TEXT] [--deep] [--lint-goal]
python3 "$RELAY" resume [latest|packet-id]
python3 "$RELAY" bridge <keyword> [--deep]
python3 "$RELAY" suggest [--task TEXT]   # peer 推荐 + harness pattern
# 真调（默认 async + lean；--wait 同步等结束）
python3 "$RELAY" invoke --to claude|grok|kimi_code|mimo [--wait] [--visible] [--full-context]
python3 "$RELAY" job-status [--packet latest] [--json] [--verbose]
python3 "$RELAY" handoff --to claude --goal "… VERIFY: test -f path && rg -q TOK path" [--wait] [--visible]
python3 "$RELAY" delegate --to claude --task "写文件 X 内容 Y VERIFY: test -f X && rg -q Y X" [--mode implement|review|fix] [--strict-goal] [--visible]
# goal 建议带 VERIFY: 可脚本验收；结束后读 result.json 一屏回执
# 可见终端默认 Ghostty；zcode 无 CLI invoke
# 评估（按项目 cwd 探测会话；无硬编码用户路径；mac/linux/win）：
#   python3 scripts/eval_wave2.py --project . --dry-run
#   python3 scripts/eval_wave2.py --session "$GROK_SESSION" --to claude
```

**产品目的**：跨产品任务接力/委派——换设备、换工作区、额度切换，任务仍能接上。  
链路：`workspace` 发现本机各 Agent 落盘 → `pack` 按当前项目自动绑会话 → `invoke`/`handoff` 静默或 `--visible` 调用 → `VERIFY`。  
环境适配内置（`core/env_map.py`，不装 digger 也能用）；路径按本机 `~` 与项目路径动态编码。  
E2E：`python3 scripts/e2e_collab.py --project .`

## 信任闭环（借鉴 codex-workflows，落地为 relay 形态）

```
goal-lint → plan → pack/delegate → job-status / VERIFY
```

- **goal-lint**：把模糊 goal 收成可判定合同（VERIFY、sandbox、involvement、scale）
- **plan**：零 token 干跑，报路由 / 风险 / 建议命令
- **VERIFY + result.json**：委派后半验收（已有）
- **不抄**：Codex 舰队 DAG、`human()` GUI、sessionful race — 那些是 codex-workflows 的战场

## 路由

| 用户意图 | 动作 |
|----------|------|
| 目标太糊，先硬化 | `goal-lint --goal "…"` |
| 先估路由/风险再花额度 | `plan --task "…"` |
| 打包离开 / 额度快没了 / 交接 | `pack`（可选 `--lint-goal`） |
| 委派子代理执行任务 | `delegate --to <peer> --task "..."` |
| 接着做 / resume | `resume latest`，注入后先 Read primary 文件 |
| 跨环境对齐 / 另一个产品做到哪 | `bridge <关键词>` |
| 该交给谁 + 用什么模式 | `suggest --task "..."` |
| **直接调用另一产品继续/审查** | `invoke --to claude|grok|kimi_code|mimo` 或 `handoff`；**zcode 仅 resume** |
| 环境是否就绪 | `doctor` / `peers` |
| 为什么这样定（考古） | **session-digger** `/recall`，本 skill 不答 |
| Codex 多代理 fan-out / 执行图 | **codex-workflows** `/codex-workflows`，本 skill 不答 |

## 子代理委派模式

`delegate` 命令将当前 agent 作为「主管」，分配任务给子代理（claude/grok/kimi_code 等），等待完成并获取结构化结果。

```bash
# 委派 Claude 实现一个功能
python3 "$RELAY" delegate --to claude --task "写文件 tests/test_relay.py，测试 delegate 流程。VERIFY: test -f tests/test_relay.py && rg -q delegate tests/test_relay.py"

# 委派 Kimi 审查代码
python3 "$RELAY" delegate --to kimi_code --task "审查 src/core/peers.py，列出 3 个改进建议。mode=review"

# 委派 Grok 修复 bug
python3 "$RELAY" delegate --to grok --task "修复 relay_cli.py 中 session 路径 glob 问题。VERIFY: python3 -c 'print(\\\"ok\\\")'" --mode fix
```

输出格式（调用方可解析）：
```
DELEGATE_OK peer=claude duration=12.3s verify=pass
done: 写文件 tests/test_relay.py
files: tests/test_relay.py
open: (none)
result: /path/to/result.json
```

## 执行纪律

1. pack/resume/bridge/delegate **优先跑 CLI**，不要只凭记忆写交接文  
2. resume 后：**文件与 git 是真源**，packet 是导航  
3. 昂贵 delegate/handoff 前：`goal-lint` 或 `plan`；goal 建议带 `VERIFY:`  
4. 可委派 peer：`claude` · `grok` · `kimi_code` · `mimo`（`mimo run`）；模型：`AGENT_RELAY_MIMO_MODEL`  
5. ZCode：仅 pack/resume（无 CLI invoke），不在一等 peer 中  
6. 勿把密钥写入 packet  
7. **反过度建设**：两行 proof / typo 用 quick + 小 peer，勿 `--deep`、勿多 peer 舰队  

## 模式卡片（`suggest` 会附带）

| id | 防什么 | 典型动作 |
|----|--------|----------|
| `trust_loop` | 糊目标 + 无验收 | goal-lint → delegate → job-status |
| `fresh_context_review` | 作者审自己 | A implement → B `--mode review` |
| `hedged_race` | 单路径死磕 | 两 peer 各试假说，先 VERIFY 胜 |
| `quota_handoff` | 上下文丢失 | pack → resume |
| `cross_peer_align` | 多端各说各话 | bridge |
| `supervisor_checkpoint` | 盲写高风险区 | plan → 人确认 → delegate |
| `anti_overbuild` | 小任务开舰队 | short budget + trivial peer |

## 产物

- 全局：`~/.agents/relay/<project-slug>/<id>/{packet.json,HANDOFF.md,sources.json,result.json}`  
- 项目：`.relay/CURRENT.md`（`init` + 每次 pack 更新）  
- job 摘要：`job-status` 归一化 `running|completed|stopped|idle`（借鉴 fleet 状态，单包粒度）  

## 插件预留

本目录即为未来插件根：`.claude-plugin/plugin.json`、`hooks/`、`commands/` 已占位。本期只作 skill 使用。

## DO NOT

- 不替代 session-digger 分析  
- 不替代 **codex-workflows** 的 Codex 舰队 / sessionful race / GUI map  
- L3 消息总线仍延期；L2 `invoke`/`handoff`/`delegate`：**claude/grok/kimi_code**；**zcode 仅 resume**  
- zcode：保留 digger 证据 + pack/resume；不可 invoke 委派  
