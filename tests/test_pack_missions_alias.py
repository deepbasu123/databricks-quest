"""Pack schema: a Quest consists of Missions (missions: alias for tasks:) + bonus."""
from models.quest_pack import QuestSpec


def test_missions_alias_populates_tasks():
    q = QuestSpec(slug="w", title="Warm-up",
                  missions=[{"slug": "m1", "title": "Connectivity", "objective": "check"}])
    assert len(q.tasks) == 1 and q.tasks[0].slug == "m1"


def test_tasks_still_supported():
    q = QuestSpec(slug="w", title="W",
                  tasks=[{"slug": "t1", "title": "T1", "objective": "o"}])
    assert len(q.tasks) == 1


def test_bonus_flag():
    assert QuestSpec(slug="b", title="Bonus", bonus=True, missions=[]).bonus is True
    assert QuestSpec(slug="c", title="Core", missions=[]).bonus is False
