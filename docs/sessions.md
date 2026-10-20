# 会话保存与恢复

`coding/session/` 负责保存消息树、当前分支位置和模型选择。它独立于 Agent 运行时，由 coding 应用层连接两者。

## 运行离线示例

在项目根目录运行：

```powershell
uv run python examples/session_storage.py
```

示例不调用模型：创建会话，保存两个回答分支，选择第一个分支，重新加载，再切换到第二个分支。文件放在临时目录，示例结束后自动清理。

## 基本接口

```python
import time
from pathlib import Path

from mini_pi.ai.types import UserMessage
from mini_pi.coding.session import SessionManager

path = Path("sessions/example.jsonl")

# 创建新文件。文件已存在时抛出 FileExistsError，恢复时使用 load。
session = SessionManager.create(cwd=Path.cwd(), file_path=path)
session.append_model_change(provider="deepseek", model_id="your-model-id")
message_id = session.append_message(
    UserMessage(content="读取项目说明。", timestamp=int(time.time() * 1000))
)

# 每次追加已经写入文件，无需再调用 save。
restored = SessionManager.load(path)
context = restored.build_context()

# context.messages 是所选分支的消息；模型信息由应用层用于选择 Provider。
print(context.messages)
print(context.provider, context.model_id)
```

也可以先建立纯内存会话，再保存：

```python
session = SessionManager(cwd=Path.cwd())
# 可以先 append_message / append_model_change。
session.save(Path("sessions/another-session.jsonl"))
# 此后追加消息和切换分支都会自动写入该文件。
```

`save` 首次创建并绑定文件；同一路径重复调用不会重复写入。已绑定的会话不能用 `save` 切换到另一个文件。

## 分支如何工作

每个消息或模型变更节点都有自己的 `id` 和指向前一个节点的 `parentId`。`append_message` 和 `append_model_change` 返回新节点 ID。

```text
模型选择 → 用户问题 → 回答 A
                  ↘ 回答 B
```

- `checkout(id)`：选中已有节点，后续追加成为该节点的子节点。
- `checkout(None)`：选中空分支，后续追加成为新的根节点。
- `get_entries()`：取得所有分支的树节点，不包含文件头和分支选择记录。
- `get_branch()`：取得从根到当前选中节点的树节点。
- `build_context()`：提取当前分支的消息，以及沿途最后一次选择的模型。
- `build_context_at(id)`：预览指定节点所在分支的上下文，不改变当前选择，也不写入文件；未知 ID 抛出 `ValueError`，`None` 返回空上下文。

应用层切换分支时，先通过 `build_context_at` 检查目标模型和消息，再调用 `checkout` 保存选择，最后更新 Agent 历史。这样检查失败不会改变当前分支。会话文件仍按单写入者使用；这些调用不构成多个线程或进程之间的事务。

切换分支会保存一条 `cursor` 记录。即使切换后没有追加消息，重新打开文件也能恢复选择。切换只影响对话上下文，不会撤销工具已经修改的项目文件。

## 文件与错误处理

使用 UTF-8 JSONL：每行一个 JSON 对象。第一条非空记录为 `session` 文件头，其后可以是 `message`、`model_change`、`compaction` 和 `cursor`。这是 mini-pi 自己的 v1 格式，不支持直接读写 pi 的会话文件。用户消息可带 `taskStart`，记录当前任务的起点。

- 写入前完成数据构造，写入后执行 flush 和 fsync，再更新内存中的当前节点。
- 加载时检查记录格式、版本、重复 ID、父节点和分支选择目标。父节点必须已经出现，因此不能形成环。
- 损坏记录会抛出 `SessionFormatError`，错误包含文件路径和行号；不会自动跳过或删除损坏内容。
- 普通文件访问失败保留原始异常，例如文件不存在或权限不足。
- 已加载的文件被其他写入者修改后，再追加会尽量通过文件版本检查抛出 `SessionConflictError`。一次写入失败后，该存储实例禁止继续追加，需要重新加载并检查文件。

一个会话文件应由一个 manager 写入。进程内使用锁串行处理文件操作；版本检查用于发现过期实例，不提供多个进程同时写入的协调机制。

当前存储接口是同步的，适合在会话控制层调用。未来接入异步交互时，可通过 `asyncio.to_thread` 放到工作线程，并等待写入完成，保持消息顺序。

## 与 Agent 的边界

`CodingSession` 已提供初始化和 `prompt`：初始化时恢复消息并检查模型是否匹配，运行时按事件保存用户消息、已提交的助手消息和工具结果。终端等调用方通过 `CodingSession.prompt` 运行需要保存的对话。

加载保证记录结构和树关系有效。`CodingSession.prompt` 在调用模型前检查工具消息配对。CLI 恢复时调用 `session/recovery.py` 中的 `recover_pending_tools(manager)`，为末尾缺少结果的工具调用补充执行状态未知记录，再创建 `CodingSession`。它不执行原工具，不修复损坏 JSON 或历史中间的配对错误。

CLI 在会话文件旁持有操作系统文件锁，阻止另一个 CLI 同时写入。直接使用 `SessionManager` 的程序仍需自行遵守单写入者约定。启动参数、会话恢复和交互命令见 [CLI 说明](cli.md)。

文件头的 `cwd` 只是原工作目录信息；加载不会改变进程工作目录，也不会自动重建工具实例。

## 压缩记录

`compaction` 是一个普通树节点，保存摘要、按顺序保留的消息 ID 和压缩前后的估计大小。它不会删除或覆盖旧节点。`get_branch()` 返回原始分支记录；`build_context()`、`build_context_at()` 和 `get_context_entries()` 会应用所选分支上的压缩记录，重建模型可见上下文。

`CodingSession` 记录新任务和 follow-up 的任务起点，并在 `context_compacted` 事件到达时保存压缩节点。追加前和加载时会检查保留引用及工具消息配对。摘要的稳定时间戳由记录提供，因此重新加载得到的消息能与 Agent 历史保持一致。

切换到压缩前的节点会恢复该位置的完整上下文；切换到包含压缩节点的分支则使用摘要。具体阈值、保留策略和失败处理见 [上下文压缩说明](compaction.md)。

## 历史消息与当前系统提示

CLI 每次启动都基于工作目录和当前 `AGENTS.md` 组装系统提示。新会话将初始提示保存为系统消息；恢复已有会话时，原系统消息仍留在历史中。

实际调用模型前，`SystemPromptHooks.before_model` 在请求上下文副本中替换系统消息，因此恢复会话或切换分支后仍使用本次启动的提示，普通对话和工具结果保持原有顺序。该 Hook 不修改存储树或 Agent 的历史，也不会为恢复后的当前提示另外保存快照。精确记录每次请求所用的提示属于后续能力，当前 JSONL 保存初始提示、对话、模型选择、压缩和分支选择记录。
