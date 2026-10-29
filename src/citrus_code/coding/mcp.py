"""MCP server configuration and adapters for the Agent tool interface."""
import asyncio
from contextlib import asynccontextmanager
import hashlib
import json
import os
from pathlib import Path
import re
from typing import AsyncIterator, Literal, Optional
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel, ConfigDict, Field, JsonValue, SecretStr, ValidationError, model_validator

from ..agent.types import AgentTool, ToolExecutionResult
from ..ai.types import ImageContent, TextContent, ToolContent, ToolDefinition
from .extensions import ExtensionPreferences


class ServerConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    command: Optional[str] = None
    args: list[str] = Field(default_factory=list)
    env: dict[str, SecretStr] = Field(default_factory=dict)
    url: Optional[str] = None
    headers: dict[str, SecretStr] = Field(default_factory=dict)
    enabled: bool = True
    type: Optional[Literal["stdio", "http", "streamable-http"]] = None

    @model_validator(mode="after")
    def check_transport(self) -> "ServerConfig":
        if bool(self.command) == bool(self.url):
            raise ValueError("必须设置 command 或 url 中的一项")
        if (self.type == "stdio" and not self.command) or (self.type in ("http", "streamable-http") and not self.url):
            raise ValueError("type 与连接参数不匹配")
        if self.url:
            parsed = urlsplit(self.url)
            if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password:
                raise ValueError("url 必须是 HTTP(S) 地址，不能包含用户名或密码")
            if self.args or self.env:
                raise ValueError("HTTP 服务不支持 args 或 env")
        elif self.headers:
            raise ValueError("stdio 服务不支持 headers")
        return self


@asynccontextmanager
async def open_server(config: ServerConfig) -> AsyncIterator[object]:
    try:
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client
        from mcp.client.streamable_http import streamable_http_client
    except ImportError:
        raise RuntimeError("MCP 需要 Python 3.10 及以上并安装 mcp 依赖。") from None

    if config.command:
        params = StdioServerParameters(
            command=config.command, args=config.args,
            env={key: value.get_secret_value() for key, value in config.env.items()},
        )
        with open(os.devnull, "w", encoding="utf-8") as errors:
            async with stdio_client(params, errlog=errors) as streams:
                async with ClientSession(streams[0], streams[1]) as session:
                    await asyncio.wait_for(session.initialize(), timeout=30)
                    yield session
    else:
        headers = {key: value.get_secret_value() for key, value in config.headers.items()}
        async with httpx.AsyncClient(headers=headers, timeout=httpx.Timeout(60.0, connect=10.0)) as client:
            async with streamable_http_client(config.url, http_client=client) as streams:
                async with ClientSession(streams[0], streams[1]) as session:
                    await asyncio.wait_for(session.initialize(), timeout=30)
                    yield session


def tool_name(server: str, remote_name: str) -> str:
    raw = f"mcp_{server}_{remote_name}"
    safe = re.sub(r"[^a-zA-Z0-9_-]", "_", raw)
    if safe == raw and len(safe) <= 64:
        return safe
    suffix = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:8]
    return safe[:55] + "_" + suffix


