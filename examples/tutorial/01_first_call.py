import json
from pathlib import Path

import httpx


def load_config(path: Path) -> dict[str, str]:
    text = path.read_text(encoding="utf-8-sig")
    data = json.loads(text)

    if not isinstance(data, dict):
        raise ValueError("配置文件的最外层必须是 JSON 对象。")

    config: dict[str, str] = {}
    for name in ("base_url", "api_key", "model"):
        value = data.get(name)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"请在配置文件中填写非空字符串：{name}")
        config[name] = value.strip()

    return config


def ask_model(config: dict[str, str], question: str) -> str:
    url = config["base_url"].rstrip("/") + "/chat/completions"
    headers = {"Authorization": f"Bearer {config['api_key']}"}
    messages = [{"role": "user", "content": question}]
    body = {
        "model": config["model"],
        "messages": messages,
        "stream": False,
        "max_tokens": 512,
        "thinking": {"type": "disabled"},
    }

    with httpx.Client(timeout=httpx.Timeout(120.0, connect=10.0)) as client:
        response = client.post(url, headers=headers, json=body)
        response.raise_for_status()
        data = response.json()

    choice = data["choices"][0]
    if choice["finish_reason"] != "stop":
        raise RuntimeError(
            f"本章需要完整的文字回答，实际结束原因：{choice['finish_reason']}"
        )

    content = choice["message"]["content"]
    if not isinstance(content, str) or not content.strip():
        raise RuntimeError("服务没有返回可显示的文字回答。")

    return content


def main() -> None:
    project_root = Path(__file__).resolve().parents[2]
    config_path = project_root / ".citrus-code" / "tutorial.json"
    config = load_config(config_path)

    question = "用一句话解释什么是 ReAct。"
    print("问题：", question)
    print("正在等待模型回答……")

    answer = ask_model(config, question)
    print("回答：", answer)


if __name__ == "__main__":
    main()
