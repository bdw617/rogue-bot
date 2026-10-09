"""Run a curses program in a pseudo-terminal and keep an emulated screen of it."""

import fcntl
import os
import pty
import select
import signal
import struct
import tempfile
import termios
import time

import pyte

ROWS, COLS = 24, 80


class Terminal:
    def __init__(self, argv: list[str], env: dict[str, str] | None = None):
        self.screen = pyte.Screen(COLS, ROWS)
        self.stream = pyte.ByteStream(self.screen)
        pid, fd = pty.fork()
        if pid == 0:
            # Rogue drops rogue.esave in cwd when killed mid-game.
            os.chdir(tempfile.gettempdir())
            fcntl.ioctl(0, termios.TIOCSWINSZ, struct.pack("HHHH", ROWS, COLS, 0, 0))
            full_env = {**os.environ, **(env or {}), "TERM": "vt100",
                        "LINES": str(ROWS), "COLUMNS": str(COLS)}
            os.execvpe(argv[0], argv, full_env)
        self.pid, self.fd = pid, fd
        self.alive = True

    def send(self, keys: str) -> None:
        if self.alive:
            try:
                os.write(self.fd, keys.encode())
            except OSError:
                self.alive = False

    def pump(self, first_wait: float = 0.1, idle: float = 0.015) -> bool:
        """Read output until it goes quiet. Returns True if anything arrived."""
        got = False
        while self.alive:
            r, _, _ = select.select([self.fd], [], [], idle if got else first_wait)
            if not r:
                break
            try:
                data = os.read(self.fd, 65536)
            except OSError:
                data = b""
            if not data:
                self.alive = False
                break
            self.stream.feed(data)
            got = True
        return got

    def lines(self) -> list[str]:
        return list(self.screen.display)

    def close(self) -> None:
        if self.alive:
            try:
                os.kill(self.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        self.alive = False
        try:
            os.close(self.fd)
        except OSError:
            pass
        deadline = time.monotonic() + 1
        while time.monotonic() < deadline:
            try:
                if os.waitpid(self.pid, os.WNOHANG)[0]:
                    break
            except ChildProcessError:
                break
            time.sleep(0.01)
