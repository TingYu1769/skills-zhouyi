# 在不同宿主中使用

周译只用文件读写、命令执行和看图三种能力，能在 Claude Code、Codex 等 agent 中运行。各宿主的差异集中在四处：用户的选择记在哪里，怎么联网，怎么看图，有没有子代理。下表依据各家官方文档，查证于 2026-10，宿主更新后以其文档为准。

| | 记住用户的选择 | 联网下载 | 看图 | 子代理 |
|---|---|---|---|---|
| Claude Code | 写入项目记忆目录 `~/.claude/projects/<项目>/memory/`，后续会话自动加载 `MEMORY.md` 的开头部分；跨项目的个人偏好写 `~/.claude/CLAUDE.md` | Bash、WebFetch 分别受权限设置控制；开启沙箱后命令联网走域名代理 | `Read` 直接读 PNG、JPEG | `Agent` 工具，可嵌套 |
| Codex CLI | 原生记忆默认关闭；可写用户级 `$CODEX_HOME/AGENTS.md`，启动时自动读取（`AGENTS.override.md` 优先） | `workspace-write` 沙箱默认不联网，配置 `sandbox_workspace_write.network_access = true` 后可用；`--search` 只开网页搜索，不给命令联网 | `view_image`，需模型支持图片输入 | Subagents，用 `/subagents` 查看 |
| Cursor | 项目规则 `.cursor/rules/*.mdc`，设 `alwaysApply: true` 后自动带入 | 按其终端权限 | 按其文件工具 | — |
| Gemini CLI | 项目 `GEMINI.md` 或全局 `~/.gemini/GEMINI.md`，后续会话自动加载 | 按其 shell 权限 | 按其文件工具 | — |

记偏好时只写一两行，例如“周译：输出根目录 `reading/`；超出估算 1.5 倍时停下；不用子代理；不打 ZIP”。宿主没有可用的记忆机制，或写入需要用户授权而用户没有授权时，不写，开场时按默认值办或询问。

## 用量

几家宿主都能让用户查看用量，但没有让 agent 在会话中读到自身累计用量的通用接口：Claude Code 用 `/usage`，Codex 用 `/status`，Gemini CLI 用 `/stats model`。周译的花费控制因此按耗时折算（见 [开场与默认](onboarding.md)），需要金额时请用户用这些命令查看。

## 模型分工

- 默认用当前会话的模型，一篇论文一个会话，不用子代理。
- 宿主允许选模型时，正文翻译可用较便宜的模型；两道关卡的判断、疑难段落和最终抽查用强模型。
- 长论文超出一个会话能承载的正文量时，在块的边界停下，开新会话续作，比开子代理便宜：子代理每次都要重付系统提示和规范的开销。

各模型在花费和质量上的实测对比，待补充。

## 出处

- Claude Code：[记忆](https://code.claude.com/docs/en/memory)、[权限](https://code.claude.com/docs/en/permissions)、[沙箱](https://code.claude.com/docs/en/sandboxing)、[用量](https://code.claude.com/docs/en/costs)、[子代理](https://code.claude.com/docs/en/sub-agents)
- Codex：[记忆](https://learn.chatgpt.com/docs/customization/memories)、[AGENTS.md](https://learn.chatgpt.com/docs/agent-configuration/agents-md)、[联网与审批](https://learn.chatgpt.com/docs/agent-approvals-security)、[图片输入](https://learn.chatgpt.com/docs/image-inputs)、[子代理](https://learn.chatgpt.com/docs/agent-configuration/subagents)
- Cursor：[规则](https://cursor.com/docs/rules)
- Gemini CLI：[记忆文件](https://geminicli.com/docs/tools/memory/)、[命令](https://geminicli.com/docs/reference/commands/#stats)
