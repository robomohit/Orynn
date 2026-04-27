import logging
import os
from pathlib import Path
from typing import Dict, List, Optional
from pydantic import BaseModel

_log = logging.getLogger(__name__)

class Skill(BaseModel):
    id: str
    name: str
    description: str
    manual: str

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
        # Professional Email Architect
        self.skills["email_architect"] = Skill(
            id="email_architect",
            name="Email Architect",
            description="Expertise in drafting professional, high-impact emails with perfect tone.",
            manual="""## SKILL: Email Architect
- You are an expert at professional communication.
- Prioritize conciseness and clarity.
- Use a tone that matches the user's intent (Formal, Casual, Persuasive).
- Always check for spelling and grammar.
- Suggest 3 subject line options for every draft."""
        )

        # Web Deep Searcher
        self.skills["deep_searcher"] = Skill(
            id="deep_searcher",
            name="Deep Searcher",
            description="Specialized in thorough web research, data extraction, and synthesis.",
            manual="""## SKILL: Deep Searcher
- You are a research specialist.
- When searching, look for primary sources and data-backed reports.
- Synthesize information from at least 3 different sources.
- Provide a 'Key Takeaways' summary at the beginning of your findings.
- Always cite the URLs you used."""
        )

    def _load_custom_skills(self):
        """Scan the skills/ directory for .md files and load them as custom skills."""
        if not self.skills_dir.exists():
            return
        for md_file in self.skills_dir.glob("*.md"):
            skill_id = md_file.stem
            try:
                raw = md_file.read_text(encoding="utf-8").strip()
                lines = raw.splitlines()
                # Extract name from first heading line (e.g. "# My Skill Name")
                name = skill_id
                manual_start = 0
                if lines and lines[0].startswith("#"):
                    name = lines[0].lstrip("#").strip()
                    manual_start = 1
                manual = "\n".join(lines[manual_start:]).strip()
                # Use first non-empty line of manual as description fallback
                description = next((l.lstrip("-# ").strip() for l in lines[manual_start:] if l.strip()), name)
                self.skills[skill_id] = Skill(
                    id=skill_id,
                    name=name,
                    description=description,
                    manual=manual,
                )
            except Exception as exc:
                _log.warning("Skipping custom skill %s: %s", md_file.name, exc)

    def get_skill(self, skill_id: str) -> Optional[Skill]:
        self._ensure_loaded()
        return self.skills.get(skill_id)

    def get_all_skills(self) -> List[Skill]:
        self._ensure_loaded()
        return list(self.skills.values())

skill_manager = SkillManager()
