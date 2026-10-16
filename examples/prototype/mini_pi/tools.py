from dataclasses import dataclass
from typing import Callable

from pydantic import BaseModel, ConfigDict, Field


ToolArguments = dict[str, object]


class AddNumbersArgs(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        strict=True,
    )

    a: float = Field(description="The first number.")
    b: float = Field(description="The second number.")


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    parameters: dict[str, object]
    execute: Callable[[ToolArguments], str]


def add_numbers(arguments: ToolArguments) -> str:
    args = AddNumbersArgs.model_validate(arguments)
    return str(args.a + args.b)


add_numbers_tool = Tool(
    name="add_numbers",
    description="Add two numbers together.",
    parameters=AddNumbersArgs.model_json_schema(),
    execute=add_numbers,
)