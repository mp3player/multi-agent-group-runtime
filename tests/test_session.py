import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.session import Session
from models import System, User


def test_history_limit_prunes_old_non_system_messages() -> None:
    session = Session(history_limit=3)

    session.add(System("base"))
    for idx in range(5):
        session.add(User(f"message {idx}"))

    assert [msg.role for msg in session.history] == ["system", "user", "user"]
    assert [msg.message for msg in session.history] == [
        "base",
        "message 3",
        "message 4",
    ]
    assert [msg.message for msg in session.active] == [
        "base",
        "message 0",
        "message 1",
        "message 2",
        "message 3",
        "message 4",
    ]


if __name__ == "__main__":
    test_history_limit_prunes_old_non_system_messages()
