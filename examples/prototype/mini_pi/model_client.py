import json
from dataclasses import dataclass
from urllib.request import Request, urlopen

from .config import ModelConfig
from .tools import Tool


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, object]


@dataclass(frozen=True)
class ModelResponse:
    text: str | None
    tool_calls: list[ToolCall]


def complete(
    config: ModelConfig,
    messages: list[dict[str, object]],
    tools: list[Tool] | None = None,
) -> ModelResponse:
    request_body: dict[str, object] = {
        "model": config.model,
        "messages": messages,
    }

    if tools:
        request_body["tools"] = [
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.parameters,
                },
            }
            for tool in tools
        ]
        request_body["tool_choice"] = "auto"

    request_data = json.dumps(request_body).encode("utf-8")

    headers = {
        "Content-Type": "application/json",
    }
    if config.api_key:
        headers["Authorization"] = f"Bearer {config.api_key}"

    request = Request(
        url=f"{config.base_url}/chat/completions",
        data=request_data,
        headers=headers,
        method="POST",
    )

    with urlopen(request, timeout=config.timeout_seconds) as response:
        response_data = json.loads(response.read())

    message = response_data["choices"][0]["message"]
    text = message.get("content")

    if text is not None and not isinstance(text, str):
        raise ValueError("The model response content is not text")

    tool_calls: list[ToolCall] = []

    for raw_tool_call in message.get("tool_calls") or []:
        function = raw_tool_call["function"]
        arguments = json.loads(function["arguments"])

        if not isinstance(arguments, dict):
            raise ValueError("Tool arguments must be a JSON object")

        tool_calls.append(
            ToolCall(
                id=raw_tool_call["id"],
                name=function["name"],
                arguments=arguments,
            )
        )

    return ModelResponse(
        text=text,
        tool_calls=tool_calls,
    )