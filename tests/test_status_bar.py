import json
import unittest
from types import SimpleNamespace

from citrus_code.agent.events import AssistantMessageCommittedEvent
from citrus_code.ai.events import DoneEvent
from citrus_code.ai.providers.chat_completions import stream_assistant_events
from citrus_code.ai.types import AssistantMessage, ModelSpec, Usage
from citrus_code.cli.render import Console
from citrus_code.cli.terminal import InteractiveTerminal


def model(name="DeepSeek V4.1 Flash"):
    return ModelSpec(id="deepseek-flash", name=name, provider="deepseek", api="chat-completions", base_url="https://example.test")


def message(usage):
    return AssistantMessage(content=[], api="chat-completions", provider="deepseek", model="deepseek-flash",
                            usage=usage, stop_reason="stop", timestamp=0)


class StatusBarTest(unittest.TestCase):
    def setUp(self):
        self.console = Console()
        self.console.set_model(model(), None)
        session = SimpleNamespace(compaction=None, has_queued_messages=lambda: False)
        self.terminal = InteractiveTerminal(session, self.console)

    def toolbar(self):
        return "".join(text for _, text in self.terminal._toolbar())

    def test_model_name_cache_and_missing_data(self):
        self.assertIn("DeepSeek V4.1 Flash", self.toolbar())
        self.assertNotIn("缓存命中", self.toolbar())
        self.assertNotIn("PgUp", self.toolbar())
        usage = Usage(input_tokens=100, output_tokens=10, total_tokens=110, cache_read_tokens=75)
        self.console.event(AssistantMessageCommittedEvent(message=message(usage)))
        self.assertIn("缓存命中率 75.0%", self.toolbar())
        self.console.event(AssistantMessageCommittedEvent(message=message(Usage(input_tokens=100, output_tokens=0, total_tokens=100))))
        self.assertNotIn("缓存命中", self.toolbar())

    def test_zero_and_model_switch(self):
        usage = Usage(input_tokens=100, output_tokens=0, total_tokens=100, cache_read_tokens=0)
        self.console.event(AssistantMessageCommittedEvent(message=message(usage)))
        self.assertIn("缓存命中率 0.0%", self.toolbar())
        self.console.set_model(model("Another model"), None)
        self.assertIn("Another model", self.toolbar())
        self.assertNotIn("缓存命中", self.toolbar())


class CacheUsageParsingTest(unittest.IsolatedAsyncioTestCase):
    async def test_openai_and_deepseek_cache_fields(self):
        for details, expected in (({"prompt_tokens_details": {"cached_tokens": 40}}, 40),
                                  ({"prompt_cache_hit_tokens": 25, "prompt_tokens_details": {"cached_tokens": 40}}, 25),
                                  ({}, None)):
            with self.subTest(details=details):
                async def chunks():
                    yield json.dumps({"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]})
                    yield json.dumps({"choices": [], "usage": {"prompt_tokens": 100, "completion_tokens": 1, "total_tokens": 101, **details}})
                events = [event async for event in stream_assistant_events(model(), chunks())]
                done = next(event for event in events if isinstance(event, DoneEvent))
                self.assertEqual(done.message.usage.cache_read_tokens, expected)


if __name__ == "__main__":
    unittest.main()
