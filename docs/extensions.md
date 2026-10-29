# Skill 与 MCP

Citrus Code 可以发现本地 Agent Skills，也可以把 MCP 服务提供的工具接入现有 Agent。管理命令在交互界面中使用；扩展偏好按项目保存到工作目录的 `.citrus-code/extensions.json`。

## Skill

按以下顺序扫描，重名时保留先发现的 Skill，并在启动时提示：

1. `<工作目录>/.citrus-code/skills/`
2. `<工作目录>/.agents/skills/`
3. `~/.citrus-code/skills/`
4. `~/.agents/skills/`
5. 用户通过 `/skill add` 添加的目录或 `SKILL.md`

目录下可以有子目录，每个 Skill 使用 `SKILL.md`：

```text
.citrus-code/skills/review-code/SKILL.md
```

```markdown
---
name: review-code
description: 检查代码变更并指出可以采取行动的问题。
---

先阅读项目约定和相关代码，再报告发现的问题。
```

`name` 使用小写字母、数字和连字符；`description` 是非空文本。启动时只把可用 Skill 的名称、说明和文件路径加入模型请求；模型可按需调用 `read` 读取完整文件。Skill 中的相对路径以该 `SKILL.md` 所在目录为准。设置 `disable-model-invocation: true` 会让它不出现在自动发现列表中，仍可通过 `@名称` 显式选择。

交互界面输入 `@` 或名称前缀，选择候选后，输入例如 `请按 @review-code 检查这个改动`。提交时程序读取对应文件，并将完整内容附加到用户消息；显示的输入仍是原文。完整指令随消息保存在会话文件中。未匹配的 `@文本` 按普通文字处理。

```text
/skill
/skill reload
/skill disable review-code
/skill enable review-code
/skill add "C:\Users\你的用户名\.codex\skills"
/skill remove "C:\Users\你的用户名\.codex\skills"
```

`/skill` 展示名称、状态、说明、路径与发现时的错误；添加或修改文件后使用 `/skill reload`。`add` 注册现有目录或单个 `SKILL.md`，`remove` 只移除注册，不删除文件；可借此加载其他工具的 Skill 目录，但其专用工具或插件不一定存在于 Citrus Code。Skill 可指示模型运行命令或读取文件，使用前应了解其内容。

## MCP

MCP 使用官方 Python SDK。Python 3.10 及以上安装 SDK；Python 3.9 仍可使用其他功能，但不能连接 MCP 服务。

配置文件依次为 `~/.citrus-code/mcp.json` 和 `<工作目录>/.citrus-code/mcp.json`；项目中的同名服务覆盖全局配置。格式示例：

```json
{
  "mcpServers": {
    "local-example": {
      "command": "python",
      "args": ["D:/tools/my_mcp_server.py"],
      "env": {"EXAMPLE_SETTING": "value"}
    },
    "remote-example": {
      "type": "http",
      "url": "https://example.com/mcp",
      "headers": {"Authorization": "Bearer 替换为自己的凭据"}
    }
  }
}
```

本地服务通过 stdio 启动，`command` 和 `args` 分开填写，不通过 shell 解析；远程服务使用 Streamable HTTP。支持配置 `enabled: false`。服务的环境变量和请求头可包含凭据，请不要提交配置文件或将凭据发到聊天中。

启动及 `/mcp reload` 时连接启用的服务、读取工具声明。服务连接在当前 CLI 运行期间保持，多个调用复用同一连接；重新加载、禁用或退出时关闭连接。工具名称映射为 `mcp_<服务名>_<工具名>`，其参数 Schema 由服务提供，并通过原有工具运行器执行。

```text
/mcp
/mcp config
/mcp reload
/mcp disable local-example
/mcp enable local-example
/mcp add remote-example https://example.com/mcp
/mcp remove remote-example
```

`/mcp` 显示服务状态和工具名。`add` 添加项目级 Streamable HTTP 地址；stdio 服务、请求头和环境变量通过配置文件填写，再执行 `/mcp reload`。`remove` 只移除项目配置中的条目；如果全局文件有同名配置，移除后会重新显示全局服务。启用、禁用和重新加载需要任务结束且没有排队消息。如果配置本身写了 `enabled: false`，需先编辑配置再启用。

工具调用超时为 120 秒；中断或超时不保证远端操作没有副作用。断开后可用 `/mcp reload` 重新连接。连接失败时显示错误类型，配置内容和认证头不会显示在状态列表中。`--tools read-only` 下不连接 MCP，因为服务可能提供写入能力。

当前只将 MCP **工具**接入模型；资源列表、提示模板、采样、交互式授权和旧版 SSE 传输尚未接入。工具返回的文本、图片、内嵌文本资源及资源链接可转换；其他内容显示占位说明。较长文本会被截断。连接远程服务或运行本地服务时，该服务具有其自身的执行权限。
