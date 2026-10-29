# 第一章：第一次模型调用

[教程目录](README.md) · [上一章：开始开发 Agent 前，你需要知道什么](00-before-you-start.md)

消息角色、上下文、工具调用与 Agent 主循环的整体关系见第零章。
本章会把其中的一次模型调用写成可运行的 Python 程序。

这一章只完成一个可以观察的结果：从配置文件读取模型连接信息，向模型发送一句话，然后在终端打印回答。

```text
本机配置文件
    ↓ 读取地址、密钥、模型名称
Python 程序
    ↓ 发送包含 messages 的 HTTP 请求
模型服务
    ↓ 返回包含回答的 JSON
终端打印回答
```

这条链路是后续 Agent 的基础。以后模型提出工具调用、程序执行工具、模型根据结果继续判断，
都会多次经过模型调用层。我们先把一次调用走通，再逐步建立统一接口和主循环。

## 1. 一次模型调用需要哪些信息

先明确我们准备发送什么：

```text
我们发送：用一句话解释什么是 ReAct。
模型返回：ReAct 是……
```

为了完成这次调用，需要准备五类信息：

| 信息 | 要解决的问题 | 本章示例 | 最终放在哪里 |
| --- | --- | --- | --- |
| 接口地址 | 请求发给谁？ | `https://api.deepseek.com/chat/completions` | HTTP 请求地址 |
| API Key | 服务怎样识别调用者？ | 你在本机填写的密钥 | `Authorization` 请求头 |
| 模型 ID | 使用哪个模型？ | `deepseek-flash` | 请求体的 `model` 字段 |
| 消息列表 | 把什么内容交给模型？ | 一条用户问题 | 请求体的 `messages` 字段 |
| 生成参数 | 这次怎样生成回答？ | 非流式、输出上限、关闭思考 | 请求体的其他字段 |

HTTP 请求可以先理解成“向服务发送的一份数据”。本章用 `POST` 方法提交数据，
请求头携带认证等信息，请求体携带模型和消息等业务内容。后面的 Python 代码负责把这些信息组织起来。

接下来先说明消息和参数，再把它们拼成一份完整请求。

## 2. 我们要发送哪些消息

### 一条消息包含什么

本章发送的消息是：

```json
{
  "role": "user",
  "content": "用一句话解释什么是 ReAct。"
}
```

一条普通文字消息有两个基本字段：`role` 表示这条消息由谁提供，`content` 表示具体内容。
这两个字段名由当前接口协议约定。把 `role` 改成 `speaker`，服务并不会自动理解它们含义相同。

### 四种常见角色分别负责什么

| role | 谁提供 | 放什么内容 | 什么时候出现在请求中 |
| --- | --- | --- | --- |
| `system` | 应用程序 | 助手的行为要求，例如“请用中文回答” | 需要给模型提供行为指令时 |
| `user` | 用户 | 当前问题、任务或补充要求 | 用户提出请求时 |
| `assistant` | 模型 | 之前的回答，也可能包含工具调用 | 后续请求需要携带模型历史回复时 |
| `tool` | 程序 | 某次工具执行的结果 | 工具执行后，让模型根据结果继续处理时 |

这四种角色不需要每次全部出现。第一次提问时，没有历史回答，也没有工具执行结果，
因此本章只发送一条 `user` 消息即可。`system` 可以按需要添加。
`tool` 消息还需要关联具体工具调用，例如通过 `tool_call_id` 指明回应哪次调用，后续工具章节再展开。

### 本章实际发送的 messages

接口通过 `messages` 接收一个按对话顺序排列的消息列表，所以即使只有一条消息，也写成列表：

```json
[
  {
    "role": "user",
    "content": "用一句话解释什么是 ReAct。"
  }
]
```

外面的 `[]` 表示列表，里面的 `{}` 表示其中一条消息。在 Python 中，
这个结构可以先类比 Java 的 `List<Map<String, String>>`。

如果应用还需要“请用中文简短回答”这条行为指令，就可以在用户消息前加一条 `system`：

```json
[
  {"role": "system", "content": "请用中文简短回答。"},
  {"role": "user", "content": "用一句话解释什么是 ReAct。"}
]
```

这是说明消息组合方式的扩展示例；本章配套代码仍然只发送前面的一条用户消息。

### 多轮对话时消息怎样增加

当模型已经回答过一次，下一次请求可以带上之前的对话：

