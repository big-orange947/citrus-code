from pathlib import Path

from pydantic import BaseModel, ConfigDict



class ProjectContextFile(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
    )

    path: Path
    content: str


def load_project_context(
        cwd: Path,
) -> list[ProjectContextFile]:
    directory = cwd.expanduser().resolve()

    if not directory.is_dir():
        raise NotADirectoryError(
            f"Project directory does not exist: {directory}"
        )

    path = directory / "AGENTS.md"

    try:
        content = path.read_text(
            encoding="utf-8-sig",
        )
    except FileNotFoundError:
        return []

    if not content.strip():
        return []

    return [
        ProjectContextFile(
            path=path,
            content=content,
        )
    ]
