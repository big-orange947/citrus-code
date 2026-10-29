from typing import Optional
import asyncio
import base64
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from ...agent.types import ToolExecutionResult
from ...ai.types import ImageContent, TextContent, ToolDefinition
from .local_files import LocalFileOperations
from .operations import FileOperations
from .truncate import DEFAULT_MAX_BYTES, truncate_text

class ReadArguments(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        strict=True
    )
    path: str = Field(
        min_length=1,
        description="The file path, absolute or relative to the working directory.",
    )

    offset: int = Field(
        default=1,
        ge=1,
        description="The first line to read, starting from 1.",
    )

    limit: Optional[int] = Field(
        default=None,
        ge=1,
        description="The maximum number of lines to read.",
    )


class ReadTool:
    name: str = "read"
    description: str = (
        "Read a UTF-8 text file or an image (PNG, JPEG, GIF, WebP, BMP). "
        "Images are returned as attachments. Text is limited to 2000 lines or 50KB, "
        "whichever is hit first. Use offset (1-based) and limit for large files; "
        "continue with the suggested offset to read more."
    )

    def __init__(self, cwd: Path, *, operations: Optional[FileOperations] = None) -> None:
        self._cwd = cwd
        self._ops = LocalFileOperations() if operations is None else operations

    def get_definition(self) -> ToolDefinition:
        return ToolDefinition(
            name=self.name,
            description=self.description,
            parameters=ReadArguments.model_json_schema(),
        )

    async def execute(
        self,
        tool_call_id: str,
        arguments: dict[str, JsonValue],
    ) -> ToolExecutionResult:
        args = ReadArguments.model_validate(arguments)

        path = await asyncio.to_thread(self._ops.resolve_path, args.path, self._cwd)

        return await asyncio.to_thread(
            self._read,
            path,
            args,
        )

    def _read(
        self,
        path: str,
        args: ReadArguments,
    ) -> ToolExecutionResult:
        buffer = self._ops.read_bytes(path)
        mime_type: Optional[str] = None
        if buffer.startswith(b"\x89PNG\r\n\x1a\n"):
            mime_type = "image/png"
        elif buffer.startswith(b"\xff\xd8\xff"):
            mime_type = "image/jpeg"
        elif buffer.startswith((b"GIF87a", b"GIF89a")):
            mime_type = "image/gif"
        elif buffer.startswith(b"RIFF") and buffer[8:12] == b"WEBP":
            mime_type = "image/webp"
        elif buffer.startswith(b"BM"):
            mime_type = "image/bmp"
        if mime_type is not None:
            return ToolExecutionResult(
                content=[
                    TextContent(text=f"Read image file [{mime_type}]: {args.path}"),
                    ImageContent(data=base64.b64encode(buffer).decode("ascii"), mime_type=mime_type),
                ],
                details={"path": str(path), "mimeType": mime_type, "bytes": len(buffer)},
            )
        text = buffer.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")
        # Split only LF: Unicode separators inside source literals are not lines.
        parts = text.split("\n")
        lines = [line + "\n" for line in parts[:-1]]
        if parts[-1]:
            lines.append(parts[-1])
        start = args.offset - 1
        if start >= len(lines) and (lines or start != 0):
            raise ValueError(
                f"Offset {args.offset} is beyond end of file ({len(lines)} lines total)"
            )
        stop = len(lines) if args.limit is None else start + args.limit
        truncation = truncate_text("".join(lines[start:stop]))
        text = truncation.content
        has_more = start + truncation.output_lines < len(lines)
        if truncation.first_line_exceeds_limit:
            text = (
                f"[Line {args.offset} exceeds {DEFAULT_MAX_BYTES} bytes. "
                "Use bash or powershell to inspect a smaller portion of this line.]"
            )
        elif has_more:
            next_offset = args.offset + truncation.output_lines
            text += (
                f"\n\n[Showing {truncation.output_lines} lines starting at {args.offset}. "
                f"Use offset={next_offset} to continue.]"
            )

        details: dict[str, JsonValue] = {
            "path": str(path),
            "offset": args.offset,
            "lines_read": truncation.output_lines,
            "has_more": has_more,
        }
        if truncation.truncated:
            details["truncation"] = truncation.model_dump(mode="json", exclude={"content"})

        return ToolExecutionResult(
            content=[TextContent(text=text)],
            details=details,
        )
