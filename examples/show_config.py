from mini_pi.config import load_connection_config


def main() -> None:
    config = load_connection_config()

    print("提供者：", config.provider_id)
    print("模型：", config.model_id)
    print("基础地址：", config.base_url)
    print("API Key：", config.api_key)


if __name__ == "__main__":
    main()