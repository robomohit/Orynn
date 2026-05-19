from pathlib import Path

from app.skills import SkillManager


def test_skill_manager_loads_skill_md_directory_format(workspace):
    skills_dir = workspace / "skills"
    skill_dir = skills_dir / "reviewer"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        """---
name: Reviewer
description: Review code changes for correctness.
allowed-tools:
  - read_file
  - run_tests
---
# Reviewer

- Focus on bugs first.
- Cite files when possible.
""",
        encoding="utf-8",
    )

    manager = SkillManager(str(skills_dir))
    listed = {skill.id: skill for skill in manager.get_all_skills()}
    assert "reviewer" in listed
    assert listed["reviewer"].description == "Review code changes for correctness."
    assert listed["reviewer"].manual == ""
    assert listed["reviewer"].allowed_tools == ["read_file", "run_tests"]

    loaded = manager.get_skill("reviewer")
    assert loaded is not None
    assert "# Reviewer" in loaded.manual
    assert "Focus on bugs first." in loaded.manual


def test_skill_manager_keeps_flat_markdown_skills_compatible(workspace):
    skills_dir = workspace / "skills"
    skills_dir.mkdir()
    (skills_dir / "note_taker.md").write_text(
        """# Note Taker

- Capture the important bits.
""",
        encoding="utf-8",
    )

    manager = SkillManager(str(skills_dir))
    skill = manager.get_skill("note_taker")
    assert skill is not None
    assert skill.name == "Note Taker"
    assert "Capture the important bits." in skill.manual
