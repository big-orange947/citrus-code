# mini-pi

**带初学者用 Python，一步步重写一个 mini 版 pi coding agent。**

这是一个持续实现中的学习项目，参考 [pi](https://github.com/earendil-works/pi) 的核心设计思路，用 Python 理解模型调用、流式事件、工具执行和 Agent 运行时如何协作。

适合有 Java 或其他语言基础、正在学习 Python，同时对 Agent 开发有一些了解的读者。项目不使用 LangChain、LangGraph 等 Agent 框架；网络通信和数据校验分别使用 HTTPX 与 Pydantic，其余流程逐步实现。

> 当前仓库已完成模型调用层、工具调用解析、工具接口和工具运行器。Agent 主循环尚未加入当前源码，CLI、Hooks 和会话管理也在后续计划中。

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
| 消息类型 | 系统、用户、助手、工具结果消息；文字块与工具调用块 |
| Provider 接口 | 统一模型信息和异步事件流，提供注册与查找 |
| FakeProvider | 使用预设文字模拟流式响应，便于理解异步生成器 |
| DeepSeek 接入 | HTTP 请求、SSE 读取、非思考模式文字流、用量统计和错误事件 |
| 工具调用解析 | 按索引拼接多个工具调用的参数，转换成统一工具事件 |
| 完整回答接口 | `complete()` 消费事件流并返回最终助手消息 |
| 工具接口 | 区分模型可见的工具定义与 Python 执行实现 |
| read 工具 | 读取 UTF-8 文本，支持工作目录、起始行和行数限制 |
| 工具运行器 | 按名称查找工具，将执行结果或异常转换成工具结果消息 |

当前 `read` 以行数限制输出；统一字节截断、图片读取和更完整的取消处理尚待实现。

## 项目结构

```text
mini-pi/
├── src/mini_pi/
│   ├── config.py                 # 从环境变量加载连接配置
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
│   │   ├── types.py
│   │   └── tool_runner.py
│   └── coding/tools/             # 具体 coding 工具
│       └── read.py
├── examples/                     # 各学习阶段的独立演示
│   └── prototype/                # 早期原型存档，供对照阅读
├── pyproject.toml
└── uv.lock
```

## 环境准备

需要 Python 3.12 或更新版本，以及 [uv](https://docs.astral.sh/uv/)。以下命令从仓库根目录执行。

```powershell
git clone https://github.com/big-orange947/mini-pi.git
cd mini-pi
uv sync --locked
```

`uv sync --locked` 根据锁文件安装依赖。之后使用 `uv run python ...` 运行示例，无需手动激活虚拟环境。

当前第三方依赖：

| 库 | 用途 |
| --- | --- |
| HTTPX | 异步 HTTP 请求和响应流读取 |
| Pydantic v2 | 数据类型校验、JSON 解析和 JSON Schema 生成 |

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
```

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

`deepseek_provider.py` 会向模型声明 `read` 工具，并显示模型提出的工具调用；该示例尚未把调用交给 `ToolRunner` 执行，也没有把工具结果再次发给模型。`read_tool.py` 和 `tool_runner.py` 分别演示本地工具执行。下一阶段的 Agent 主循环将把这两部分连接起来。

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

上表中的源码路径以 `src/mini_pi/` 为基准，示例路径以 `examples/` 为基准。早期原型位于 `examples/prototype/`，仅保留作学习对照，不作为当前版本入口。

学习时建议手写消息状态变化、参数拼接、执行边界和主循环；导入、重复类型声明和启动示例可以复制。重点是能够解释每一步的数据从哪里来、交给谁，以及失败后如何处理。

## 后续计划

- [ ] Agent 主循环：模型调用、工具执行、历史追加和停止条件。
- [ ] Agent 状态封装与运行时事件。
- [ ] Hooks：模型调用前后、工具执行前后的扩展点。
- [ ] 写入、精确编辑、搜索和命令执行工具。
- [ ] 输出截断、取消与超时处理。
- [ ] CLI 交互与终端流式展示。
- [ ] 会话保存、恢复和上下文压缩。
- [ ] 将各阶段整理成配套教程文档。
