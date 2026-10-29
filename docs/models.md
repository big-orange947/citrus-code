# 模型配置与供应商

CLI 使用 JSON 文件配置供应商、模型、接口地址和密钥，无需设置环境变量。
首次运行：

```powershell
citrus --init-config
notepad "$HOME\.citrus-code\settings.json"
```

填写 `api_key` 后运行 `citrus`。初始化仅创建示例，不覆盖已有文件。
Windows 默认全局路径为 `C:\Users\你的用户名\.citrus-code\settings.json`；
macOS/Linux 为 `~/.citrus-code/settings.json`。

## 配置层级

默认先读取用户目录的 `.citrus-code/settings.json`，再读取工作目录的
`.citrus-code/settings.json`。项目同名供应商按字段覆盖，未指定字段沿用全局值；
`models` 数组整体替换，`headers`、`extra_body` 对象也整体替换。
`default_model` 由项目配置覆盖。

也可只使用指定文件，不读取全局或项目默认文件：

```powershell
citrus --config "D:\config\citrus-settings.json"
citrus --config "D:\config\citrus-settings.json" --init-config
```

仓库的 [settings.example.json](../settings.example.json) 包含 DeepSeek、中转站和 Anthropic 的完整示例。
其中 `your-model-id`、`your-claude-model-id` 和示例中转地址必须替换成服务实际提供的值。
模型列表由用户声明，本版不自动从服务端查询。

## 最小 DeepSeek 配置

```json
{
  "default_model": "deepseek/deepseek-flash",
  "providers": {
    "deepseek": {
      "api": "chat-completions",
      "base_url": "https://api.deepseek.com",
      "api_key": "在本机填写你的密钥",
      "extra_body": {"thinking": {"type": "enabled"}, "reasoning_effort": "max"},
      "models": [
        {"id": "deepseek-flash", "name": "DeepSeek V4.1 Flash", "context_window": 1000000, "max_output_tokens": 32768, "supports_images": true}
      ]
    }
  }
}
```

`providers` 下的键是本地供应商 ID，例如 `deepseek`、`openai`、`relay`。
模型引用由 `供应商ID/模型ID` 组成。模型 ID 本身可以含 `/`，例如
`relay/vendor/model-name`，其中 `relay` 是本地供应商 ID。

## 协议与中转站

| `api` | 处理方式 | 默认认证 |
| --- | --- | --- |
| `chat-completions` | OpenAI Chat Completions 的文字、图片、工具调用和 SSE | Bearer Token |
| `anthropic-messages` | Anthropic Messages 的文字、图片、工具调用和 SSE | `x-api-key` |

供应商名称不决定协议。DeepSeek、Qwen、OpenRouter，以及提供兼容接口的其他服务或中转站，
均可选择 `chat-completions`；提供 Anthropic 原生格式的中转站选择 `anthropic-messages`。
兼容程度仍取决于服务是否支持工具调用、流式事件和所填模型的能力。

OpenAI 兼容接口的 `base_url` 应包含服务要求的前缀，例如
`https://api.openai.com/v1`、`https://your-relay.example/v1`。
程序在末尾追加 `/chat/completions`；已经填写完整端点时不重复追加。
DeepSeek 官方接口也可以使用 `https://api.deepseek.com`。

Anthropic 的 `base_url` 可以填 `https://api.anthropic.com`，程序追加 `/v1/messages`；
以 `/v1` 结尾时仅追加 `/messages`，已经填写完整端点时不重复追加。
地址不支持携带查询参数；认证信息通过密钥或请求头提供。

中转站的可选字段：

```json
{
  "api": "chat-completions",
  "base_url": "https://your-relay.example/v1",
  "api_key": "在本机填写中转站密钥",
  "auth": "bearer",
  "headers": {"X-Custom-Header": "服务要求的值"},
  "include_usage": false,
  "max_tokens_field": "max_completion_tokens",
  "extra_body": {"reasoning_effort": "low"},
  "models": [{"id": "your-model-id", "context_window": 128000, "max_output_tokens": 8192}]
}
```

