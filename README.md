# agent-relay

> 跨产品 Agent 任务接力与委派。用科学的方式来交接任务和上下文，而不是粘贴整段聊天记录。

额度用完、上下文压扁、换工具继续时，把「目标 + 关键文件 + 下一步」打成可验收的数据，交给另一个 Agent 续跑或委派执行。灵感来自 [session-digger](https://github.com/taxueseek/session-digger) 的跨环境会话分析，本技能更进一步。可以进行跨agent的任务接力跟调用其他的agent来打下手。


---

## 与 session-digger 的关系

| | [session-digger](https://github.com/taxueseek/session-digger) | **agent-relay** |
|--|--|--|
| 解决什么 | 历史会话怎么挖、怎么搜、怎么沉淀 | 任务怎么跨产品接上、怎么委派、怎么验收 |
| 典型动作 | `/recall`、索引、趋势 | `pack` / `resume` / `handoff` / `delegate` |
| 产物 | 可搜索索引与记忆 | `packet.json` + `HANDOFF.md` + `result.json` |

两者叠用：有 digger 时 pack 会拉会话证据；没有 digger 时降级为 git 状态 + 手填 goal，仍可交接。

---

## 如何安装

### 通用安装（推荐）

```bash
# 方式一：npx 一键安装
npx -y skills add taxueseek/agent-relay -g --all

# 方式二：git clone 到 skills 目录
git clone https://github.com/taxueseek/agent-relay.git ~/.agents/skills/agent-relay
```

可选依赖（增强 pack 证据）：

```bash
# 安装 session-digger 后，relay 会自动探测
npx -y skills add taxueseek/session-digger -g --all
# 或指定路径
export SESSION_DIGGER_ROOT=~/.agents/skills/session-digger
```

### Claude Code 插件安装

```bash
git clone https://github.com/taxueseek/agent-relay.git ~/.claude/plugins/agent-relay
```

装完可用 skill 触发词：接力、handoff、delegate、resume、pack、goal-lint 等。

### 自检

```bash
RELAY=~/.agents/skills/agent-relay/scripts/relay_cli.py
# clone 到其他路径时改 RELAY
python3 "$RELAY" doctor
python3 "$RELAY" peers
```

---

## 能做什么

- **证据驱动交接** — 从 session-digger / git 组装瘦包，不 dump 全文 transcript
- **pack / resume** — 额度切换、压缩后续跑；resume 校验 mtime/git 漂移（STALE）
- **goal-lint / plan** — 先把目标硬化、干跑路由与风险，再花 peer token
- **invoke / handoff / delegate** — 真调 Claude Code、Grok Build、Kimi Code、MiMo 等
- **bridge / suggest** — 跨环境对齐进度；推荐 peer 与协作模式卡片
- **VERIFY + result.json** — goal 可带可脚本验收命令，委派结束一屏回执
- **可见终端** — `--visible` 结束后在 Ghostty / Terminal 中 resume 会话

---

## 快速开始

```bash
RELAY=~/.agents/skills/agent-relay/scripts/relay_cli.py

# 1. 环境是否就绪
python3 "$RELAY" doctor
python3 "$RELAY" peers

# 2. 模糊目标先硬化
python3 "$RELAY" goal-lint --goal "修登录超时 VERIFY: pytest -q tests/test_auth.py"

# 3. 零 token 干跑路由
python3 "$RELAY" plan --task "修登录超时" --to claude --action delegate

# 4. 打包当前会话（额度快没了 / 准备换工具）
python3 "$RELAY" pack --from auto --goal "继续修登录超时"

# 5. 在另一产品里续跑
python3 "$RELAY" resume latest

# 6. 委派子代理执行（建议 goal 带 VERIFY）
python3 "$RELAY" delegate --to claude \
  --task "写 tests/test_relay_smoke.py 含 test_ok。VERIFY: test -f tests/test_relay_smoke.py && rg -q test_ok tests/test_relay_smoke.py"

# 7. 查 job 状态
python3 "$RELAY" job-status --packet latest
```

信任闭环（推荐）：

```
goal-lint → plan → pack/delegate → job-status / VERIFY
```

---

## 命令参考

| 命令 | 用途 |
|------|------|
| `peers` | 列出可探测 peer 与能力（证据 / pack / 委派） |
| `doctor` | 环境与依赖自检 |
| `init` | 初始化项目 `.relay/` 指针 |
| `goal-lint` | 硬化目标：VERIFY、sandbox、规模、失败条件 |
| `plan` | 干跑路由与风险，不花 peer token |
| `pack` | 打证据包；可选 `--lint-goal`、`--deep` |
| `resume` | 加载 packet / HANDOFF，注入后先读 primary 文件 |
| `bridge` | 按关键词跨环境对齐进度 |
| `suggest` | 推荐 peer + harness 模式卡片 |
| `invoke` | 直接调用另一产品继续（`--wait` / `--visible`） |
| `handoff` | pack + invoke 一键交接 |
| `delegate` | 主管式委派：implement / review / fix |
| `job-status` | 查询异步 job：running / completed / stopped / idle |

### 常见场景

| 你想… | 命令 |
|--------|------|
| 目标太糊 | `goal-lint --goal "…"` |
| 先估风险再花钱 | `plan --task "…"` |
| 额度没了 / 换环境 | `pack` → 另一边 `resume latest` |
| 交给子代理干活 | `delegate --to <peer> --task "… VERIFY: …"` |
| 打开可见窗口续聊 | `handoff --to grok --visible --goal "…"` |
| 两边各说各话 | `bridge <关键词>` |

---

## 支持的 peer

| Peer | 证据 pack | 委派 invoke | 说明 |
|------|-----------|-------------|------|
| Claude Code | ✓ | ✓ | 一等 peer |
| Grok Build | ✓ | ✓ | 一等 peer |
| Kimi Code | ✓ | ✓ | 有 CLI 即可 |
| MiMo | ✓ | ✓ | `mimo run`；模型见 `AGENT_RELAY_MIMO_MODEL` |
| Codex | ✓ | ✓* | 探测到 CLI 时可用 |
| ZCode | ✓ | 默认关 | 仅 pack/resume；实验：`AGENT_RELAY_ENABLE_ZCODE_INVOKE=1` |

\* 复杂 Codex 舰队 / sessionful race 请用专门的 codex 编排工具，本 skill 不做第二套编排层。

---

## 产物结构

```
~/.agents/relay/<project-slug>/<packet-id>/
  packet.json      # 结构化瘦包
  HANDOFF.md       # 人/机可读交接
  sources.json     # 证据来源
  result.json      # 委派回执（若有）
  invoke/          # 调用日志与 job 状态

<project>/.relay/CURRENT.md   # 当前 packet 指针
```

Schema：`agent-relay/v1`。packet 默认短预算（goal + next + 有限 files/decisions），避免 token 膨胀。

---

## 环境变量（可选）

| 变量 | 作用 |
|------|------|
| `SESSION_DIGGER_ROOT` / `SD_ROOT` | session-digger 安装路径 |
| `AGENT_RELAY_HOME` | packet 存储根，默认 `~/.agents/relay` |
| `AGENT_RELAY_TERMINAL` | 可见终端：`Ghostty` / `Terminal` 等 |
| `AGENT_RELAY_MIMO_MODEL` | MiMo 模型名 |
| `AGENT_RELAY_ENABLE_ZCODE_INVOKE` | 实验性打开 ZCode invoke |

---

## 模式卡片（`suggest` 会提示）

| id | 防什么 |
|----|--------|
| `trust_loop` | 糊目标 + 无验收 |
| `fresh_context_review` | 作者审自己 |
| `quota_handoff` | 上下文丢失 |
| `cross_peer_align` | 多端各说各话 |
| `anti_overbuild` | 小任务开舰队 |

---

## 开发与测试

```bash
cd agent-relay
python3 -m pytest tests/ -q
# 或
python3 tests/test_packet_and_peers.py
```

更多：`docs/PLAN.md`（架构与阶段）、`references/packet-schema.md`（包字段）、`SKILL.md`（Agent 触发与纪律）。

---

## 版本

### v0.1.2

- 信任闭环：`goal-lint` → `plan` → pack/delegate → `job-status` / VERIFY
- L2 真调：claude / grok / kimi_code / mimo；async + lean 默认
- 模式卡片、`job-status` 归一化状态
- 对接 session-digger 证据层；无 digger 可降级

---

## DO NOT

- 不替代 session-digger 的会话分析
- 不做在线多 Agent 消息总线（L3 延期）
- 不把密钥写入 packet
- 两行 typo 不要开多 peer 舰队

## License

ISC