class MCPConnection:
    """Own the MCP transport in one task and serialize calls through it."""

    def __init__(self, config: ServerConfig) -> None:
        self.config = config
        self.ready: asyncio.Future = asyncio.get_running_loop().create_future()
        self.queue: asyncio.Queue = asyncio.Queue()
        self.task = asyncio.create_task(self._run())

    async def _run(self) -> None:
        active: Optional[asyncio.Future] = None
        try:
            async with open_server(self.config) as session:
                cursor = None
                remote_tools = []
                cursors: set[str] = set()
                while True:
                    page = await asyncio.wait_for(session.list_tools(cursor=cursor), timeout=30)
                    remote_tools.extend(page.tools)
                    cursor = page.nextCursor
                    if cursor is None:
                        break
                    if cursor in cursors:
                        raise ValueError("MCP 工具列表分页游标重复")
                    cursors.add(cursor)
                self.ready.set_result(remote_tools)
                while True:
                    item = await self.queue.get()
                    if item is None:
                        break
                    name, arguments, future = item
                    if future.cancelled():
                        continue
                    active = future
                    operation = asyncio.create_task(session.call_tool(name, arguments))
                    future.add_done_callback(lambda done, task=operation: task.cancel() if done.cancelled() else None)
                    try:
                        result = await operation
                        if not future.done():
                            future.set_result(result)
                    except asyncio.CancelledError:
                        if not future.cancelled():
                            raise
                    except Exception as exc:
                        if not future.done():
                            future.set_exception(exc)
                    finally:
                        if not future.done():
                            future.set_exception(RuntimeError("MCP 连接中断，工具执行状态未知"))
                        active = None
        except BaseException as exc:
            if not self.ready.done():
                if isinstance(exc, asyncio.CancelledError):
                    self.ready.cancel()
                else:
                    self.ready.set_exception(exc)
            raise
        finally:
            if active is not None and not active.done():
                active.set_exception(RuntimeError("MCP 连接中断，工具执行状态未知"))
            while not self.queue.empty():
                item = self.queue.get_nowait()
                if item is not None and not item[2].done():
                    item[2].set_exception(RuntimeError("MCP 连接已关闭"))

    async def start(self) -> list:
        try:
            return await asyncio.wait_for(asyncio.shield(self.ready), timeout=45)
        except BaseException:
            await self.close()
            raise

    async def call(self, name: str, arguments: dict[str, JsonValue]) -> object:
        if self.task.done():
            raise RuntimeError("MCP 连接已关闭，请执行 /mcp reload。")
        future = asyncio.get_running_loop().create_future()
        await self.queue.put((name, arguments, future))
        return await asyncio.wait_for(future, timeout=120)

    async def close(self) -> None:
        if not self.task.done():
            await self.queue.put(None)
            try:
                await asyncio.wait_for(asyncio.shield(self.task), timeout=5)
            except asyncio.TimeoutError:
                self.task.cancel()
            except Exception:
                pass
        await asyncio.gather(self.task, return_exceptions=True)


class MCPTool:
    def __init__(self, server: str, connection: MCPConnection, definition: ToolDefinition, remote_name: str) -> None:
        self.server, self.connection, self.definition, self.remote_name = server, connection, definition, remote_name
        self.name, self.description = definition.name, definition.description

    def get_definition(self) -> ToolDefinition:
        return self.definition.model_copy(deep=True)

    async def execute(self, tool_call_id: str, arguments: dict[str, JsonValue]) -> ToolExecutionResult:
        result = await self.connection.call(self.remote_name, arguments)
        content: list[ToolContent] = []
        for block in result.content:
            if block.type == "text":
                text = block.text[:50_000]
                if len(block.text) > 50_000:
                    text += "\n[MCP 文本结果已截断]"
                content.append(TextContent(text=text))
            elif block.type == "image":
                if len(block.data) > 16 * 1024 * 1024:
                    raise ValueError("MCP 图片结果超过大小限制")
                content.append(ImageContent(data=block.data, mimeType=block.mimeType))
            elif block.type == "resource" and isinstance(getattr(block.resource, "text", None), str):
                text = block.resource.text
                content.append(TextContent(text=text[:50_000] + ("\n[MCP 资源内容已截断]" if len(text) > 50_000 else "")))
            elif block.type == "resource_link":
                content.append(TextContent(text=f"[MCP 资源] {block.name}: {block.uri}"))
            else:
                content.append(TextContent(text=f"[MCP {block.type} 内容未转换]"))
        if result.isError:
            detail = "\n".join(item.text for item in content if isinstance(item, TextContent))
            raise RuntimeError(detail or "MCP 工具返回错误")
        if not content and result.structuredContent is not None:
            content.append(TextContent(text=json.dumps(result.structuredContent, ensure_ascii=False)[:50_000]))
        if not content:
            content.append(TextContent(text="MCP 工具没有返回内容。"))
        return ToolExecutionResult(content=content, details={"mcp_server": self.server, "mcp_tool": self.remote_name})


