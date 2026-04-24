import os
from pathlib import Path
from typing import Dict, List, Optional
from pydantic import BaseModel

class Skill(BaseModel):
    id: str
    name: str
    description: str
    manual: str

class SkillManager:
    def __init__(self, skills_dir: str = "skills"):
        self.skills_dir = Path(skills_dir)
        self.skills_dir.mkdir(parents=True, exist_ok=True)
        self.skills: Dict[str, Skill] = {}
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
        # In the future, we can load .md files from the skills/ directory here
        pass

    def get_skill(self, skill_id: str) -> Optional[Skill]:
        return self.skills.get(skill_id)

    def get_all_skills(self) -> List[Skill]:
        return list(self.skills.values())

skill_manager = SkillManager()
