# citrus-code

**带初学者用 Python，一步步重写一个 mini 版 pi coding agent。**

> 项目原名 mini-pi，现更名为 citrus-code。仓库名和克隆目录已经变更；当前 Python 包名（`src/mini_pi/`）、命令行 `mini-pi`、`MINI_PI_*` 环境变量和 `.mini-pi/` 会话目录暂未改名，文中命令与路径以实际代码为准。

这是一个持续实现中的学习项目，参考 [pi](https://github.com/earendil-works/pi) 的核心设计思路，用 Python 理解模型调用、流式事件、工具执行和 Agent 运行时如何协作。

适合有 Java 或其他语言基础、正在学习 Python，同时对 Agent 开发有一些了解的读者。项目不使用 LangChain、LangGraph 等 Agent 框架；网络通信和数据校验分别使用 HTTPX 与 Pydantic，其余流程逐步实现。

> 当前已实现模型调用层、ReAct 主循环、Agent 状态、四个 Hooks、参考 pi 的八个内置工具、会话保存与恢复、带 Markdown 的 CLI、AGENTS.md 加载、本地图片读取，以及按 1M 窗口、60% 阈值实现的自动上下文压缩。基础 CLI 已手动运行；新版界面、图片接入和压缩流程仍需进一步交互与真实模型验证。

## 学习目标与设计思路

- **模型接入与 Agent 运行时分开。** Provider 处理具体服务的协议，运行时使用统一的消息和事件。
- **以简单的工具调用循环为核心。** 模型提出动作，程序执行工具，把结果放回上下文，再让模型继续判断。
- **流式生成不等于工具执行。** 工具参数可以分段到达；完整接收并解析后，才能进入执行阶段。
- **工具声明和实际校验使用同一个来源。** Pydantic 参数模型既生成 JSON Schema，也校验工具收到的实际参数。
- **逐步补齐工程能力。** 在核心流程清楚的基础上，再加入状态管理、Hooks、CLI、会话保存和上下文管理。

本项目是学习实现，并非 pi 的官方 Python 移植版，也不承诺与其接口和功能完全一致。

## 当前进度

| 模块 | 当前能力 |
| --- | --- |
| 消息类型 | 系统、用户、助手、工具结果消息；文字块、图片块与工具调用块 |
| Provider 接口 | 统一模型信息和异步事件流，提供注册与查找 |
| FakeProvider | 使用预设文字模拟流式响应，便于理解异步生成器 |
| DeepSeek 接入 | HTTP 请求、SSE 读取、非思考模式、Flash 图片输入、用量统计和错误事件 |
| 工具调用解析 | 按索引拼接多个工具调用的参数，转换成统一工具事件 |
| 生成截断处理 | 默认输出上限 32,768 tokens；回复被截断时不执行该回复中的工具调用 |
| 完整回答接口 | `complete()` 消费事件流并返回最终助手消息 |
| 工具接口 | 区分模型可见的工具定义与 Python 执行实现 |
| Agent 运行时 | 模型与工具循环、连续对话状态、流式事件、可选轮数限制 |
| Hooks | 模型调用前后、工具执行前后；支持拦截工具与处理结果 |
| coding 工具 | read、write、edit、bash、powershell、grep、find、ls |
| 输出控制 | read/命令输出按 2000 行或 50KB 截断；搜索工具提供数量和字节限制 |
| 工具运行器 | 按名称查找工具，将执行结果或异常转换成工具结果消息 |
| 会话存储 | JSONL 追加保存、加载校验、消息树与分支选择、当前分支上下文重建 |
| CodingSession | 按事件保存消息、安全切换分支、取消与排队消息入口 |
| CLI | 交互与单次任务、流式输出、运行中追加消息、会话恢复、末尾缺失工具结果修复 |
| 终端展示 | 原创字符 π 标识、欢迎面板、多行输入、状态栏、Markdown 流式预览、代码高亮和工具结果卡片 |
| 项目指令 | 启动时读取工作目录下的 AGENTS.md，通过模型前 Hook 组装当前请求 |
| 上下文压缩 | 估计达到 600,000 tokens 时生成摘要，保留当前要求及近期完整交互，保存压缩记录 |

工具名称、主要参数和默认组合参考 pi。实现细节、系统程序依赖、图片能力和取消处理的边界见 [工具说明](docs/tools.md)。

会话存储的接口、文件格式和恢复边界见 [会话说明](docs/sessions.md)。带自动保存的任务通过 `CodingSession.prompt()` 运行。命令入口见 [CLI 使用说明](docs/cli.md)。

自动压缩策略与失败边界见 [上下文压缩说明](docs/compaction.md)。当前固定假设模型支持 1,000,000 tokens；估计达到 60% 时压缩。摘要调用会消耗模型额度。

## 运行 CLI

在仓库根目录运行：

```powershell
uv sync --locked

# 离线交互，使用预设文字，不需要 API Key
uv run mini-pi --fake

# 设置下文的 DeepSeek 环境变量后，进入真实模型交互
uv run mini-pi

# 单次任务
uv run mini-pi "读取 pyproject.toml，说明项目依赖"

# 列出保存的会话；恢复最近会话
uv run mini-pi --sessions
uv run mini-pi --resume
```

默认会话保存在工作目录的 `.mini-pi/sessions/`。交互时使用 `/help` 查看命令；运行期间可继续输入引导消息，Ctrl+C 请求取消。Windows 默认提供 PowerShell，可用 `--shell bash` 为新会话选择 Git Bash。离线模型只生成固定文字，不能完成编程任务。

输入框中 Enter 发送，Alt+Enter 或 Ctrl+J 换行，Tab 补全命令。助手回答支持 Markdown；较长的工具结果默认只展示预览，使用 `--verbose` 启动可查看完整工具返回。升级依赖前先退出正在运行的旧 CLI，避免 Windows 锁住启动程序。

## 在任意项目中启动

安装为用户级命令，开发期间采用可编辑模式：

```powershell
uv tool install --editable "D:\project\citrus-code"

# 在目标项目中运行，默认以当前目录作为工作目录
cd D:\project\poirot
mini-pi

# 或从任意目录指定工作目录
mini-pi --cwd "D:\project\poirot"
```

安装后无需激活 `.venv`。源码修改后重启命令即可；新增依赖时先退出 mini-pi，再重新安装工具。全局安装和指定目录的详细说明见 [CLI 文档](docs/cli.md#全局命令与工作目录)。

上面的源码路径请替换为自己的克隆目录。如果安装后找不到命令，可执行 `uv tool update-shell`，然后重新打开终端。全局工具使用独立环境；需要严格使用项目的 `uv.lock` 时，在源码仓库中执行 `uv sync --locked` 和 `uv run mini-pi`。

## 读取本地图片

CLI 为 `deepseek-flash`、`deepseek-v4-flash` 和 `deepseek-v4-flash-vision-exp` 启用图片输入。启动后提供图片路径，并让模型调用 `read`：

```text
请调用 read 读取 D:\screenshots\page.png，先描述图片内容，再分析布局问题。
```

`read` 成功读取后才会把图片作为附件交给模型，单独输入路径不等于发送图片。当前 DeepSeek 接入允许 PNG、JPEG、GIF 和 WebP；其他格式需先转换，文件大小和尺寸仍受服务限制。第三方转发服务也需要支持相应的图片协议。

当前不支持直接粘贴剪贴板位图、自动截图、缩放或转码。SVG 和 HTML 按源码读取；要分析渲染效果，请先生成截图，再提供截图路径。详见 [图片输入说明](docs/cli.md#读取本地图片)。

## 生成较大文件

1M 上下文窗口与单次输出上限是两个限制。`write` 的文件正文、`edit` 的替换内容也占用模型输出预算。CLI 默认每次最多输出 32,768 tokens，可在模型服务允许的范围内调整：

```powershell
mini-pi --cwd "D:\project\poirot" --max-output-tokens 65536

# 恢复会话时也可调整
mini-pi --cwd "D:\project\poirot" --resume latest --max-output-tokens 65536
```

模型以 `length` 结束时，界面显示“回复被截断”。该回复中的整批工具调用都不会执行，历史中记录截断诊断；此前已完成的调用不受影响。程序不自动补全残缺 JSON 或重复请求。可以继续会话，让模型先创建文件骨架，再分批编辑。详见 [截断处理](docs/cli.md#生成较大文件与回复截断)。

## 项目结构

```text
citrus-code/
├── src/mini_pi/
│   ├── config.py                 # 从环境变量加载连接配置
│   ├── __main__.py               # python -m mini_pi 入口
│   ├── cli/                      # 参数、组装、终端输入和事件展示
│   │   ├── main.py               # 命令参数、应用组装和交互命令
│   │   ├── terminal.py           # 异步输入、按键和状态栏
│   │   ├── render.py             # Markdown、工具卡片和事件展示
│   │   ├── preview.py            # 输入框上方的流式 Markdown 预览
│   │   └── theme.py              # 字符标识、配色和终端文字清理
│   ├── ai/                       # 模型调用层
│   │   ├── types.py              # 内部消息、模型、上下文和工具定义
│   │   ├── events.py             # 统一模型事件
│   │   ├── provider.py           # Provider 接口
│   │   ├── registry.py           # Provider 注册与查找
│   │   ├── completion.py         # 等待完整模型响应
│   │   └── providers/
│   │       ├── fake.py
│   │       ├── deepseek.py
│   │       ├── chat_completions.py
│   │       ├── chat_stream_types.py
│   │       └── tool_call_buffer.py
│   ├── agent/                    # 通用工具执行与运行时基础
│   │   ├── agent.py              # 连续对话和状态维护
│   │   ├── loop.py               # 模型与工具循环
│   │   ├── events.py
│   │   ├── state.py
│   │   ├── hooks.py
│   │   ├── context.py            # 模型调用前的通用上下文准备接口
│   │   ├── types.py
│   │   └── tool_runner.py
│   └── coding/
│       ├── coding_session.py     # 运行与持久化的应用层连接
│       ├── history.py            # 工具调用与结果配对检查
│       ├── project_context.py    # 读取工作目录下的 AGENTS.md
│       ├── context_hooks.py      # 在模型请求中应用当前系统提示
│       ├── compaction/           # token 预算、近期交互选择和摘要生成
│       ├── session/              # 会话树、JSONL 存储和上下文重建
│       │   ├── types.py
│       │   ├── manager.py
│       │   ├── storage.py
│       │   ├── projection.py     # 根据压缩记录重建模型上下文
│       │   └── recovery.py       # 为缺失结果补充执行状态未知记录
│       └── tools/                # 具体 coding 工具
│           ├── factory.py        # 默认、只读和全部工具组合
│           ├── read.py / write.py
│           ├── edit.py / edit_text.py
│           ├── bash.py / powershell.py / shell.py
│           ├── grep.py / find.py / ls.py
│           └── truncate.py / paths.py / mutation.py / processes.py
├── docs/                         # CLI、工具、会话和上下文压缩说明
├── examples/                     # 各学习阶段的独立演示
│   └── prototype/                # 早期原型存档，供对照阅读
├── pyproject.toml
└── uv.lock
```

本地实验目录 `demo/`、会话目录 `.mini-pi/`、虚拟环境和 `.env` 均在 Git 忽略规则中，不随项目提交。

## 环境准备

需要 Python 3.12 或更新版本，以及 [uv](https://docs.astral.sh/uv/)。以下命令从仓库根目录执行。

```powershell
git clone https://github.com/big-orange947/citrus-code.git
cd citrus-code
uv sync --locked
```

`uv sync --locked` 根据锁文件安装依赖。之后使用 `uv run python ...` 运行示例，无需手动激活虚拟环境。

当前第三方依赖：

| 库 | 用途 |
| --- | --- |
| HTTPX | 异步 HTTP 请求和响应流读取 |
| Pydantic v2 | 数据类型校验、JSON 解析和 JSON Schema 生成 |
| prompt_toolkit | 异步终端输入、输入编辑和输入期间的输出展示 |
| Rich | Markdown、代码高亮、欢迎面板、工具卡片和终端排版 |

## 先运行本地示例

这些示例不需要 API Key，也不会调用远程模型。

```powershell
# 观察模拟模型逐段输出
uv run python examples/stream_text.py

# 观察工具参数片段如何拼成完整调用
uv run python examples/tool_call_buffer.py

# 实际读取当前项目的 pyproject.toml
uv run python examples/read_tool.py

# 观察工具成功、参数错误和工具不存在时的统一处理
uv run python examples/tool_runner.py

# 在临时目录串起写入、编辑、读取、目录和搜索工具
uv run python examples/coding_tools.py

# 保存、加载会话，并恢复所选分支；结束后自动清理临时文件
uv run python examples/session_storage.py
```

搜索示例需要系统程序 `rg` 和 `fd`；可复用本机 pi 安装的版本。命令执行示例可以添加 `--shell bash` 或 `--shell powershell`。Python 依赖没有增加。

## 配置 DeepSeek

当前连接配置默认使用 DeepSeek 官方服务和 `deepseek-flash` 模型 ID。具体可用模型以 [DeepSeek 官方文档](https://api-docs.deepseek.com/quick_start/pricing/) 为准。

在运行示例的 PowerShell 终端中设置：

```powershell
$env:DEEPSEEK_API_KEY = "替换为你自己的 API Key"

# 以下两项可选，代码中已有默认值
$env:MINI_PI_MODEL = "deepseek-flash"
$env:MINI_PI_BASE_URL = "https://api.deepseek.com"
```

这些设置作用于当前终端会话及其启动的程序。当前代码只读取进程环境变量，不会自动加载 `.env` 文件。

```powershell
# 查看配置；API Key 使用 SecretStr 遮盖显示
uv run python examples/show_config.py

# 查看真实模型的流式文字响应
uv run python examples/deepseek_raw_stream.py

# 通过 Provider、注册表和 complete() 取得完整响应
uv run python examples/deepseek_provider.py
```

后两个示例会实际调用模型，使用对应账户的 API 额度。当前接入显式关闭思考模式。

`deepseek_provider.py` 保留为仅演示模型接入的阶段示例。完整主循环见 `agent_state.py`；使用默认 coding 工具执行真实任务的示例为：

```powershell
uv run python examples/coding_agent.py "读取 pyproject.toml，说明项目依赖"
```

Windows 上如需使用 PowerShell，可添加 `--shell powershell`。上述示例会实际调用模型，工具也会按模型提出的调用执行。

## 建议阅读顺序

| 阶段 | 源码 | 对应示例 | 重点 |
| --- | --- | --- | --- |
| 1 | `ai/types.py` | `convert_message.py` | 消息对象、工具定义、实际参数的区别 |
| 2 | `ai/events.py`、`ai/provider.py`、`providers/fake.py` | `stream_text.py` | `async`、`yield`、`async for` 和事件流 |
| 3 | `ai/registry.py`、`ai/completion.py` | `register_text.py` | 注册查找与流式接口的包装 |
| 4 | `config.py`、`providers/chat_completions.py` | `show_config.py`、`build_request.py`、`deepseek_raw_stream.py` | 环境变量、消息转换、HTTP 与 SSE |
| 5 | `providers/tool_call_buffer.py`、`providers/chat_stream_types.py` | `tool_call_buffer.py` | 工具索引、参数片段与完整 JSON 对象 |
| 6 | `providers/deepseek.py` | `deepseek_provider.py` | 把接入逻辑封装成统一 Provider |
| 7 | `agent/types.py`、`coding/tools/read.py`、`agent/tool_runner.py` | `read_tool.py`、`tool_runner.py` | 参数校验、实际执行、结果与错误反馈 |
| 8 | `agent/events.py`、`agent/loop.py`、`agent/agent.py`、`agent/state.py` | `agent_state.py` | 主循环、停止条件和连续对话状态 |
| 9 | `agent/hooks.py`、`agent/tool_runner.py` | `model_hooks.py`、`tool_hooks.py` | 调用前后扩展、拦截与结果处理 |
| 10 | `coding/tools/` | `coding_tools.py`、`coding_agent.py` | pi 风格工具、精确修改和输出控制 |
| 11 | `coding/session/` | `session_storage.py` | 消息树、追加存储、加载与分支恢复 |
| 12 | `coding/coding_session.py`、`coding/history.py`、`cli/` | `uv run mini-pi --fake` | 按事件保存、取消、消息队列与终端交互 |
| 13 | `coding/project_context.py`、`coding/context_hooks.py`、`cli/prompts.py` | CLI 启动时显示项目指令来源 | 项目约定、系统提示组装与历史消息的区别 |
| 14 | `coding/compaction/`、`coding/session/projection.py`、`agent/context.py` | 见上下文压缩说明 | 安全边界、完整工具交互、摘要与原始历史的区别 |

上表中的源码路径以 `src/mini_pi/` 为基准，示例路径以 `examples/` 为基准。早期原型位于 `examples/prototype/`，仅保留作学习对照，不作为当前版本入口。

学习时建议手写消息状态变化、参数拼接、执行边界和主循环；导入、重复类型声明和启动示例可以复制。重点是能够解释每一步的数据从哪里来、交给谁，以及失败后如何处理。

## 后续计划

- [x] Agent 主循环：模型调用、工具执行、历史追加和停止条件。
- [x] Agent 状态封装与运行时事件。
- [x] Hooks：模型调用前后、工具执行前后的扩展点。
- [x] 写入、精确编辑、搜索和命令执行工具。
- [x] 工具输出截断与命令超时处理。
- [x] Agent 主动取消与嵌套事件流关闭。
- [ ] 工具增量事件。
- [x] 第一版 CLI 交互与终端流式展示代码。
- [x] 原创字符标识、Markdown 渲染、多行输入和状态栏。
- [x] 用户级命令安装与指定工作目录。
- [x] Flash 模型通过 read 工具接收本地图片。
- [x] 可配置模型输出上限，以及截断工具调用的执行保护。
- [ ] CLI 交互、取消和真实模型集成验证。
- [x] 独立会话存储层：保存、加载、分支选择和上下文重建。
- [x] 会话与 Agent 的集成及末尾缺失工具结果的恢复。
- [x] 启动时加载工作目录下的 AGENTS.md，并用于新会话和恢复会话的模型请求。
- [ ] 项目指令的父目录查找和运行中重新加载。
- [x] 1M 窗口下的 60% 自动压缩、近期完整交互保留和压缩记录重建。
- [ ] 上下文压缩的自动化与真实模型验证。
- [ ] 将各阶段整理成配套教程文档。
