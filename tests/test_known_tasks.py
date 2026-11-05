import pathlib
import re

from ai.router.orchestrator import KNOWN_TASKS


def test_every_route_task_is_registered():
    used = set()
    for p in pathlib.Path("api/routes").glob("*.py"):
        used |= set(re.findall(r'task_type="([a-z_]+)"', p.read_text(encoding="utf-8")))
    assert used - set(KNOWN_TASKS) == set()