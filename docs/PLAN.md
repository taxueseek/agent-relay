# agent-relay 开发计划

> 状态真源：本文件。每完成一阶段，更新「阶段状态」与「核查记录」。  
> 定位：产品无关的多 Agent 任务接力 skill；L0 证据依赖 session-digger；结构预留插件化。  
> 默认一等 peer：`claude` · `grok` · `zcode`；其余 digger 已登记环境自动探测。

**完成标准（总）**  
1. `relay_cli.py peers|pack|resume|bridge|suggest|doctor|init` 可用且 exit 0  
2. 真实会话：claude↔grok、grok↔zcode（或 zcode 只读 pack）至少各 1 次 pack→resume  
3. 目录可原样升为 Claude/ZCode 插件（含 `.claude-plugin/plugin.json` 与 hooks 占位）  
4. PLAN 核查项全部勾选或标注延期原因  

---

## 0. 架构定案

```
L0 Evidence  session-digger (echolib / sd-recall)
L1 Continuity pack / resume / bridge / CURRENT.md
L2 Delegate   invoke --to <peer>   （本期 stub，下期实现）
L3 Bus        send/poll            （本期 stub）
L4 Surface    SKILL.md + 未来 plugin 皮
```

**任意搭配**：`from_peer` / `to_peer` 开放；不写死 Codex。  
**真源**：磁盘文件 + git；packet 是导航。  
**依赖**：`~/.agents/skills/session-digger`（peer）；无 digger 时降级为 git + 手填 goal。

---

## 1. 目录（skill 即未来插件根）

```
agent-relay/
  docs/PLAN.md
  SKILL.md
  scripts/relay_cli.py
  scripts/core/
  references/packet-schema.md
  templates/
  hooks/hooks.json
  commands/
  .claude-plugin/plugin.json
  tests/
```

运行时：`~/.agents/relay/<slug>/<id>/` + `<project>/.relay/CURRENT.md`

---

## 2. 阶段状态

| 阶段 | 状态 | 日期 |
|------|------|------|
| Phase 0 | **done** | 2026-07-09 |
| Phase 1 | **done** | 2026-07-09 |
| Phase 2 | **done** | 2026-07-09 |
| Phase 3 E2E | **done** | 2026-07-09 |
| Phase 4 L2 invoke | **done**（claude/grok/kimi；zcode 默认关） | 2026-07-09 |
| Phase 4.1 lean/async | **done** | 2026-07-09 |
| Phase 4.2 wave2 六项 | **done** + eval 实测 | 2026-07-09 |
| Phase 4 L3 bus | deferred | — |
| Phase 5 codex-workflows 借鉴 | **done** | 2026-07-09 |

### Phase 5 借鉴清单（scasella/claude-dynamic-workflows-codex）

| 外来概念 | 我们的落地 | 明确不抄 |
|----------|------------|----------|
| GoalLint | `goal-lint` + pack `--lint-goal` + delegate 自动 harden | 多 agent 编译 harness |
| `--plan` dry-run | `plan` 子命令（零 peer token） | agent 数 / token 预算估算舰队 |
| fleet job state | `job-status` → running/completed/stopped/idle + stalled | multi-run dashboard / answer channel |
| harness 模式库 | `patterns.py` + `suggest` 附卡片 | workflow.js DSL |
| trust loop | lint → plan → run → VERIFY | claim_check 全量 harness |
| anti-overbuild | scale quick/standard/deep + 纪律条文 | 20+ agent deep fleet |
| sessionful race / human() GUI | — | 留给 codex-workflows |

安装旁路：`~/.claude/skills/codex-workflows` + symlink `~/.agents/skills/codex-workflows`；`npm run doctor` → `state: ready`。

---

## 3. 核查记录

| 核查项 | 结果 | 备注 |
|--------|------|------|
| P0-1 目录 | PASS | `agent-relay/` |
| P0-2 schema | PASS | unittest validate_packet |
| P0-3 peers | PASS | claude/grok/zcode 均 present=Y |
| P0-4 --help | PASS | 子命令齐全 |
| P1 pack grok | PASS | `20260709-124953-4784` grok→claude |
| P1 pack claude | PASS | `20260709-124953-4cd2` claude→zcode |
| P1 pack zcode | PASS | `zcode://sess_…` → `20260709-124953-358c` |
| P1 resume | PASS | 打印 HANDOFF 注入块 |
| P1 bridge | PASS | `session-digger` → sources=7，多 peer claude+grok |
| P1 doctor | PASS | exit 0，digger 命中 |
| P1 init | PASS | `<project>/.relay/` |
| SKILL 存在 | PASS | `SKILL.md` 已被 Grok 发现 |
| plugin.json | PASS | `.claude-plugin/plugin.json` |
| 单测 | PASS | 7 tests OK |
| E2E 矩阵 | PASS | 见下 |

### E2E 矩阵（真实会话）

| ID | 场景 | 结果 |
|----|------|------|
| E1 | grok session pack → resume | PASS |
| E2 | claude session pack → resume | PASS |
| E3 | bridge session-digger 多 peer | PASS（claude+grok） |
| E4 | zcode pack via zcode:// | PASS |
| E5 | HANDOFF.md 独立可读 | PASS（任意产品 Read 路径即可） |
| E6 | **真实 invoke claude** | PASS：`20260709-125850-8b9e` → 写出 `docs/INVOKE_REVIEW.md`（~399s） |
| E7 | invoke 加固（try 全包/history/add-dir/dry-run） | PASS：单测 + `--cmd-only` |
| E8 | goal-lint / plan / patterns / job digest | PASS：见 Phase 5 单测 |

### 已知限制（诚实记录）

- digger `sd-recall sessions --agent` **不含 zcode**；zcode 经 echolib `zcode_db_*`  
- 多数会话 `files` 抽取为空（无 file snapshot）时，HANDOFF 提示以 git status 为准  
- extract-knowledge 噪声已过滤 tool lesson 长堆栈  
- L2 invoke / L3 bus **未实现**（PLAN Phase 4）  
- PreCompact hook **未写入**用户 Claude settings，仅占位  

---

## 4. 命令面（已冻结 v0.1）

```text
relay peers | doctor | init
relay pack [--from] [--to] [--session] [--goal] [--budget]
relay resume [latest|id]
relay bridge <keyword>
relay suggest [--task]
```

入口：

```bash
python3 agent-relay/scripts/relay_cli.py <cmd>
```

---

## 5. 后续（插件化 / L2）

1. 将本目录拷入或 symlink 到 `~/.agents/plugins/agent-relay` 或 Claude marketplace  
2. 修正 plugin `skills` 布局为 `skills/agent-relay/SKILL.md` 若校验器要求  
3. `invoke --to zcode|claude|grok` 薄封装 CLI  
4. 可选 bus JSONL  

---

## 6. 风险与缓解（已处理）

| 风险 | 状态 |
|------|------|
| packet_id 秒级碰撞 | 已加 `secrets.token_hex(2)` |
| bridge 无绝对路径 | 已解析 `--- [n/m] id (peer,` 行 |
| zcode CLI agent 参数 | 直连 echolib |