- `auth` 可选 `bearer`、`x-api-key` 或 `none`；省略时按协议选择。无需认证的本地服务可选 `none`。
- `headers` 可以覆盖默认请求头；自定义 `Authorization` 时可以省略 `api_key`。
- `include_usage` 默认为 `true`；不接受 `stream_options` 的 OpenAI 兼容服务可设为 `false`。
- `max_tokens_field` 仅用于 OpenAI 兼容接口，默认 `max_tokens`；部分模型要求 `max_completion_tokens`。
- `extra_body` 为请求额外参数。它不能覆盖模型路由、对话、系统提示、工具声明和流式开关。
  不同模型接受的参数不同，示例中的 `reasoning_effort` 不是所有服务都支持。

配置中每个模型可填写显示名称 `name`、`supports_images`、`context_window` 和
`max_output_tokens`。未声明图片能力时不会发送图片。默认窗口为 1M，默认输出上限为 32768；
请按实际模型填写，程序不会把这些声明当作服务端能力验证。
CLI 输出预算取命令行预算与模型声明上限的较小值。
状态栏显示模型的 `name`；未填写时显示接口 ID。供应商返回缓存命中数据时，
状态栏显示上次模型请求的缓存命中 token 占输入 token 的比例；没有数据时不显示。

当前自动压缩在配置窗口的 60% 触发，保留完整工具交互；1M 模型沿用原来的 600000 阈值。
近期交互软预算为窗口的约 10%，最多 100000 tokens；摘要请求也按模型窗口限制大小。

## 在 CLI 中选择

使用 DeepSeek 官方 Chat Completions 的 `deepseek-flash` 或 `deepseek-v4-pro` 时，
思考模式默认开启，强度为 `max`。输入 `/effort` 在列表中选择 `low`、`high` 或 `max`，
也可以直接输入 `/effort low`。状态栏以不同颜色显示当前强度。设置仅在当前进程生效，
重启后恢复为 `max`；任务运行时不能切换。其他供应商暂不自动启用此设置。
这两款官方模型的思考设置由 `/effort` 控制，会覆盖供应商 `extra_body` 中的相关字段。

```text
/model
/model relay/your-model-id
/model reload
```

输入 `/model ` 会展开配置中的模型列表，可以输入文字筛选、用 ↑↓ 或 Tab 选择，按 Enter 切换。
`/model reload` 重新读取修改后的配置并展开列表；直接输入完整模型引用也会重新读取配置。
菜单会标明未配置密钥的供应商；真正切换前会检查认证配置。

切换只能在任务结束且没有排队消息时进行。运行中的任务可先 `/abort`，尚未接纳的消息可用
`/clear-queue` 清空。切换会保留当前对话、工具和项目约定，更新模型身份提示与压缩状态，
并追加一条模型选择记录；下一次恢复会话沿用最后记录的模型。
密钥和认证请求头不写入会话文件，运行时重新从本机配置取得。
`/model` 不改写配置中的 `default_model`，新会话仍使用文件指定的默认值。

启动时也可以直接选择：

```powershell
citrus --model relay/your-model-id
citrus --provider relay --model your-model-id
```

恢复会话时先加载原模型，进入 CLI 后再切换。不同模型的旧分支仍须先用 `/model` 选择
匹配模型，再执行 `/checkout`；本版不在分支切换时自动选择供应商。

## 当前边界

- 旧 DeepSeek 环境变量只在完全没有配置文件时作为可选回退；配置文件存在时以文件为准。
  原有 DeepSeek 学习示例通过 `config.py` 读取文件中的 `deepseek` 供应商。
- 提供 API Key 或中转站认证，不实现 Claude/OpenAI 订阅账号登录、OAuth、Gemini 原生或 Responses 协议。
- Anthropic 目前处理普通文字、图片和本地工具调用；不接收 thinking、服务器工具等特殊内容块。
  请不要在该供应商的 `extra_body` 中开启这些特性。
- 模型列表、连接参数和密钥在本机配置，文件中的密钥为明文；不要把实际配置提交到仓库。
  默认项目 `.citrus-code/` 已被 Git 忽略。配置错误提示不打印原始文件内容。
- 本次实现仅完成语法和静态检查，尚未调用真实供应商或中转站验证。

协议参考：[OpenAI 流式事件](https://developers.openai.com/api/reference/resources/chat/subresources/completions/streaming-events)、
[Anthropic 流式事件](https://platform.claude.com/docs/en/build-with-claude/streaming)。