class MCPManager:
    def __init__(self, cwd: Path, preferences: ExtensionPreferences, *, read_only: bool = False) -> None:
        self.cwd, self.preferences = cwd, preferences
        self.read_only = read_only
        self.paths = [Path.home() / ".citrus-code" / "mcp.json", cwd / ".citrus-code" / "mcp.json"]
        self.servers: dict[str, ServerConfig] = {}
        self.tools: list[AgentTool] = []
        self.status: dict[str, str] = {}
        self.connections: list[MCPConnection] = []

    async def close(self) -> None:
        await asyncio.gather(*(connection.close() for connection in self.connections), return_exceptions=True)
        self.connections = []

    def edit_project_server(self, name: str, url: Optional[str]) -> None:
        if not re.fullmatch(r"[a-zA-Z0-9_-]{1,40}", name):
            raise ValueError("服务名称只支持字母、数字、下划线和连字符。")
        path = self.paths[-1]
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8-sig"))
                if not isinstance(data, dict) or set(data) != {"mcpServers"} or not isinstance(data["mcpServers"], dict):
                    raise ValueError("invalid config")
            except (ValueError, UnicodeError):
                raise ValueError(f"MCP 配置无效：{path}") from None
        else:
            data = {"mcpServers": {}}
        servers = data["mcpServers"]
        if url is None:
            if name not in servers:
                raise ValueError("项目配置中没有这个服务；全局配置需要手动编辑。")
            del servers[name]
        else:
            if name in servers:
                raise ValueError("项目配置中已存在这个服务。")
            ServerConfig(url=url)
            servers[name] = {"url": url}
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + ".tmp")
        try:
            temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)

    def load_config(self) -> dict[str, ServerConfig]:
        servers: dict[str, ServerConfig] = {}
        for path in dict.fromkeys(self.paths):
            if not path.exists():
                continue
            try:
                data = json.loads(path.read_text(encoding="utf-8-sig"))
                if not isinstance(data, dict) or set(data) != {"mcpServers"} or not isinstance(data["mcpServers"], dict):
                    raise ValueError("需要 mcpServers 对象")
                for name, item in data["mcpServers"].items():
                    if not isinstance(name, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,40}", name):
                        raise ValueError("服务名格式无效")
                    servers[name] = ServerConfig.model_validate(item)
            except (ValueError, UnicodeError, ValidationError):
                raise ValueError(f"MCP 配置无效：{path}") from None
        return servers

    async def refresh(self) -> None:
        servers = self.load_config()
        tools: list[AgentTool] = []
        status: dict[str, str] = {}
        seen: set[str] = set()
        connections: list[MCPConnection] = []
        try:
            for name, config in servers.items():
                if self.read_only:
                    status[name] = "只读工具模式不加载 MCP"
                    continue
                if not config.enabled or name in self.preferences.disabled_mcp:
                    status[name] = "已禁用"
                    continue
                connection = MCPConnection(config)
                try:
                    remote_tools = await connection.start()
                    server_tools = []
                    server_names: set[str] = set()
                    for remote in remote_tools:
                        name_in_agent = tool_name(name, remote.name)
                        if name_in_agent in seen or name_in_agent in server_names:
                            raise ValueError("MCP 工具名冲突")
                        definition = ToolDefinition(
                            name=name_in_agent, description=remote.description or f"MCP tool {remote.name}",
                            parameters=remote.inputSchema,
                        )
                        server_tools.append(MCPTool(name, connection, definition, remote.name))
                        server_names.add(name_in_agent)
                    tools.extend(server_tools)
                    seen.update(server_names)
                    connections.append(connection)
                    status[name] = f"可用（{len(server_tools)} 个工具）"
                except Exception as exc:
                    await connection.close()
                    status[name] = f"连接失败：{type(exc).__name__}"
                except BaseException:
                    await connection.close()
                    raise
        except BaseException:
            await asyncio.gather(*(connection.close() for connection in connections), return_exceptions=True)
            raise
        old_connections = self.connections
        self.servers, self.tools, self.status, self.connections = servers, tools, status, connections
        await asyncio.gather(*(connection.close() for connection in old_connections), return_exceptions=True)
