from typing import Optional
from typing import Literal

from pydantic import BaseModel, ConfigDict


DEFAULT_MAX_LINES = 2000
DEFAULT_MAX_BYTES = 50 * 1024
GREP_MAX_LINE_LENGTH = 500


class TruncationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: str
    truncated: bool
    truncated_by: Optional[Literal['lines', 'bytes']]
    total_lines: int
    total_bytes: int
    output_lines: int
    output_bytes: int
    first_line_exceeds_limit: bool = False
    last_line_partial: bool = False


def truncate_text(
    content: str,
    *,
    tail: bool = False,
    max_lines: int = DEFAULT_MAX_LINES,
    max_bytes: int = DEFAULT_MAX_BYTES,
) -> TruncationResult:
    """Keep complete lines, with a UTF-8-safe partial last line for shell tails."""
    if max_lines < 1 or max_bytes < 1:
        raise ValueError("Output limits must be positive")

    lines = content.split("\n") if content else []
    if content.endswith("\n"):
        lines.pop()
    total_bytes = len(content.encode("utf-8"))
    if len(lines) <= max_lines and total_bytes <= max_bytes:
        return TruncationResult(
            content=content,
            truncated=False,
            truncated_by=None,
            total_lines=len(lines),
            total_bytes=total_bytes,
            output_lines=len(lines),
            output_bytes=total_bytes,
        )

    selected: list[str] = []
    size = 0
    reason: Literal["lines", "bytes"] = "lines"
    partial = False
    first_too_large = False
    candidates = reversed(lines) if tail else iter(lines)
    for line in candidates:
        if len(selected) >= max_lines:
            break
        line_bytes = line.encode("utf-8")
        cost = len(line_bytes) + (1 if selected else 0)
        if size + cost > max_bytes:
            reason = "bytes"
            if not selected:
                if tail:
                    selected.append(line_bytes[-max_bytes:].decode("utf-8", errors="ignore"))
                    partial = True
                else:
                    first_too_large = True
            break
        selected.append(line)
        size += cost

    if tail:
        selected.reverse()
    output = "\n".join(selected)
    return TruncationResult(
        content=output,
        truncated=True,
        truncated_by=reason,
        total_lines=len(lines),
        total_bytes=total_bytes,
        output_lines=len(selected),
        output_bytes=len(output.encode("utf-8")),
        first_line_exceeds_limit=first_too_large,
        last_line_partial=partial,
    )
