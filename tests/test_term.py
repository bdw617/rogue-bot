import subprocess
import time

from rogue_bot.term import _children_dir, reap_orphans


def fake_rogue():
    return subprocess.Popen(["bash", "-c", "exec -a fake-rogue sleep 60"])


def dead_pid() -> int:
    p = subprocess.Popen(["true"])
    p.wait()
    return p.pid


def test_reaps_games_whose_bot_died(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    game = fake_rogue()
    time.sleep(0.2)
    (_children_dir() / str(game.pid)).write_text(str(dead_pid()))
    assert reap_orphans() == 1
    assert game.wait(timeout=5) is not None
    assert not list(_children_dir().iterdir())


def test_leaves_games_of_a_running_bot_alone(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    game = fake_rogue()
    time.sleep(0.2)
    (_children_dir() / str(game.pid)).write_text(str(subprocess.os.getpid()))
    try:
        assert reap_orphans() == 0
        assert game.poll() is None
    finally:
        game.kill()
