import json

from .config import ModelConfig
from .model_client import complete
from .tools import Tool


Message=dict[str,object]


def run_agent(
        config: ModelConfig,
        prompt: str,
        tools: list[Tool],
        max_steps: int =8,
)->str:
    messages: list[Message] =[
        {
            "role":"user",
            "content":prompt,
        }
    ]
    tools_by_name={
        tool.name:tool,
        for tool in tools
    }
    for _ in range(max_steps):
        response=complete(config,messages,tools)

        if not response.tool_calls:
            return response.text or ""

        messages.append(
            {
                "role":"assistant",
                "content":response.text,
                "tool_calls":[
                    {
                        "id":tool_call.id,
                        "type":"function",
                        "function":{
                            "name":tool_call.name,
                            "arguments":json.dumps(tool_call.argumnets),
                        },
                    }
                    for tool_call in response.tool_calls
                ],
            }
        )

        for tool_call in response.tool_calls:
            tool=tools_by_name.get(tool_call.name)

            if tool is None:
                result=f"Unknown tool:{tool_call.name}"
            else:
                try:
                    result=tool.execute(tool_call.arguments)
                except Exception as error:
                    result=f"Tool error:{error}"

            messages.append(
                {
                    "role":"tool",
                    "tool_call_id":tool_call.id,
                    "content":result,
                }
            )
        raise RuntimeError(f"Agent stopped after {max_steps} model calls")