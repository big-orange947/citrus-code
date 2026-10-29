"""Project-local enable/disable preferences for skills and MCP servers."""
import json
import os
from pathlib import Path
from typing import Literal


class ExtensionPreferences:
    def __init__(self, cwd: Path) -> None:
        self.path = cwd / ".citrus-code" / "extensions.json"
        self.disabled_skills: set[str] = set()
        self.disabled_mcp: set[str] = set()
        self.skill_paths: list[str] = []
        self.load()

    def load(self) -> None:
        if not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8-sig"))
            if not isinstance(data, dict) or set(data) - {"disabled_skills", "disabled_mcp", "skill_paths"}:
                raise ValueError("invalid fields")
            for key in ("disabled_skills", "disabled_mcp", "skill_paths"):
                values = data.get(key, [])
                if not isinstance(values, list) or any(not isinstance(v, str) for v in values):
                    raise ValueError("expected list of names")
            self.disabled_skills = set(data.get("disabled_skills", []))
            self.disabled_mcp = set(data.get("disabled_mcp", []))
            self.skill_paths = list(dict.fromkeys(data.get("skill_paths", [])))
        except (ValueError, UnicodeError):
            raise ValueError(f"扩展配置格式无效：{self.path}") from None

    def set_enabled(self, kind: Literal["skill", "mcp"], name: str, enabled: bool) -> None:
        skills = set(self.disabled_skills)
        servers = set(self.disabled_mcp)
        selected = skills if kind == "skill" else servers
        if enabled:
            selected.discard(name)
        else:
            selected.add(name)
        self._save(skills, servers, self.skill_paths)
        self.disabled_skills = skills
        self.disabled_mcp = servers

    def set_skill_path(self, path: Path, add: bool) -> None:
        resolved_path = path.expanduser().resolve()
        resolved = str(resolved_path)
        paths = list(self.skill_paths)
        if add:
            if not resolved_path.is_dir() and not (resolved_path.is_file() and resolved_path.name == "SKILL.md"):
                raise ValueError("路径必须是 Skill 目录或 SKILL.md 文件。")
            if resolved not in paths:
                paths.append(resolved)
        else:
            if resolved not in paths:
                raise ValueError("该路径不在用户添加的 Skill 路径中。")
            paths.remove(resolved)
        self._save(self.disabled_skills, self.disabled_mcp, paths)
        self.skill_paths = paths

    def _save(self, skills: set[str], servers: set[str], paths: list[str]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(self.path.name + ".tmp")
        try:
            temporary.write_text(json.dumps({
                "disabled_skills": sorted(skills), "disabled_mcp": sorted(servers), "skill_paths": paths,
            }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            os.replace(temporary, self.path)
        finally:
            temporary.unlink(missing_ok=True)
