from typing import Optional
import asyncio
import difflib
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from ...agent.types import ToolExecutionResult
from ...ai.types import TextContent, ToolDefinition
from .edit_text import TextEdit, apply_text_edits, normalize_to_lf
from .local_files import LocalFileOperations
from .operations import FileOperations


class EditArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    path: str = Field(min_length=1, description="File to edit, relative or absolute.")
    edits: list[TextEdit] = Field(
        min_length=1,
        description=(
            "Targeted replacements matched against the original file. "
            "Each oldText must be unique. Regions must not overlap; "
            "merge nearby changes into one edit."
        ),
    )


class EditTool:
    name: str = "edit"
    description: str = (
        "Edit one file using exact text replacement. Each edits[].oldText must "
        "match a unique, non-overlapping region of the original file. "
        "Use multiple edits for separate locations; merge overlapping changes."
    )

    def __init__(self, cwd: Path, *, operations: Optional[FileOperations] = None) -> None:
        self._cwd = cwd
        self._ops = LocalFileOperations() if operations is None else operations

    def get_definition(self) -> ToolDefinition:
        return ToolDefinition(
            name=self.name,
            description=self.description,
            parameters=EditArguments.model_json_schema(by_alias=True),
        )

    async def execute(
        self,
        tool_call_id: str,
        arguments: dict[str, JsonValue],
    ) -> ToolExecutionResult:
        args = EditArguments.model_validate(arguments)
        path = await asyncio.to_thread(self._ops.resolve_path, args.path, self._cwd)
        return await asyncio.to_thread(self._edit, path, args)

    def _edit(self, path: str, args: EditArguments) -> ToolExecutionResult:
        with self._ops.mutation(path):
            raw = self._ops.read_bytes(path).decode("utf-8")
            bom = "\ufeff" if raw.startswith("\ufeff") else ""
            text = raw[len(bom):]
            first_newline = text.find("\n")
            ending = (
                "\r\n"
                if first_newline > 0 and text[first_newline - 1] == "\r"
                else "\n"
            )
            original = normalize_to_lf(text)
            updated = apply_text_edits(original, args.edits, args.path)

            old_lines = original.splitlines(keepends=True)
            new_lines = updated.splitlines(keepends=True)
            patch_parts: list[str] = []
            for line in difflib.unified_diff(
                old_lines, new_lines, fromfile=args.path, tofile=args.path, n=4
            ):
                patch_parts.append(line)
                if not line.endswith("\n"):
                    patch_parts.append("\n\\ No newline at end of file\n")
            patch = "".join(patch_parts)

            first_changed_line = 1
            matcher = difflib.SequenceMatcher(a=old_lines, b=new_lines, autojunk=False)
            for tag, _, _, new_start, _ in matcher.get_opcodes():
                if tag != "equal":
                    first_changed_line = new_start + 1
                    break

            # Match and prepare the report before touching the file.
            final = bom + updated.replace("\n", ending)
            self._ops.write_bytes(path, final.encode("utf-8"))

        return ToolExecutionResult(
            content=[TextContent(
                text=f"Successfully replaced {len(args.edits)} block(s) in {args.path}."
            )],
            details={
                "path": str(path),
                "diff": patch,
                "patch": patch,
                "firstChangedLine": first_changed_line,
            },
        )
