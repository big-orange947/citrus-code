import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock

from prompt_toolkit.completion import CompleteEvent
from prompt_toolkit.document import Document

from citrus_code.agent.hooks import AgentHooks
from citrus_code.agent.agent import Agent
from citrus_code.agent.events import AgentEndEvent
from citrus_code.agent.tool_runner import ToolRunner
from citrus_code.ai.events import DoneEvent
from citrus_code.ai.types import AssistantMessage, ModelContext, ModelSpec, SystemMessage, TextContent, ToolCall, ToolResultMessage, UserMessage
from citrus_code.cli.commands import SlashCommandCompleter
from citrus_code.cli.terminal import InteractiveTerminal
from citrus_code.coding.extensions import ExtensionPreferences
from citrus_code.coding.mcp import MCPManager
from citrus_code.coding.skill_hooks import SkillHooks
from citrus_code.coding.skills import SkillManager


class SkillTests(unittest.IsolatedAsyncioTestCase):
    async def test_discovery_completion_and_explicit_loading(self):
        with tempfile.TemporaryDirectory() as folder:
            cwd = Path(folder)
            path = cwd / ".citrus-code" / "skills" / "review" / "SKILL.md"
            path.parent.mkdir(parents=True)
            path.write_text("---\nname: review\ndescription: Review code\n---\nInspect the code.\n", encoding="utf-8")
            preferences = ExtensionPreferences(cwd)
            skills = SkillManager(cwd, preferences)
            context = ModelContext(messages=[SystemMessage(content="system", timestamp=0), UserMessage(content="hello", timestamp=0)])
            result = await SkillHooks(AgentHooks(), skills).before_model(context)
            self.assertIn("review", result.messages[0].content)
            self.assertEqual(context.messages[0].content, "system")
            self.assertIn("Inspect the code.", skills.expand_mentions("use @review"))
            completion = SlashCommandCompleter(skills=lambda: ((s.name, s.description) for s in skills.enabled()))
            candidates = list(completion.get_completions(Document("use @rev"), CompleteEvent()))
            self.assertEqual([candidate.text for candidate in candidates], ["@review"])
            preferences.set_enabled("skill", "review", False)
            self.assertEqual(skills.expand_mentions("use @review"), "use @review")
            self.assertIn("review", ExtensionPreferences(cwd).disabled_skills)
            external = cwd / "external" / "SKILL.md"
            external.parent.mkdir()
            external.write_text("---\nname: external\ndescription: External skill\n---\nUse it.\n", encoding="utf-8")
            preferences.set_skill_path(external, True)
            skills.reload()
            self.assertIn("external", skills.skills)
            self.assertIn(str(external.resolve()), ExtensionPreferences(cwd).skill_paths)
            preferences.set_skill_path(external, False)
            skills.reload()
            self.assertNotIn("external", skills.skills)
            self.assertTrue(external.exists())

    @unittest.skipUnless(sys.version_info < (3, 10), "Python 3.9 fallback")
    async def test_python39_reports_unavailable_mcp_without_crashing(self):
        with tempfile.TemporaryDirectory() as folder:
            cwd = Path(folder)
            config = cwd / ".citrus-code" / "mcp.json"
            config.parent.mkdir()
            config.write_text(json.dumps({"mcpServers": {"test": {"command": "unused"}}}), encoding="utf-8")
            manager = MCPManager(cwd, ExtensionPreferences(cwd))
            manager.paths = [config]
            await manager.refresh()
            self.assertEqual(manager.tools, [])
            self.assertIn("连接失败", manager.status["test"])
            await manager.close()


@unittest.skipUnless(importlib.util.find_spec("mcp"), "MCP SDK requires Python 3.10+")
class MCPTests(unittest.IsolatedAsyncioTestCase):
    async def test_stdio_stateful_tools_and_disable(self):
        with tempfile.TemporaryDirectory() as folder:
            cwd = Path(folder)
            server = cwd / "server.py"
            server.write_text(
                "from mcp.server.fastmcp import FastMCP\n"
                "app = FastMCP('test')\ncount = 0\n"
                "@app.tool()\ndef counter() -> int:\n global count\n count += 1\n return count\n"
                "if __name__ == '__main__': app.run(transport='stdio')\n",
                encoding="utf-8",
            )
            config = cwd / ".citrus-code" / "mcp.json"
            config.parent.mkdir()
            config.write_text(json.dumps({"mcpServers": {"test": {"command": sys.executable, "args": [str(server)]}}}), encoding="utf-8")
            preferences = ExtensionPreferences(cwd)
            manager = MCPManager(cwd, preferences)
            manager.paths = [config]
            try:
                await manager.refresh()
                self.assertEqual(len(manager.tools), 1, manager.status)
                runner = ToolRunner(manager.tools)
                for number in (1, 2):
                    result = await runner.execute(ToolCall(id=str(number), name="mcp_test_counter", arguments={}))
                    self.assertFalse(result.message.is_error)
                    self.assertEqual(result.message.content[0].text, str(number))

                class ScriptedProvider:
                    id = "fake"

                    async def stream(self, model, context, options=None):
                        results = [m for m in context.messages if isinstance(m, ToolResultMessage)]
                        self.assert_tool_present(context)
                        content = [TextContent(text="done")] if results else [ToolCall(id="agent-call", name="mcp_test_counter", arguments={})]
                        reason = "stop" if results else "toolUse"
                        message = AssistantMessage(content=content, api="fake", provider="fake", model="fake", usage=None, stop_reason=reason, timestamp=0)
                        yield DoneEvent(reason=reason, message=message)

                    def assert_tool_present(self, context):
                        assert any(tool.name == "mcp_test_counter" for tool in context.tools)

                provider = ScriptedProvider()
                model = ModelSpec(id="fake", name="fake", provider="fake", api="fake", base_url="")
                agent = Agent(provider=provider, model=model, tools=manager.tools)
                events = [event async for event in agent.prompt("use the MCP tool")]
                self.assertIsInstance(events[-1], AgentEndEvent)
                self.assertEqual(events[-1].reason, "stop")
                tool_result = next(m for m in agent.state.messages if isinstance(m, ToolResultMessage))
                self.assertEqual(tool_result.content[0].text, "3")
                session = Mock()
                session.has_queued_messages.return_value = False
                terminal = InteractiveTerminal(session, Mock(), mcp=manager)
                await terminal._command("/mcp disable test")
                self.assertEqual(manager.tools, [])
                session.replace_tools.assert_called()
                await terminal._command("/mcp enable test")
                self.assertEqual(len(manager.tools), 1)
            finally:
                await manager.close()

    async def test_read_only_never_launches_servers(self):
        with tempfile.TemporaryDirectory() as folder:
            cwd = Path(folder)
            config = cwd / ".citrus-code" / "mcp.json"
            config.parent.mkdir()
            config.write_text(json.dumps({"mcpServers": {"test": {"command": "nonexistent"}}}), encoding="utf-8")
            manager = MCPManager(cwd, ExtensionPreferences(cwd), read_only=True)
            manager.paths = [config]
            await manager.refresh()
            self.assertEqual(manager.tools, [])
            self.assertEqual(manager.status["test"], "只读工具模式不加载 MCP")
            await manager.close()


if __name__ == "__main__":
    unittest.main()
