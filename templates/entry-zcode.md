# agent-relay（ZCode 入口片段）

当你被要求「接力」「接着做」「resume」「续跑」或看到 `.relay/CURRENT.md` 时：

1. **定位接力包**
   - 先读项目 `.relay/CURRENT.md`，其中指向 `HANDOFF.md` 路径
   - 如文件不存在，用 `agent-relay resume` 命令打印注入块:
     ```
     python3 ~/.agents/skills/agent-relay/scripts/relay_cli.py resume [packet-id|latest]
     ```
   - 或在任意终端读全局接力目录: `ls -t ~/.agents/relay/<project-slug>/`

2. **理解接力结构**
   - `HANDOFF.md` 包含: 已完成、未决、下一步(next_actions)、需读文件(primary)
   - 文件与 git 是真源，HANDOFF 是导航
   - 背景已在 HANDOFF 中列出，不要向用户复述

3. **执行**
   - 按 next_actions 顺序推进
   - 先 Read primary 列表中的文件，再动手
   - 如有 VERIFY 命令，执行完后手动验证

4. **完成后回包**
   - 运行 pack 交回给来源 peer:
     ```
     python3 ~/.agents/skills/agent-relay/scripts/relay_cli.py pack --from zcode --to <来源peer>
     ```
   - 如来源 peer 是 claude/grok/kimi 且 CLI 可用，可继续:
     ```
     python3 ~/.agents/skills/agent-relay/scripts/relay_cli.py handoff --to <peer> --goal "已完成…并附上新完成标准"
     ```

5. **注意**
   - 不要再问「背景是什么」——所有必要上下文都在 HANDOFF 里
   - 不要在非必要时全仓搜索，优先读 primary 文件
   - 额度将尽时，先 pack 再退出，避免丢失未决项
