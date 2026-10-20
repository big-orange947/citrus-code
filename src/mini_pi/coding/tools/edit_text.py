import unicodedata
from bisect import bisect_right
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, Field


class TextEdit(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        strict=True,
    )

    old_text: str = Field(
        alias="oldText",
        min_length=1,
        description=(
            "Exact text to replace. It must match "
            "a unique region of the original file."
        ),
    )

    new_text: str = Field(
        alias="newText",
        description="The replacement text.",
    )


@dataclass(frozen=True)
class MatchedEdit:
    edit_index: int
    start: int
    end: int
    new_text: str


def normalize_to_lf(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def normalize_for_match(text: str) -> str:
    """Pi's fallback: normalize typography and trailing whitespace, not indentation."""
    text = unicodedata.normalize("NFKC", text)
    text = "\n".join(line.rstrip() for line in text.split("\n"))
    replacements = {
        **dict.fromkeys("\u2018\u2019\u201a\u201b", "'"),
        **dict.fromkeys("\u201c\u201d\u201e\u201f", '"'),
        **dict.fromkeys("\u2010\u2011\u2012\u2013\u2014\u2015\u2212", "-"),
        **dict.fromkeys("\u00a0\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a\u202f\u205f\u3000", " "),
    }
    return text.translate(str.maketrans(replacements))


def apply_text_edits(
    content: str,
    edits: list[TextEdit],
    path: str,
) -> str:
    if not edits:
        raise ValueError(
            "edits must contain at least one replacement"
        )

    original = normalize_to_lf(content)
    prepared = [
        (normalize_to_lf(edit.old_text), normalize_to_lf(edit.new_text))
        for edit in edits
    ]
    use_fuzzy = any(old not in original for old, _ in prepared)
    base = normalize_for_match(original) if use_fuzzy else original

    matches: list[MatchedEdit] = []

    # 所有匹配都针对同一份原始内容。
    for edit_index, (old_text, new_text) in enumerate(prepared):
        if use_fuzzy:
            old_text = normalize_for_match(old_text)
        if not old_text:
            raise ValueError(f"edits[{edit_index}].oldText must not be empty in {path}")

        start = base.find(old_text)

        if start == -1:
            raise ValueError(
                f"Could not find edits[{edit_index}].oldText "
                f"in {path}. The text must match exactly."
            )

        # Typography-equivalent duplicates are ambiguous even with one exact hit.
        uniqueness_text = normalize_for_match(base)
        uniqueness_old = normalize_for_match(old_text)
        if not uniqueness_old:
            uniqueness_text, uniqueness_old = base, old_text
        first_start = uniqueness_text.find(uniqueness_old)
        second_start = uniqueness_text.find(uniqueness_old, first_start + 1)

        if second_start != -1:
            raise ValueError(
                f"edits[{edit_index}].oldText is not unique "
                f"in {path}. Provide more surrounding text."
            )

        matches.append(
            MatchedEdit(
                edit_index=edit_index,
                start=start,
                end=start + len(old_text),
                new_text=new_text,
            )
        )

    matches.sort(key=lambda match: match.start)

    # 排序之后，相邻区域就足以用于检测重叠。
    for index in range(1, len(matches)):
        previous = matches[index - 1]
        current = matches[index]

        if previous.end > current.start:
            raise ValueError(
                f"edits[{previous.edit_index}] and "
                f"edits[{current.edit_index}] overlap "
                f"in {path}. Merge them into one edit."
            )

    if not use_fuzzy:
        result = original
        # 从后往前替换，保持前面尚未处理的位置有效。
        for match in reversed(matches):
            result = result[:match.start] + match.new_text + result[match.end:]
    else:
        # Only rewrite lines touched by a normalized match. Untouched lines keep
        # their original typography and trailing spaces.
        original_lines = original.splitlines(keepends=True)
        base_lines = base.splitlines(keepends=True)
        if len(original_lines) != len(base_lines):
            raise ValueError("Normalization changed the line structure")
        offsets = [0]
        for line in base_lines:
            offsets.append(offsets[-1] + len(line))
        groups: list[tuple[int, int, list[MatchedEdit]]] = []
        for match in matches:
            start_line = bisect_right(offsets, match.start) - 1
            end_line = bisect_right(offsets, match.end - 1)
            if groups and start_line < groups[-1][1]:
                previous_start, previous_end, group_matches = groups[-1]
                group_matches.append(match)
                groups[-1] = (previous_start, max(previous_end, end_line), group_matches)
            else:
                groups.append((start_line, end_line, [match]))

        pieces: list[str] = []
        cursor = 0
        for start_line, end_line, group_matches in groups:
            pieces.extend(original_lines[cursor:start_line])
            offset = offsets[start_line]
            block = base[offset:offsets[end_line]]
            for match in reversed(group_matches):
                start, end = match.start - offset, match.end - offset
                block = block[:start] + match.new_text + block[end:]
            pieces.append(block)
            cursor = end_line
        pieces.extend(original_lines[cursor:])
        result = "".join(pieces)

    if result == original:
        raise ValueError(
            f"No changes made to {path}. "
            "The replacements produced identical content."
        )

    return result
