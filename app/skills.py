import logging
from pathlib import Path
from typing import Dict, List, Optional

from pydantic import BaseModel, Field

_log = logging.getLogger(__name__)


class Skill(BaseModel):
    id: str
    name: str
    description: str
    manual: str = ""
    allowed_tools: List[str] = Field(default_factory=list)
    manual_path: Optional[str] = None


def _parse_frontmatter(raw: str) -> tuple[dict, str]:
    if not raw.startswith("---"):
        return {}, raw
    lines = raw.splitlines()
    if len(lines) < 3:
        return {}, raw
    end_idx = None
    for idx in range(1, len(lines)):
        if lines[idx].strip() == "---":
            end_idx = idx
            break
    if end_idx is None:
        return {}, raw
    meta_lines = lines[1:end_idx]
    body = "\n".join(lines[end_idx + 1 :]).strip()
    meta: dict = {}
    i = 0
    while i < len(meta_lines):
        line = meta_lines[i].rstrip()
        i += 1
        if not line or ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip().lower()
        value = value.strip()
        if key == "allowed-tools":
            tools: List[str] = []
            if value.startswith("[") and value.endswith("]"):
                inner = value[1:-1].strip()
                if inner:
                    tools = [part.strip().strip("'\"") for part in inner.split(",") if part.strip()]
            else:
                while i < len(meta_lines):
                    item = meta_lines[i].strip()
                    if not item.startswith("-"):
                        break
                    tools.append(item[1:].strip().strip("'\""))
                    i += 1
            meta[key] = tools
            continue
        meta[key] = value.strip("'\"")
    return meta, body


def _manual_name_and_description(skill_id: str, body: str) -> tuple[str, str]:
    lines = body.splitlines()
    name = skill_id
    manual_lines = lines
    if lines and lines[0].startswith("#"):
        name = lines[0].lstrip("#").strip() or skill_id
        manual_lines = lines[1:]
    description = next(
        (line.lstrip("-# ").strip() for line in manual_lines if line.strip()),
        name,
    )
    return name, description


class SkillManager:
    def __init__(self, skills_dir: str = "skills"):
        self.skills_dir = Path(skills_dir)
        self.skills: Dict[str, Skill] = {}
        self._loaded: bool = False

    def _ensure_loaded(self):
        """Lazily load starter and custom skills on first access."""
        if self._loaded:
            return
        self._loaded = True
        self._load_starter_skills()
        self._load_custom_skills()

    def _load_starter_skills(self):
        self.skills["email_architect"] = Skill(
            id="email_architect",
            name="Email Architect",
            description="Expertise in drafting professional, high-impact emails with perfect tone.",
            manual="""## SKILL: Email Architect
- You are an expert at professional communication.
- Prioritize conciseness and clarity.
- Use a tone that matches the user's intent (Formal, Casual, Persuasive).
- Always check for spelling and grammar.
- Suggest 3 subject line options for every draft.""",
        )
        self.skills["deep_searcher"] = Skill(
            id="deep_searcher",
            name="Deep Searcher",
            description="Specialized in thorough web research, data extraction, and synthesis.",
            manual="""## SKILL: Deep Searcher
- You are a research specialist.
- When searching, look for primary sources and data-backed reports.
- Synthesize information from at least 3 different sources.
- Provide a 'Key Takeaways' summary at the beginning of your findings.
- Always cite the URLs you used.""",
        )

    def _register_file_skill(self, skill_id: str, source: Path) -> None:
        raw = source.read_text(encoding="utf-8").strip()
        meta, body = _parse_frontmatter(raw)
        name, description = _manual_name_and_description(skill_id, body)
        self.skills[skill_id] = Skill(
            id=skill_id,
            name=str(meta.get("name") or name),
            description=str(meta.get("description") or description),
            manual="",
            allowed_tools=list(meta.get("allowed-tools") or []),
            manual_path=str(source),
        )

    def _load_custom_skills(self):
        """Load flat `.md` skills and standard `skills/<name>/SKILL.md` skills."""
        if not self.skills_dir.exists():
            return
        for md_file in self.skills_dir.glob("*.md"):
            skill_id = md_file.stem
            try:
                self._register_file_skill(skill_id, md_file)
            except Exception as exc:
                _log.warning("Skipping custom skill %s: %s", md_file.name, exc)
        for skill_dir in self.skills_dir.iterdir():
            if not skill_dir.is_dir():
                continue
            skill_file = skill_dir / "SKILL.md"
            if not skill_file.exists():
                continue
            try:
                self._register_file_skill(skill_dir.name, skill_file)
            except Exception as exc:
                _log.warning("Skipping custom skill %s: %s", skill_file, exc)

    def get_skill(self, skill_id: str) -> Optional[Skill]:
        self._ensure_loaded()
        skill = self.skills.get(skill_id)
        if not skill:
            return None
        if not skill.manual and skill.manual_path:
            try:
                raw = Path(skill.manual_path).read_text(encoding="utf-8").strip()
                _meta, body = _parse_frontmatter(raw)
                skill.manual = body
            except Exception as exc:
                _log.warning("Failed to load skill manual %s: %s", skill.manual_path, exc)
        return skill

    def get_all_skills(self) -> List[Skill]:
        self._ensure_loaded()
        return list(self.skills.values())


skill_manager = SkillManager()
