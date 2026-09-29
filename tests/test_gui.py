import sys
import pytest

tk = pytest.importorskip("tkinter")  # tkinterが無い環境ではスキップ
from gui import build_command, split_words


def test_split_words():
    assert split_words("ジャグラー, ハナハナ、 A\nB") == ["ジャグラー", "ハナハナ", "A", "B"]


def test_build_command():
    s = {"stores": "店A\n店B", "keywords": "ジャグラー, ハナハナ", "since": "2025-01-01", "headed": True, "test": True}
    cmd = build_command(s)
    assert cmd[0] == sys.executable
    assert cmd[cmd.index("--since") + 1] == "2025-01-01"
    assert cmd.count("--store") == 2 and cmd.count("--machine") == 2
    assert "--headed" in cmd and cmd[cmd.index("--limit") + 1] == "2"


def test_invalid():
    with pytest.raises(ValueError):
        build_command({"stores": "", "keywords": "", "since": "", "headed": False, "test": False})
    with pytest.raises(ValueError):
        build_command({"stores": "A", "keywords": "", "since": "2025-01-201", "headed": False, "test": False})
