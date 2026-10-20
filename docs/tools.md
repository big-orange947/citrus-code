# 内置工具：实现与 pi 的对应关系

工具名、模型可见的主要参数和默认组合参考本机 pi 的
`packages/coding-agent/src/core/tools/`。Python 使用 Pydantic 生成 JSON Schema，
不引入 Agent 框架，也没有新增 Python 依赖。

## 入口

```python
from pathlib import Path

from mini_pi.coding.tools import (
    create_all_tools,
    create_coding_tools,
    create_read_only_tools,
    create_tools,
)

cwd = Path.cwd()
coding = create_coding_tools(cwd)       # read, bash, edit, write
read_only = create_read_only_tools(cwd) # read, grep, find, ls
all_tools = create_all_tools(cwd)       # 按名称索引的八个工具对象
windows = create_tools(cwd, ("read", "powershell", "edit", "write"))
```

将列表传入 `Agent(tools=...)` 或 `ToolRunner(tools=...)` 即可。全部工具通过同一个
ToolRunner 执行，沿用现有工具 Hooks 和错误转换。工厂只创建对象，不启动进程。

## 参数与行为

| 工具 | 参数 | 行为 |
| --- | --- | --- |
| read | path, offset?, limit? | 起始行从 1 开始；文本保留开头；图片作为附件返回 |
| write | path, content | 创建或完整覆盖 UTF-8 文件，自动创建父目录 |
| edit | path, edits: [{oldText, newText}] | 全部替换针对原文件定位，要求唯一且互不重叠 |
| bash | command, timeout? | 使用 Bash 执行，timeout 单位秒，省略时不设超时 |
| powershell | command, timeout? | 使用 PowerShell 执行，与 bash 共用后端 |
| grep | pattern, path?, glob?, ignoreCase?, literal?, context?, limit? | 使用 rg，默认 100 个匹配行 |
| find | pattern, path?, limit? | 使用 fd 匹配 glob，默认 1000 个结果 |
| ls | path?, limit? | 包含隐藏项，按名称排序，目录带 /，默认 500 项 |

Python 属性 `old_text/new_text/ignore_case` 通过 Pydantic alias 对应模型参数
`oldText/newText/ignoreCase`。可省略的路径默认使用工作目录。

路径支持工作目录相对路径、绝对路径、`~` 和开头的 `@`。工作目录是相对路径的
解析基准，不是文件系统沙箱。

## edit 的执行边界

1. 读取文件，记录 UTF-8 BOM 和原来的 LF/CRLF 换行形式。
2. 统一换行符；优先精确匹配，必要时使用 pi 风格的字符规范化兜底匹配。
3. 检查全部目标是否存在、唯一，以及区域是否重叠。
4. 从后往前应用替换。兜底匹配只重写实际触及的行，其他行保留原文字。
5. 生成差异，恢复 BOM 和换行，再写回文件。

`details` 包含 `diff`、`patch`、`firstChangedLine`。当前 diff 和 patch 都使用
Python 标准库生成的统一差异格式，没有复制 pi 带行号的专用渲染格式。

`write` 和 `edit` 共用每文件互斥锁。同一进程中同一路径的修改串行执行，
不同文件仍可并行。锁在实际文件操作的线程中持有，避免等待协程取消后锁提前释放。
这是进程内协调，不提供跨进程文件锁，也不保证磁盘写入失败时的事务回滚。

## 输出控制

- read 和命令输出默认最多 2000 行或 50 × 1024 字节，先达到的限制生效。
- read 保留开头的完整行，并提供继续读取的 offset。
- 命令工具保留尾部；截断时保留完整输出临时文件，路径同时写入文字和 details。
- grep/find/ls 使用各自的匹配数或条目数限制，加上 50KB 字节限制。
- grep 单行最多展示 500 个字符。

截断提示本身不计入上述正文预算。命令输出直接落到文件，返回结果时扫描统计，
仅将受限的尾部保存在内存。临时文件在未截断时自动删除；截断文件保留供后续读取。

## 依赖的系统程序

- bash：Linux/macOS 使用 PATH 中的 bash；Windows 优先查找 Git Bash。
- powershell：优先使用 pwsh，其次使用 powershell。
- grep：需要 rg（ripgrep）。
- find：需要 fd。

rg/fd 优先从 PATH 查找，也可复用 `~/.pi/agent/bin/` 中已安装的程序。
缺失时返回明确错误，不自动下载。grep/find 将模式作为独立参数传给程序，
不经 shell 拼接。搜索遵循后端的 .gitignore 规则。

## 图片与取消的当前边界

read 能识别 PNG、JPEG、GIF、WebP、BMP 的文件头并返回图片块。
模型元数据 `supports_images=True` 时，Chat Completions 转换会在连续工具结果
之后添加一条图片用户消息，保持工具调用与结果配对。默认值为 False。
CLI 为 `deepseek-flash` 及其旧别名 `deepseek-v4-flash`、
`deepseek-v4-flash-vision-exp` 显式启用图片输入；其他模型 ID 保持关闭。
独立示例若自行创建 ModelSpec，仍需设置 `supports_images=True`。
这属于已知模型能力配置，不是对服务的自动探测；第三方转发服务还需实际支持图片。

DeepSeek 接入允许 PNG、JPEG、GIF 和 WebP；BMP 等其他格式在请求中替换为明确的
“图片未发送，请先转换”提示，原附件仍保留在历史中。SVG、HTML 按源码读取，
不会自动渲染为截图。CLI 当前通过本地文件
路径和 read 工具提供图片，没有直接读取剪贴板位图的输入入口。

当前不自动缩放或转码图片，服务是否接受对应格式和大小取决于实际模型接口。
命令超时或执行任务被取消时会请求清理进程树。Agent 已提供主动 abort API；
工具执行中的增量事件、后台任务管理和远程文件操作接口仍属于后续工作。

## 示例

以下示例在临时目录中演示 write/edit/read/ls/find/grep，退出后清理目录：

```powershell
uv run python examples/coding_tools.py
uv run python examples/coding_tools.py --shell bash
uv run python examples/coding_tools.py --shell powershell
```

下面的示例调用真实模型，需要已有环境变量配置。prompt 会原样交给 Agent，
它可以按任务使用工具：

```powershell
uv run python examples/coding_agent.py "读取 pyproject.toml，说明项目依赖"
uv run python examples/coding_agent.py --shell powershell "读取 pyproject.toml，说明项目依赖"
```

这些文件是阶段演示入口。日常使用 CLI，例如启动后输入：

```text
请调用 read 读取 D:\screenshots\page.png，描述图片内容，再指出布局问题。
```
