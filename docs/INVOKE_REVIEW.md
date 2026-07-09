# INVOKE_REVIEW.md

> 审查范围：`scripts/core/invoke.py`、`scripts/relay_cli.py` 的 invoke / handoff 路径。
> 审查目标：找出阻塞级或高概率故障，给出可执行修改建议，不改业务无关代码。

---

## 问题 1（高）— `invoke_peer` 的 try 包不住函数体，pre-run 异常直接让 CLI 以 traceback 崩溃

**位置**：`invoke.py:173` 起的 try 块。

**现象**：`try:` 从 `subprocess.run` 才开始，但在此之前函数已经做了多种可抛异常的 I/O：
- `build_handoff_prompt`（line 117，调 `render_handoff_md`、读写 packet 字段）
- `out_dir.mkdir`（line 119，权限问题 / 已存在为文件时抛）
- `prompt_file.write_text`（line 124，磁盘满 / 路径非法）
- `add_dirs` 的 `Path.resolve()`（line 132）

这些阶段抛出的异常**不在 try 管辖范围内**，会直接穿透 `invoke_peer` 冒泡到 `cmd_invoke`（relay_cli.py:372），`cmd_invoke` 没包 try，于是 CLI 以原始 traceback 退出。调用方得到的不是结构化的 `InvokeResult(error=...)`，而是一堆栈帧。

`t0 = time.time()` 在 line 172、紧挨 try 之前。如果未来有人把 `t0` 也挪进 try，`except Exception` 分支的 `time.time() - t0` 在当前代码里不会 NameError，但 pre-run 异常路径下 `t0` 未定义同样会二次抛错，值得一起修正。

**建议**：把 try 起点提前到 `peer = peer.lower().strip()` 之后、`t0 = time.time()` 之前（即把整个函数执行体包进去），让**所有路径**都收敛到 `InvokeResult(error=str(e))` 而不是抛异常。这是最小改动、最高收益的加固。

---

## 问题 2（高）— grok 的 `add_dirs` 被丢弃，且 CLI 参数纯属猜测，callee 很大概率读不到 handoff 文件

**位置**：`invoke.py:151-167`（grok 分支）、`invoke.py:129-136`（add_dirs 构造）。

**现象分两层：**

1. **可达性**：claude 分支用 `--add-dir` 把 `~/.agents/relay`、`packet_dir`、`skill_root` 都塞进工作区，保证 callee 能 Read HANDOFF 与 primary 文件。grok 分支却用 `pass` 把 `add_dirs` 整段丢弃（line 163-165），只靠 `--cwd` 指向项目根。但 prompt 里给 callee 的指令是 Read `~/.agents/relay/<slug>/<id>/HANDOFF.md` 与 `~/.agents/skills/agent-relay/...`，都是 cwd 外的绝对路径。如果 grok 的读取沙箱限制在 cwd 内，callee 第一步就会失败 — 接力在第一跳就断。

2. **CLI 参数兼容性**：传给 grok 的参数包括 `--always-approve`、`--permission-mode bypassPermissions`、`--prompt-file`、`--max-turns`。这一组 flag **没有证据**表明 grok CLI 实际支持。陌生 flag 的典型后果是立即 `returncode=2` + stderr 打印 usage，从而被判定为 `INVOKE_FAIL`。这不仅是失败 — 还会让使用者误以为是 packet 问题而不是命令拼写问题。

**建议**：
- 在 `peers.py` 里给每个 peer 加一份**命令行参数白名单**（从 `grok --help`、`claude --help` 实测得到），只发白名单内的 flag；
- grok 路径补一个兜底：把 HANDOFF.md 复制到 `cwd/.relay/HANDOFF.md`，prompt 里优先读 cwd 内的这份；
- 给 `invoke_peer` 加一个 `dry_run: bool = False` 开关与 `build_cmd(...)` 纯函数，dry_run 时只返回 `cmd` 列表不执行，让用户先确认形如 `grok --permission-mode ...` 的命令是不是合法（relay_cli.py 的 `--mode` 旁加一个 `--cmd-only` 即可）。

---

## 问题 3（中）— InvokeResult 没有回写到 packet / CURRENT，是 fire-and-forget，后续接力丢失进度

**位置**：`relay_cli.py:382-389`（`cmd_invoke` 的收尾）、`invoke.py:200-210`。

**现象**：invoke 完成后，`InvokeResult` 只被 `print` 到 stdout，程序就退出了。packet 的 `status` 仍是 `ready_for_handoff`、`done` 仍为空、`CURRENT.md` 指针仍是 invoke 前的状态。三次后果：

- 用户下一次 `pack` 不知道上次 callee（例如 grok）已经做完了哪几步，可能重复劳动；
- `resume latest` 拿到的 HANDOFF 还是旧的，看不到 delegated 进展；
- 想达到「官方协作水平」的跨 agent 连续作业，**没有反馈回路的接力做不成**。

当前代码在 PLAN 里把这条列为 L2 未来工作，但它直接决定 invoke 能不能从「能用」升到「好用」，建议**最小原型级地落地**。

**建议（只动 invoke.py + relay_cli.py，不动 schema）**：
- `cmd_invoke` 返回前，把 `InvokeResult.to_dict()` 追加写入 `packet_dir/invoke/history.jsonl`（每行一次调用记录：peer、exit_code、stdout_path、duration_sec）。这条文件即轻量审计日志，与现有的 `invoke/{peer}-{ts}.meta.json` 目录合并也行；
- 同时把 packet 的 `status` 就地改成 `delegated` 并 `save_packet` 回去，让 `resume` 一眼能分辨「已经在外部处理中」与「本地待办」；
- 若担心写开销，可只在 `result.ok` 时做，失败只写 history。

---

## 附加观察（不阻塞，点一下）

1. **line 213 的括号错位**（掩盖型 bug）：
   ```python
   stdout_path.write_text(e.stdout or "" if isinstance(e.stdout, str) else "", encoding="utf-8")
   ```
   `if-else` 优先级高于 `or`，实际分组为 `e.stdout or ("" if isinstance(e.stdout, str) else "")`。作者本意应是 `(e.stdout or "") if isinstance(e.stdout, str) else ""`。当前因 `text=True` 保证 `e.stdout` 永远是 `str | None`，被掩盖；一旦未来去掉 `text=True`，bytes 输入会让 `write_text` 抛 TypeError。建议改成意图清晰的三元括号写法。

2. **meta.json 的 `cmd[:6]` 会泄漏完整 prompt（含可能的敏感上下文）**：claude 分支 `cmd = [binary, "-p", prompt, ...]`，cmd[2] 就是完整 prompt，`cmd[:6]` 把整个 prompt 落盘。按 AGENTS.md「勿把密钥写入 packet」的精神，meta.json 也应避免存原文，建议把 cmd 显示用摘要化：只保留 flag 与文件路径，prompt 用哈希或长度代替。

3. **600s + capture_output 阻塞父进程**：同步 `subprocess.run` 让父 agent 干等最多 10 分钟，同时 stdout/stderr 全量缓冲在内存。对当前「单跳接力」可接受；要做多跳协同或并行 fan-out，应切到 `subprocess.Popen` + 轮询。放在后续。

---

## 未决

- grok / claude 的实际 CLI flag 白名单未实测（建议作为独立验证跑 `claude --help` 与 `grok --help` 后落到 peers.py 白名单）；
- fire-and-forget 是否需要升级为 bus 模式，取决于是否在近期把「多跳协同」排期；当前仅建议最小回写方案。