```json
[
  {"role": "user", "content": "我会 Java，正在学习 Python。"},
  {"role": "assistant", "content": "我会结合 Java 来解释。"},
  {"role": "user", "content": "那 Python 的 dict 是什么？"}
]
```

其中的 `assistant` 是上一轮得到的模型回答。第一次调用时，我们不需要预先编写一条回答。
对于本章使用的接口，下一次请求需要哪些历史，由应用组织并重新提交；
复用同一个 HTTP 客户端不会自动把上一次的对话添加进来。

本章的问题只是用于观察结果，可以换成“用一句话解释 Python 字典”。
询问模型“什么是 ReAct”只会得到一段解释，真正的 ReAct Agent 还需要程序执行工具、
把结果放回消息列表并再次请求模型，这些会在后续章节实现。

## 3. 本章使用的全部请求体字段

消息列表准备好后，还需要选择模型和生成方式。本章请求体一共包含下面五个字段：

| 字段 | 本章取值 | 作用 | 是否必须显式填写 |
| --- | --- | --- | --- |
| `model` | `"deepseek-flash"` | 选择服务提供的模型 | 是 |
| `messages` | 一条用户消息组成的列表 | 提交本次对话内容 | 是 |
| `stream` | `false` | 取得完整响应，暂不逐段接收 | 可以省略，本章显式表达意图 |
| `max_tokens` | `512` | 限制本次生成的 token 数量 | 可以省略，本章主动控制输出预算 |
| `thinking` | `{"type": "disabled"}` | 关闭本章使用的 DeepSeek 接口的思考模式 | 本章显式设置，属于服务特定参数 |

Token 是模型处理文字的计量单位，不等于字符数。`max_tokens` 约束这次生成，
不表示上下文窗口大小，也不表示 Agent 最多循环几次。

