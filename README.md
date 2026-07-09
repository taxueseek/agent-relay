# agent-relay

> 跨产品 Agent 任务接力与委派。额度切换、换工具、换设备时，任务还能接上。

你同时开着 Claude Code、Grok Build、Kimi Code，某天 Claude 额度用完了。以前的做法是复制整段对话粘贴到另一个工具里，上下文一长就丢东西。

agent-relay 的做法是：把「目标、关键文件、下一步」打包成一个轻量级的任务包，传给另一个工具继续。比复制聊天记录短，也不容易漏信息。

环境适配参考了 [session-digger](https://github.com/taxueseek/session-digger) 的多产品路径处理，但 **digger 不是必须装的**：本机有哪些 Agent、当前工作区对应哪个目录，内置注册表会按本机路径自动识别。换机器、换项目文件夹后，重新跑一遍就行。

Python 3.8+，零 pip 依赖。

---

## 它解决什么问题

| 场景 | 做法 |
|------|------|
| 当前产品额度不够了 | `pack` 打交接包，另一产品 `resume` 或 `handoff` |
| 想让子代理去改代码 / 做审查 | `delegate` / `invoke`，goal 带 `VERIFY` |
| 换了电脑或项目路径 | `envs` + `workspace` 重新绑定本机环境与工作区 |
| 希望有人看着跑，或完全后台跑 | `handoff --visible` 打开终端，或不加则静默异步 |

**不做的事**：会话考古与知识沉淀（交给 session-digger）；Codex 舰队级编排（交给专门编排工具）。

---

## 与 session-digger 的关系

| | [session-digger](https://github.com/taxueseek/session-digger) | **agent-relay** |
|--|--|--|
| 定位 | 跨环境会话挖掘、检索、记忆 | 跨产品任务交接与委派 |
| 典型动作 | `/recall`、建索引、趋势 | `pack` / `resume` / `handoff` / `delegate` |
| 产物 | 可搜索索引与记忆文件 | `packet.json`、`HANDOFF.md`、`result.json` |

装了 digger 时，pack 能多抽一些会话证据；没装时，按 git 状态、内置环境映射和手填 goal 也能完成交接。

---

## 安装

```bash
# 推荐：skills 一键安装
npx -y skills add taxueseek/agent-relay -g --all

# 或 clone 到 skills 目录
git clone https://github.com/taxueseek/agent-relay.git ~/.agents/skills/agent-relay
```

可选（增强证据抽取，非必须）：

```bash
npx -y skills add taxueseek/session-digger -g --all
# 或
export SESSION_DIGGER_ROOT=~/.agents/skills/session-digger
```

Claude Code 插件方式：

```bash
git clone https://github.com/taxueseek/agent-relay.git ~/.claude/plugins/agent-relay
```

安装完成后，跑一下环境检查：

```bash
RELAY=~/.agents/skills/agent-relay/scripts/relay_cli.py
python3 "$RELAY" doctor
python3 "$RELAY" envs
python3 "$RELAY" workspace --project .
```

---

## 环境适配（换设备 / 换工作区）

不同 Agent 在本机的数据目录、项目编码方式各不相同。agent-relay 内置环境注册表，在**当前机器、当前项目路径**上自动识别，不写死某台电脑的绝对路径。

| 环境 | 项目路径如何编码（示意） |
|------|--------------------------|
| Claude Code | dash：`/Users/a/app` → `-Users-a-app` |
| Grok Build | URL-encode 项目绝对路径 |
| Kimi Code | 项目名 / hash 目录匹配 |
| Codex / ZCode 等 | 尽力扫描 + 路径片段匹配 |

```bash
# 本机装了哪些 Agent、数据根目录在哪
python3 "$RELAY" envs

# 任意项目文件夹 → 各环境落盘绑定
python3 "$RELAY" workspace --project /path/to/your/repo
python3 "$RELAY" workspace -p . --json
```

换设备后：装好对应 CLI 与 skill，在目标仓库执行 `workspace` / `doctor` 即可重新对齐，无需改脚本里的路径。

---

## 能做什么

- **任务打包**：把目标、关键文件、下一步打成轻量包，不复制全文对话  
- **pack / resume**：额度切换后续跑；resume 会检查文件/git 变化  
- **goal-lint / plan**：先硬化目标、干跑路由，再花 peer 额度  
- **invoke / handoff / delegate**：真调 Claude、Grok、Kimi Code、MiMo 等  
- **静默或可见**：默认异步静默；`--visible` 结束后在 Ghostty / Terminal 打开会话  
- **VERIFY + result.json**：goal 内可写验收命令，结束一屏回执  
- **bridge / suggest**：跨环境对齐进度；推荐 peer 与协作模式  

---

## 快速开始

```bash
RELAY=~/.agents/skills/agent-relay/scripts/relay_cli.py

# 本机环境 + 当前工作区绑定
python3 "$RELAY" envs
python3 "$RELAY" workspace --project .
python3 "$RELAY" peers

# 模糊目标先硬化
python3 "$RELAY" goal-lint --goal "修登录超时 VERIFY: pytest -q tests/test_auth.py"

# 零 token 干跑
python3 "$RELAY" plan --task "修登录超时" --to claude --action delegate

# 打包当前会话（准备换工具）
python3 "$RELAY" pack --from auto --goal "继续修登录超时"

# 另一产品续跑
python3 "$RELAY" resume latest

# 静默委派（后台异步）
python3 "$RELAY" handoff --to grok --goal "写 tests/smoke.md 含 OK VERIFY: test -f tests/smoke.md"

# 可见协作（打开终端窗口）
python3 "$RELAY" handoff --to kimi_code --visible --goal "审查 src/ 列 3 条建议"

# 查 job
python3 "$RELAY" job-status --packet latest
```

推荐闭环：

```text
workspace / peers → goal-lint → plan → pack 或 handoff → job-status / VERIFY
```

端到端自测（静默 + 可见各跑一轮）：

```bash
python3 ~/.agents/skills/agent-relay/scripts/e2e_collab.py --project .
```

---

## 命令一览

| 命令 | 用途 |
|------|------|
| `envs` | 扫描本机 Agent 环境与数据根目录 |
| `workspace` | 将某项目路径映射到各环境的落盘目录 |
| `peers` | 探测可 pack / 可委派的 peer |
| `doctor` | 健康检查（含当前工作区映射） |
| `init` | 初始化项目 `.relay/` |
| `goal-lint` | 硬化目标与验收条件 |
| `plan` | 干跑路由与风险，不花 peer token |
| `pack` | 打证据包 |
| `resume` | 加载 packet / HANDOFF |
| `bridge` | 按关键词跨环境对齐 |
| `suggest` | 推荐 peer 与协作模式 |
| `invoke` | 直接调用另一产品（`--wait` / `--visible`） |
| `handoff` | pack + invoke |
| `delegate` | 主管式委派：implement / review / fix |
| `job-status` | 异步 job 状态：running / completed / stopped / idle |

---

## 支持的 peer

| Peer | 证据 pack | 委派 invoke | 说明 |
|------|-----------|-------------|------|
| Claude Code | ✓ | ✓ | 一等 peer |
| Grok Build | ✓ | ✓ | 一等 peer |
| Kimi Code | ✓ | ✓ | 有 CLI 即可 |
| MiMo | ✓ | ✓ | `mimo run` |
| Codex | ✓ | ✓* | 探测到 CLI 时可用 |
| ZCode | ✓ | 默认关 | 仅 pack/resume；实验：`AGENT_RELAY_ENABLE_ZCODE_INVOKE=1` |

\* 复杂 Codex 舰队编排请用专用工具，本 skill 不做第二套编排层。

---

## 产物

```text
~/.agents/relay/<project-slug>/<packet-id>/
  packet.json
  HANDOFF.md
  sources.json
  result.json      # 委派回执
  invoke/          # job 与日志

<project>/.relay/CURRENT.md
```

Schema：`agent-relay/v1`。packet 默认短预算，避免 token 膨胀。

---

## 环境变量（可选）

| 变量 | 作用 |
|------|------|
| `SESSION_DIGGER_ROOT` / `SD_ROOT` | session-digger 路径（可选增强） |
| `AGENT_RELAY_HOME` | packet 存储根，默认 `~/.agents/relay` |
| `AGENT_RELAY_TERMINAL` | 可见终端：`Ghostty` / `Terminal` 等 |
| `AGENT_RELAY_MIMO_MODEL` | MiMo 模型名 |
| `AGENT_RELAY_ENABLE_ZCODE_INVOKE` | 实验性打开 ZCode invoke |
| `AGENT_RELAY_EVAL_SESSION` | eval / 调试时指定会话文件 |

---

## 开发与测试

```bash
cd agent-relay
python3 tests/test_packet_and_peers.py
python3 scripts/e2e_collab.py --project /path/to/repo
python3 scripts/eval_wave2.py --project . --dry-run
```

更多：`docs/PLAN.md`、`references/packet-schema.md`、`SKILL.md`。

---

## 版本

### v0.1.3

- 内置环境适配（`env_map`）：`envs` / `workspace`，换设备与工作区可重新绑定  
- 会话发现默认走原生多环境映射，session-digger 改为可选增强  
- 静默与可见协作 E2E（`e2e_collab.py`）  
- 跨平台 VERIFY 辅助、doctor 展示工作区映射  

### v0.1.2

- 信任闭环：goal-lint → plan → pack/delegate → job-status / VERIFY  
- L2 真调：claude / grok / kimi_code / mimo；async + lean  
- 模式卡片与 job 状态归一化  

---

## 使用注意

- 勿把密钥写入 packet  
- 小改动不要开多 peer 舰队  
- 可见模式依赖本机终端（默认 Ghostty，可改 `AGENT_RELAY_TERMINAL`）  
- 换设备后请先 `envs` + `workspace`，确认 CLI 与数据目录再 `handoff`  

## License

ISC
