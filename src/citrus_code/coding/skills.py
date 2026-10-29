"""Discover Agent Skills and expand explicit @skill-name mentions."""
from dataclasses import dataclass
from pathlib import Path
import re
from xml.sax.saxutils import escape

import yaml

from .extensions import ExtensionPreferences


_NAME = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_MENTION = re.compile(r"(?<![\w@])@([a-z0-9]+(?:-[a-z0-9]+)*)(?![\w-])")
_MAX_SKILL_BYTES = 256 * 1024


@dataclass(frozen=True)
class Skill:
    name: str
    description: str
    path: Path
    disable_model_invocation: bool = False


class SkillManager:
    def __init__(self, cwd: Path, preferences: ExtensionPreferences) -> None:
        self.cwd = cwd.resolve()
        self.preferences = preferences
        self.skills: dict[str, Skill] = {}
        self.diagnostics: list[str] = []
        self.reload()

    def reload(self) -> None:
        skills: dict[str, Skill] = {}
        diagnostics: list[str] = []
        custom_roots = [Path(path) for path in self.preferences.skill_paths]
        roots = [
            self.cwd / ".citrus-code" / "skills", self.cwd / ".agents" / "skills",
            Path.home() / ".citrus-code" / "skills", Path.home() / ".agents" / "skills",
        ] + custom_roots
        for root in roots:
            if root.is_file() and root.name == "SKILL.md":
                files = [root]
            elif root.is_dir():
                files = sorted(root.rglob("SKILL.md"))
            else:
                if root in custom_roots:
                    diagnostics.append(f"Skill 路径不存在：{root}")
                continue
            for path in files:
                try:
                    if path.stat().st_size > _MAX_SKILL_BYTES:
                        raise ValueError("文件过大")
                    text = path.read_text(encoding="utf-8-sig")
                    lines = text.splitlines()
                    if not lines or lines[0].strip() != "---":
                        raise ValueError("缺少 YAML frontmatter")
                    end = next(i for i in range(1, len(lines)) if lines[i].strip() == "---")
                    metadata = yaml.safe_load("\n".join(lines[1:end]))
                    if not isinstance(metadata, dict):
                        raise ValueError("frontmatter 必须是对象")
                    name, description = metadata.get("name"), metadata.get("description")
                    if not isinstance(name, str) or len(name) > 64 or not _NAME.fullmatch(name):
                        raise ValueError("name 必须是小写字母、数字或连字符")
                    if not isinstance(description, str) or not description.strip() or len(description) > 1024:
                        raise ValueError("description 必须是非空文本且不超过 1024 字符")
                    if name in skills:
                        diagnostics.append(f"Skill 名称重复：{name}，保留 {skills[name].path}")
                        continue
                    hidden = metadata.get("disable-model-invocation", False)
                    if not isinstance(hidden, bool):
                        raise ValueError("disable-model-invocation 必须是布尔值")
                    skills[name] = Skill(name, description.strip(), path.resolve(), hidden)
                except (OSError, UnicodeError, ValueError, StopIteration, yaml.YAMLError) as exc:
                    diagnostics.append(f"无法加载 Skill {path}：{type(exc).__name__}")
        self.skills = skills
        self.diagnostics = diagnostics

    def enabled(self) -> list[Skill]:
        return [skill for name, skill in self.skills.items() if name not in self.preferences.disabled_skills]

    def catalog(self) -> str:
        skills = [s for s in self.enabled() if not s.disable_model_invocation]
        if not skills:
            return ""
        entries = [
            f"<skill><name>{escape(s.name)}</name><description>{escape(s.description)}</description>"
            f"<location>{escape(str(s.path))}</location></skill>" for s in skills
        ]
        return (
            "以下是可用 Skill。任务匹配时，先用 read 工具读取对应 SKILL.md；"
            "其中的相对路径以该文件所在目录为准。\n<available_skills>\n"
            + "\n".join(entries) + "\n</available_skills>"
        )

    def expand_mentions(self, text: str) -> str:
        selected: list[Skill] = []
        seen: set[str] = set()
        for match in _MENTION.finditer(text):
            name = match.group(1)
            if name in self.skills and name not in self.preferences.disabled_skills and name not in seen:
                selected.append(self.skills[name])
                seen.add(name)
        if not selected:
            return text
        sections = [text]
        for skill in selected:
            if skill.path.stat().st_size > _MAX_SKILL_BYTES:
                raise ValueError(f"Skill 文件过大：{skill.name}")
            content = skill.path.read_text(encoding="utf-8-sig")
            sections.append(
                f"用户指定了 Skill {skill.name}。相对路径以 {skill.path.parent} 为基准。\n"
                f"<skill-instructions name=\"{skill.name}\">\n{content}\n</skill-instructions>"
            )
        return "\n\n".join(sections)