`thinking` 是这里使用的 DeepSeek 参数。换供应商时，需要确认对方支持哪些字段。
本章只介绍实际发送的这些参数，服务还提供其他可选项，完整定义见
[DeepSeek Chat Completions 文档](https://api-docs.deepseek.com/api/create-chat-completion/)。

API Key 通过请求头传递；请求体里只有模型、消息与生成参数。
Python 代码中的布尔值写成 `False`，JSON 中写成 `false`，发送时由 HTTPX 完成转换。

## 4. 合成一份完整请求

把前面的信息合起来，本章发出的业务请求如下。这里只省略 HTTP 客户端自动生成的传输头，
密钥用占位符表示。

请求方法、地址和请求头：

```http
POST https://api.deepseek.com/chat/completions
Authorization: Bearer <你的 API Key>
Content-Type: application/json
```

请求体：

```json
{
  "model": "deepseek-flash",
  "messages": [
    {
      "role": "user",
      "content": "用一句话解释什么是 ReAct。"
    }
  ],
  "stream": false,
  "max_tokens": 512,
  "thinking": {
    "type": "disabled"
  }
}
```

这里的 `Authorization` 用于认证，`Bearer` 是这类认证使用的前缀。
`Content-Type: application/json` 告诉服务请求体采用 JSON 格式。
真正发请求时，下面的 Python 代码会分别构造地址、请求头和请求体，再交给 HTTPX。

可以沿着这份对应关系检查数据是否放对位置：

```text
base_url                 → 拼接 /chat/completions → 请求地址
api_key                  → 加上 Bearer 前缀       → Authorization 请求头
model                    →                         请求体的 model
用户的问题               → 一条 user 消息         → 请求体的 messages
stream / max_tokens 等   →                         请求体的其他字段
```

到这里，应当能在不阅读 Python 实现的情况下，指出这次请求发给谁、怎样认证、
用哪个模型、发送哪些消息，以及使用哪些生成参数。

## 5. 准备环境与示例目录

需要 Git、uv，以及符合项目要求的 Python 3.9 或更新版本。还没有源码时，执行：

```powershell
git clone https://github.com/big-orange947/citrus-code.git
cd citrus-code
```

已经克隆过仓库的读者，直接进入自己的仓库根目录。

```powershell
uv sync --locked
```

这里涉及三个文件或目录：

| 名称 | 用途 | 类比 Java 项目 |
| --- | --- | --- |
| `pyproject.toml` | 声明项目及其依赖 | 承担一部分 `pom.xml` 的职责 |
| `uv.lock` | 记录解析后的具体依赖版本 | 帮助不同机器安装相同版本 |
| `.venv/` | 存放当前项目使用的 Python 环境和依赖 | 项目独立的运行环境 |

`uv sync --locked` 根据锁文件同步环境；如果项目依赖声明与锁文件不一致，就报错。
后面通过 `uv run` 启动 Python，无需自己激活 `.venv`。
见 [uv 的锁定与同步说明](https://docs.astral.sh/uv/concepts/projects/sync/)。

本章使用的 HTTPX 已经包含在项目依赖中。它是负责发送 HTTP 请求的 Python 库，作用可以类比 Java 的
`HttpClient`。Python 自带的 `json` 和 `pathlib` 则分别负责 JSON 转换与文件路径。

### 本章代码放在哪里

本章的完整示例是 [examples/tutorial/01_first_call.py](../../examples/tutorial/01_first_call.py)。
它直接使用 HTTPX 发请求，暂时独立于 `src/citrus_code/` 中的完整实现。

```text
citrus-code/
├── .citrus-code/
│   └── tutorial.json              # 你在本机填写的教学配置，已被 Git 忽略
├── examples/tutorial/
│   ├── settings.example.json      # 不含密钥的配置模板
│   └── 01_first_call.py            # 本章代码
├── docs/tutorial/
│   └── 01-first-call.md
├── pyproject.toml
└── uv.lock
```

`tutorial.json` 是本章专用的简化配置，只有三个字段。完整 CLI 使用的 `settings.json` 还包含供应商列表、
模型能力等内容，两者格式不同。先把最基本的数据流理解清楚，后续再扩展配置结构。

## 6. 准备配置文件

以下命令在仓库根目录执行。复制模板只需做一次；已经填写过 `tutorial.json` 时，直接编辑原文件即可。

```powershell
New-Item -ItemType Directory -Force .citrus-code
Copy-Item examples/tutorial/settings.example.json .citrus-code/tutorial.json
notepad .citrus-code/tutorial.json
```

把文件中的 `api_key` 填好：

```json
{
  "base_url": "https://api.deepseek.com",
  "api_key": "在本机替换成你的 API Key",
  "model": "deepseek-flash"
}
```

这三个键由我们的程序定义：

- `base_url`：请求发给谁。本章程序会在它后面追加 `/chat/completions`，因此这里填写基础地址。
- `api_key`：使用这项服务的凭证，之后会放进 HTTP 请求头。
- `model`：希望服务调用哪个模型，必须使用服务接受的模型 ID。

本章以 DeepSeek 官方接口为例。当前示例模型和 `thinking` 参数依据
[DeepSeek Chat Completions 文档](https://api-docs.deepseek.com/api/create-chat-completion/)。
如果以后服务调整模型名称，以服务文档和自己的账户权限为准。

使用中转站时，地址、密钥、模型 ID 都应由中转站提供。仅替换地址不能保证所有字段兼容，
后续统一模型层会处理协议和服务参数的区别。本章先使用上面的官方接口。

文件保存后关闭编辑器。JSON 字符串使用双引号，最后一个字段后面没有逗号；文件中也不要写注释。
真实密钥留在本机即可，仓库已经忽略 `.citrus-code/`。文件里的密钥仍是明文，这种做法只是把连接配置与源码分开。

## 7. 用 Python 构造并发送请求

前面的 JSON 请求体，在 Python 中用字典和列表构造：

```python
url = config["base_url"].rstrip("/") + "/chat/completions"
headers = {"Authorization": f"Bearer {config['api_key']}"}
messages = [{"role": "user", "content": question}]
body = {
    "model": config["model"],
    "messages": messages,
    "stream": False,
    "max_tokens": 512,
    "thinking": {"type": "disabled"},
}
```

`config` 保存从文件读出的三个字符串。`rstrip("/")` 去掉基础地址末尾的斜杠，
再拼上接口路径；`f"...{...}"` 把密钥放进认证字符串。
`question` 保存用户问题，用来构造消息的 `content`。

现在得到的 `url`、`headers` 和 `body`，分别对应第 4 节完整请求的地址、请求头和请求体。

```python
with httpx.Client(timeout=httpx.Timeout(120.0, connect=10.0)) as client:
    response = client.post(url, headers=headers, json=body)
    response.raise_for_status()
    data = response.json()
```

`httpx.Client` 创建同步 HTTP 客户端。执行 `client.post(...)` 时，这段程序等待请求返回，然后才执行下一句。

`with ... as client` 表示进入一个资源管理范围。离开缩进块时，即使中间抛出异常，也会关闭客户端并释放连接资源，
作用可以类比 Java 的 try-with-resources。

`client.post` 中的 `headers=headers`、`json=body` 是 Python 的关键字参数写法：左边是参数名，右边是我们前面定义的变量。
`json=body` 会把 Python 字典序列化成 JSON，并设置相应的内容类型。不要先把字典转成字符串再传给 `json=`，
否则会把字符串再次编码。见 [HTTPX QuickStart](https://www.python-httpx.org/quickstart/)。

超时配置中，建立连接的等待上限是 10 秒，其他阶段默认使用 120 秒。它们约束相应的网络等待，
不是整个任务必须在 120 秒内结束的总时限。见 [HTTPX 超时说明](https://www.python-httpx.org/advanced/timeouts/)。

下面这三步处理的是不同问题：

```text
client.post(...)             发请求、取得 HTTP 响应
response.raise_for_status() 检查 HTTP 状态是否成功
response.json()             把响应中的 JSON 解析成 Python 数据
```

请求在网络上发出，不代表服务接受了它。例如，认证失败可能得到 HTTP 401。
`raise_for_status()` 会在失败状态上抛出异常，避免继续把错误响应当作正常回答解析。

HTTP 成功也不代表一定得到完整文字，所以接下来还需要检查回答本身。

## 8. 展示响应并取出回答

服务响应包含不少字段。下面是只保留本章关注字段的示意，并非逐字抓取的真实响应：

```json
{
  "choices": [
    {
      "message": {
        "role": "assistant",
        "content": "ReAct 是让模型交替进行推理和行动，并根据工具反馈继续推进任务的方法。"
      },
      "finish_reason": "stop"
    }
  ]
}
```

因此取出第一条候选结果的路径是：

```python
choice = data["choices"][0]
content = choice["message"]["content"]
```

`[0]` 取列表的第一个元素，Python 列表从 0 开始计数。

完整示例还要求 `finish_reason` 为 `stop`，并且 `content` 为非空字符串。
例如服务以 `length` 结束时，说明这次生成遇到了长度限制；本章会报告结束原因，避免把截断回答当成完整成功。

检查通过后，`return content` 把字符串交还给 `main()`，再由 `print()` 显示。
这里默认服务遵守所选协议；如果它没有返回 `choices` 等必要字段，就会出现字段或索引异常。
后续模型层会集中处理响应结构校验。

## 9. 完整代码

下面把配置读取、模型调用和启动入口合在一起。完整示例位于
[examples/tutorial/01_first_call.py](../../examples/tutorial/01_first_call.py)。
可以先复制运行，再回头手写 `ask_model()`。配置读取的逐行解释见文末的 Python 补充知识。

```python
import json
from pathlib import Path

import httpx


def load_config(path: Path) -> dict[str, str]:
    text = path.read_text(encoding="utf-8-sig")
    data = json.loads(text)

    if not isinstance(data, dict):
        raise ValueError("配置文件的最外层必须是 JSON 对象。")

    config: dict[str, str] = {}
    for name in ("base_url", "api_key", "model"):
        value = data.get(name)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"请在配置文件中填写非空字符串：{name}")
        config[name] = value.strip()

    return config


def ask_model(config: dict[str, str], question: str) -> str:
    url = config["base_url"].rstrip("/") + "/chat/completions"
    headers = {"Authorization": f"Bearer {config['api_key']}"}
    messages = [{"role": "user", "content": question}]
    body = {
        "model": config["model"],
        "messages": messages,
        "stream": False,
        "max_tokens": 512,
        "thinking": {"type": "disabled"},
    }

    with httpx.Client(timeout=httpx.Timeout(120.0, connect=10.0)) as client:
        response = client.post(url, headers=headers, json=body)
        response.raise_for_status()
        data = response.json()

    choice = data["choices"][0]
    if choice["finish_reason"] != "stop":
        raise RuntimeError(
            f"本章需要完整的文字回答，实际结束原因：{choice['finish_reason']}"
        )

    content = choice["message"]["content"]
    if not isinstance(content, str) or not content.strip():
        raise RuntimeError("服务没有返回可显示的文字回答。")

    return content


def main() -> None:
    project_root = Path(__file__).resolve().parents[2]
    config_path = project_root / ".citrus-code" / "tutorial.json"
    config = load_config(config_path)

    question = "用一句话解释什么是 ReAct。"
    print("问题：", question)
    print("正在等待模型回答……")

    answer = ask_model(config, question)
    print("回答：", answer)


if __name__ == "__main__":
    main()
```

## 10. 运行与预期结果

确认配置已经保存，在仓库根目录执行：

```powershell
uv run --locked python examples/tutorial/01_first_call.py
```

这个命令会调用真实模型并消耗对应账户的 API 额度。可能看到：

```text
问题： 用一句话解释什么是 ReAct。
正在等待模型回答……
回答： ReAct 是让模型交替进行推理和行动，并根据工具反馈继续推进任务的方法。
```

回答文字不需要与示例完全一致。成功标准是：程序读取了本机配置，取得一条完整文字回答，并正常退出。

执行顺序如下：

```text
执行 main()
  → 定位配置文件
  → load_config() 读取并检查配置
  → 定义本次问题
  → ask_model() 构造地址、请求头和请求体
  → client.post() 等待 HTTP 响应
  → 检查 HTTP 状态、结束原因和文字内容
  → 返回回答字符串
  → print() 打印回答
```

这次没有使用 `async`、`await` 或 SSE。同步调用与流式输出是两个不同维度：
同步程序也可以读取流，异步程序也可以等待完整响应。本章选择同步、非流式，方便先观察一次请求的输入与输出。

## 11. 常见问题

| 表现 | 先检查什么 |
| --- | --- |
| `FileNotFoundError` | 是否创建并保存了仓库下的 `.citrus-code/tutorial.json` |
| `JSONDecodeError` | 配置是否有注释、单引号、漏逗号或多余逗号 |
| 提示某个字段必须为非空字符串 | 是否已填写 `base_url`、`api_key`、`model`，并保存文件 |
| HTTP 401 或 403 | 密钥、服务账户权限，以及密钥是否属于该地址对应的服务 |
| HTTP 404 | 基础地址和接口路径；本章会自动追加 `/chat/completions` |
| HTTP 400 | 模型 ID 和服务支持的请求参数，尤其是使用中转站时 |
| HTTP 429 | 服务限流或账户额度情况，按服务返回的提示检查 |
| `ConnectTimeout`、`ReadTimeout` | 网络是否可达、服务是否正常，以及是否超出配置的等待时间 |
| 结束原因为 `length` | 生成达到长度限制；保持关闭思考，缩短问题或按服务限制调整输出预算 |
| 修改了配置却没有变化 | 编辑器是否保存，以及是否改的是本章的 `tutorial.json` |

本章让异常直接显示，便于理解失败发生在哪一步。后续会加入统一错误事件、取消与临时错误重试。

## 12. 与 pi 设计思路的联系

我们现在已经把职责划分成三个函数：

```text
load_config()  取得连接配置
ask_model()    处理一次具体服务的请求和响应
main()         组织一次调用并显示结果
```

例如，下一章接入另一种服务时，最先受影响的是 `ask_model()` 中的请求格式和响应解析。
如果以后主循环也直接访问 `choices[0].message.content`，每换一种协议，都可能需要修改主循环。

这就是引入统一模型层的原因：把具体协议留在 Provider 内，让上层接收统一的消息和结果。
这里沿用 pi 中“模型接入层与 Agent 运行逻辑分开”的思路。当前三个函数只是教学起点，
尚未建立完整的分层接口，也不要求照搬 pi 的 TypeScript API。

完成本章后，可以回看完整项目的 [settings.py](../../src/citrus_code/settings.py) 和
[模型接入目录](../../src/citrus_code/ai/providers/)，了解这些职责最终放在哪里；暂时不需要逐行阅读完整实现。

## 13. 动手练习与完成标准

把 `question` 改成“用 Java 的 Map 类比解释 Python 的 dict”，重新运行，观察回答是否变化。
再清空本机配置里的 `api_key`，保存并运行，观察程序是否在发送请求前指出字段问题，最后恢复配置。

本章建议手写 `ask_model()`，配置模板和启动入口可以复制。完成后应能解释：

1. 一次调用需要准备哪些信息？请求头和请求体分别放什么？
2. system、user、assistant、tool 分别由谁提供？为什么本章只发送 user？
3. 为什么配置文件要先读成字符串，再用 `json.loads()` 解析？
4. API Key、模型 ID 和聊天内容分别放在请求的哪里？
5. 为什么只发送一句话，`messages` 仍然是列表？
6. `response.json()` 与 `json=body` 的数据转换方向有何不同？
7. 为什么 HTTP 请求成功后，还要检查模型的结束原因？
8. 问模型“什么是 ReAct”，为什么不等于已经实现了 ReAct Agent？

下一章将从“换一种服务时，上层代码应当依赖什么”出发，建立统一模型层。

## 附录：Python 补充知识

这一节集中说明配置读取与启动入口涉及的 Python 写法，便于需要时回查。
Python 用缩进表示代码块，同一层缩进表示同一个函数、循环或条件分支中的语句。

### A. 配置文件怎样变成 Python 数据

先看函数签名：

```python
def load_config(path: Path) -> dict[str, str]:
```

- `def` 定义函数；这里定义了一个名为 `load_config` 的函数。
- `path: Path` 表示参数预期是一个路径对象。
- `-> dict[str, str]` 表示返回值预期是字典，键和值都是字符串，可以类比 `Map<String, String>`。

这些类型标注帮助 IDE 和读代码的人理解数据。Python 不会仅凭标注在运行时自动校验参数，
因此后面仍然需要实际检查配置内容。

```python
text = path.read_text(encoding="utf-8-sig")
data = json.loads(text)
```

第一句读取文件，得到字符串。`utf-8-sig` 兼容普通 UTF-8，以及一些 Windows 编辑器保存的带 BOM 的 UTF-8 文件。

第二句把 JSON 字符串解析成 Python 对象。例如：

```text
文件里的文字：{"model": "deepseek-flash"}
    ↓ json.loads
Python 字典：{"model": "deepseek-flash"}
    ↓ data["model"]
Python 字符串：deepseek-flash
```

文件里写了 `{...}`，不意味着 Python 已经拿到了字典。读取文件与解析 JSON 是两个动作。

```python
if not isinstance(data, dict):
    raise ValueError("配置文件的最外层必须是 JSON 对象。")
```

`isinstance(data, dict)` 检查对象是否为字典。有人可能把配置写成 `[]`，它也是合法 JSON，
但解析后是列表，不能按我们需要的方式读取字段。

`raise` 抛出异常，可以类比 Java 的 `throw`。这里遇到不符合要求的配置就停止，避免拿错误数据发送请求。

```python
config: dict[str, str] = {}
for name in ("base_url", "api_key", "model"):
    value = data.get(name)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"请在配置文件中填写非空字符串：{name}")
    config[name] = value.strip()
```

`{}` 创建空字典；`for` 依次取出三个字段名。`data.get(name)` 在字段缺失时返回 `None`，
而 `data[name]` 在字段缺失时会抛出 `KeyError`。

`or` 从左到右判断。如果 `value` 不是字符串，右边的 `.strip()` 就不会执行；如果它是字符串，
再检查去掉首尾空白后是否还有内容。空字符串在条件判断中为假，因此 `not value.strip()` 可以判断内容为空。

这里的 `f"...{name}"` 是字符串插值。例如 `name` 为 `api_key` 时，会在错误消息里显示字段名。
我们只指出哪个字段有问题，不打印密钥本身。

本章的校验只检查三个字段是否为非空字符串，还没有验证 URL 是否有效、密钥是否被服务接受。
后续会用 Pydantic 集中处理更完整的配置与类型校验。

### B. main 和文件路径怎样工作

```python
project_root = Path(__file__).resolve().parents[2]
config_path = project_root / ".citrus-code" / "tutorial.json"
```

`__file__` 是当前 Python 文件的位置，`resolve()` 得到解析后的绝对路径。对本章文件来说：

```text
.../citrus-code/examples/tutorial/01_first_call.py

parents[0] → .../citrus-code/examples/tutorial
parents[1] → .../citrus-code/examples
parents[2] → .../citrus-code
```

`Path` 对象之间的 `/` 用于拼接路径，避免自己处理 Windows 的反斜杠和其他系统的斜杠。
这两行按照本章固定目录结构寻找配置；移动脚本后，也要相应修改定位方式。

最后的入口是：

```python
if __name__ == "__main__":
    main()
```

直接运行这个文件时，`__name__` 为 `"__main__"`，于是调用 `main()`。
如果其他 Python 文件导入它来复用函数，这个分支不会执行，也就不会在导入时自动发送模型请求。
`main` 本身是我们取的函数名，Python 没有像 Java 那样强制要求这个名字。
