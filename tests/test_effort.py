import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

import httpx
from prompt_toolkit.completion import CompleteEvent
from prompt_toolkit.document import Document

from citrus_code.ai.events import DoneEvent
from citrus_code.ai.types import AssistantMessage, ModelContext, ToolDefinition, UserMessage
from citrus_code.cli.commands import SlashCommandCompleter
from citrus_code.cli.models import ModelController
from citrus_code.cli.render import Console
from citrus_code.cli.terminal import InteractiveTerminal
from citrus_code.settings import SettingsStore


class EffortTest(unittest.IsolatedAsyncioTestCase):
    async def test_request_changes_and_reasoning_history(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            settings_path = root / "settings.json"
            settings_path.write_text(json.dumps({"providers": {"deepseek": {
                "base_url": "https://api.deepseek.com", "auth": "none",
                "extra_body": {"thinking": {"type": "disabled"}},
                "models": [{"id": "deepseek-flash"}],
            }}}), encoding="utf-8")
            requests = []

            def handle(request):
                requests.append(json.loads(request.content))
                chunks = [
                    {"choices": [{"index": 0, "delta": {"reasoning_content": "reason"}, "finish_reason": None}]},
                    {"choices": [{"index": 0, "delta": {"content": "done"}, "finish_reason": "stop"}]},
                ]
                return httpx.Response(200, text="".join("data: " + json.dumps(chunk) + "\n\n" for chunk in chunks) + "data: [DONE]\n\n",
                                      headers={"content-type": "text/event-stream"})

            async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
                controller = ModelController(SettingsStore(root, settings_path), client, root, [], [], 100)
                model = controller.resolve()
                provider = controller.provider(model)
                history = AssistantMessage(content=[], api=model.api, provider=model.provider, model=model.id,
                                           usage=None, stop_reason="stop", timestamp=0)
                context = ModelContext(messages=[UserMessage(content="hello", timestamp=0), history],
                                       tools=[ToolDefinition(name="test", description="test", parameters={"type": "object"})])
                for level in ("max", "low", "high"):
                    controller.set_effort(model, level)
                    events = [event async for event in provider.stream(model, context)]
                    done = next(event for event in events if isinstance(event, DoneEvent))
                    self.assertEqual(done.message.reasoning_content, "reason")
                    self.assertEqual(requests[-1]["thinking"], {"type": "enabled"})
                    self.assertEqual(requests[-1]["reasoning_effort"], level)
                    self.assertEqual(requests[-1]["messages"][-1]["reasoning_content"], "")
                history.reasoning_content = "previous reason"
                events = [event async for event in provider.stream(model, context)]
                self.assertTrue(any(isinstance(event, DoneEvent) for event in events))
                self.assertEqual(requests[-1]["messages"][-1]["reasoning_content"], "previous reason")
                self.assertEqual(controller.effort_for(model.model_copy(update={"base_url": "https://relay.example"})), None)

    async def test_command_menu_and_colored_status(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            path = root / "settings.json"
            path.write_text(json.dumps({"providers": {"deepseek": {"base_url": "https://api.deepseek.com",
                         "auth": "none", "models": [{"id": "deepseek-flash"}]}}}), encoding="utf-8")
            async with httpx.AsyncClient() as client:
                controller = ModelController(SettingsStore(root, path), client, root, [], [], 100)
                model = controller.resolve()
                console = Console()
                console.set_model(model, 100)
                session = SimpleNamespace(model=model, compaction=None, has_queued_messages=lambda: False)
                terminal = InteractiveTerminal(session, console, models=controller)
                self.assertEqual(controller.effort, "max")
                self.assertIn(("class:effort.max", "effort · max"), terminal._toolbar())
                await terminal._command("/effort")
                self.assertTrue(terminal._show_effort_menu)
                completions = SlashCommandCompleter().get_completions(Document("/effort "), CompleteEvent())
                self.assertEqual([item.text for item in completions], ["low", "high", "max"])
                await terminal._command("/effort low")
                self.assertIn(("class:effort.low", "effort · low"), terminal._toolbar())
                with self.assertRaises(ValueError):
                    await terminal._command("/effort invalid")
                terminal._active = object()
                with self.assertRaises(ValueError):
                    await terminal._command("/effort high")
                self.assertEqual(controller.effort, "low")


if __name__ == "__main__":
    unittest.main()
