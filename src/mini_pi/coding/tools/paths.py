from pathlib import Path


def resolve_to_cwd(value: str, cwd: Path) -> Path:
    """Resolve model-supplied paths against the tool's working directory."""
    if value.startswith("@"):
        value = value[1:]
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = cwd / path
    return path.resolve()
