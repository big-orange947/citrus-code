# 可替换的工具执行操作

工具现在分为两部分：

```text
模型调用工具
  → 工具：Pydantic 参数校验、编辑算法、输出截断和结果格式
  → operations：实际文件访问、命令执行和搜索
  → 默认 Local 实现：本机文件系统与进程
```

Agent 主循环、ToolRunner、模型可见的工具参数和四个 Hooks 沿用原有接口。
本次提供替换执行实现的入口，默认 CLI 使用本机实现。当前没有容器、虚拟机、
网络限制或操作系统权限隔离适配器，不能把此接口理解为已经启用沙箱。

## 接口与实现

`coding/tools/operations.py` 使用 Python 标准库的 `Protocol` 描述接口，可以类比
Java 的接口。实现类只需要提供规定的方法，不必继承 Protocol。它用于类型检查，
不会自动生成权限规则，也不会替代 Pydantic 的工具参数校验。

`ToolOperations` 是标准库 `dataclass` 定义的配置对象，将三类实现组合起来：

| 接口 | 使用它的工具 | 默认实现 |
| --- | --- | --- |
| FileOperations | read、write、edit、ls | LocalFileOperations |
| ShellOperations | bash、powershell | LocalShellOperations |
| SearchOperations | grep、find，含 grep 的上下文读取 | LocalSearchOperations |

FileOperations 负责解析路径、读取和写入字节、创建父目录、列目录和文件修改锁。
ShellOperations 接收命令、工作目录、shell 类型、可选超时和输出文件流，返回退出状态；
被取消或超时时必须清理相应执行任务。SearchOperations 返回匹配项或路径标签，
生成器被关闭时必须清理搜索进程或远端搜索任务。

## 注入一套操作实现

```python
from pathlib import Path

from citrus_code.coding.tools import (
    LocalFileOperations,
    LocalSearchOperations,
    LocalShellOperations,
    ToolOperations,
    create_tools,
)

files = LocalFileOperations()
operations = ToolOperations(
    files=files,
    shell=LocalShellOperations(),
    search=LocalSearchOperations(files),
)
tools = create_tools(
    Path.cwd(),
    ("read", "powershell", "edit", "write"),
    operations=operations,
)
```

以上仍在本机执行。以后适配容器、虚拟机或远程服务时，传入对应实现；Agent
继续使用同一组工具接口。`create_tool`、`create_coding_tools`、
`create_read_only_tools`、`create_all_tools` 也接受 `operations`。

直接构造单个工具时，只传它需要的接口，例如：

```python
from citrus_code.coding.tools import ReadTool

reader = ReadTool(Path.cwd(), operations=files)
```

只有省略 operations 时才选择本机实现；自定义实现执行失败时向上传递错误，
由 ToolRunner 转成工具错误结果，不会自动改成本机执行。
一套自定义 ToolOperations 必须显式提供 files、shell、search，避免遗漏某类工具
而不知情地沿用本机实现。自行传入 Local 实现仍然表示允许本机操作。

## 路径、线程和输出文件

文件路径是后端返回的字符串。工具不会再对这些字符串调用宿主机的 `resolve`、
`exists` 或 `is_dir`。例如 Windows 宿主机上的后端可以把项目路径映射为
Linux 环境中的 `/workspace`，再在该环境里解析相对路径和访问文件。
`cwd: Path` 表示调用方提供的工作目录，适配器应按自身环境解释或映射它。

文件读写与列目录继续运行在线程中。`mutation(path)` 必须在实际文件操作的线程
持有锁，直到读取、定位、替换和写入全部完成；协程取消不能提前释放该锁。
默认实现沿用进程内的每文件互斥锁。自定义实现应让 write/edit 对同一目标使用同一锁。

命令输出写入受控的本地临时文件，工具继续扫描并保留尾部，避免一次性读取完整输出。
发生截断时，ShellOperations.persist_output 在工作线程中把完整日志保存在该环境的
文件工具可读取的位置，并返回路径。默认返回本地临时文件路径；远程实现应复制或上传
日志后返回远程路径，本地临时副本随后删除，不能把宿主机日志路径冒充为远端路径。

只替换 ShellOperations 不会限制文件工具；只替换 FileOperations 也不会隔离
本机 rg/fd 搜索。真正提供隔离时，应把三类实现放在同一套受限环境中，并处理好
挂载、凭据、网络和扩展代码的边界。这些约束需要具体适配器和运行环境来执行。

## 与权限 Hook 的关系

```text
before_tool 判断是否允许调用
  → 允许后由 operations 执行
  → after_tool 处理工具结果
```

before_tool 的阻止原因会沿用现有工具结果流程。它可以接入确认 UI 或策略判断，
但不会自动限制一个已经开始的命令的能力。策略确认与操作系统隔离可以分别接入。

## 学习示例

```powershell
uv run python examples/tool_operations.py
```

示例给本机文件操作加上记录功能，观察 write、edit、read 如何经过同一个
RecordingFileOperations；使用临时目录，不调用模型，不执行外部命令。
这是接口使用示例，不是沙箱示例。
