from .config import ModelConfig
from .model_client import complete
from .tools import add_numbers_tool


def main() -> None:
    config=ModelConfig.from_env()

    answer=run_agent(
        config=config,
        prompt="请使用add_numbers工具计算123加456。",
        tools=[add_numbers_tool],
    )
    print(answer)


if __name__ == "__main__":
    main()