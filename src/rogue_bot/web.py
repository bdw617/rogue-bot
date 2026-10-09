"""Live browser view: a tiny local web server that streams each frame to the page."""

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .bot import Bot
from .view import cells, panel

PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Rogue Bot</title>
<link rel="icon" href="data:,">
<style>
  :root { --bg: #101114; --fg: #d6d6d6; --dim: #5d6168; --panel: #181a1f; --line: #2a2d34;
          --player-bg: #e0b02a; --monster: #ff5c5c; --item: #4fd1d9; --stairs: #52e07a;
          --door: #e0b050; --trail: #1d3a8a; --target: #7b2fc0; }
  * { box-sizing: border-box; }
  body { margin: 0; padding: 16px; background: var(--bg); color: var(--fg);
         font-family: ui-monospace, "SF Mono", Menlo, Consolas, monospace; }
  main { max-width: 1100px; margin: 0 auto; }
  h1 { font-size: 15px; font-weight: 600; margin: 0 0 10px; color: var(--dim); letter-spacing: .04em; }
  #map { margin: 0; padding: 12px; background: #000; border: 1px solid var(--line); border-radius: 6px;
         font-size: clamp(7px, 1.32vw, 15px); line-height: 1.18; white-space: pre; overflow-x: auto; }
  .player { background: var(--player-bg); color: #000; font-weight: 700; }
  .monster { color: var(--monster); font-weight: 700; }
  .item { color: var(--item); }
  .stairs { color: var(--stairs); font-weight: 700; }
  .door { color: var(--door); }
  .memory { color: var(--dim); }
  .trail { background: var(--trail); }
  .target { background: var(--target); }
  #panel { margin-top: 12px; padding: 12px 14px; background: var(--panel); border: 1px solid var(--line);
           border-radius: 6px; font-size: 14px; line-height: 1.6; }
  #label { font-weight: 600; }
  #note { color: #fff; }
  #note::before { content: "bot: "; color: var(--dim); }
  #messages { color: var(--dim); margin: 4px 0 0; padding-left: 18px; }
  .legend { margin-top: 6px; color: var(--dim); font-size: 13px; }
  .legend span { display: inline-block; padding: 0 5px; margin-right: 4px; border-radius: 3px; }
  #status { float: right; color: var(--dim); font-size: 13px; }
</style>
</head>
<body>
<main>
  <h1>ROGUE BOT <span id="status">connecting...</span></h1>
  <pre id="map"></pre>
  <div id="panel">
    <div><span id="label"></span> &nbsp;·&nbsp; step <span id="step">0</span>
         &nbsp;·&nbsp; deepest <span id="deepest">0</span></div>
    <div id="note"></div>
    <ul id="messages"></ul>
    <div class="legend"><span class="player">@</span>bot <span class="trail">&nbsp;</span>trail
      <span class="target">&nbsp;</span>target <span class="memory">#</span>remembered map
      <span class="monster">H</span>monster <span class="item">!</span>item <span class="stairs">%</span>stairs</div>
  </div>
</main>
<script>
  const esc = s => s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  const $ = id => document.getElementById(id);
  // Keep only the newest frame and draw it when the browser paints, so a busy or
  // hidden tab never builds up a backlog.
  let pending = null;
  function draw() {
    const f = pending;
    pending = null;
    if (!f) return;
    $("map").innerHTML = f.rows.map(row =>
      row.map(([text, cls]) => cls ? `<span class="${cls}">${esc(text)}</span>` : esc(text)).join("")
    ).join("\\n");
    $("label").textContent = f.label;
    $("step").textContent = f.step;
    $("deepest").textContent = f.deepest;
    $("note").textContent = f.note;
    $("messages").innerHTML = f.messages.map(m => `<li>${esc(m)}</li>`).join("");
  }
  const events = new EventSource("/events");  // reconnects by itself if the bot restarts
  events.onopen = () => { $("status").textContent = "live"; };
  events.onerror = () => { $("status").textContent = "bot stopped: waiting for it to restart..."; };
  events.onmessage = e => {
    const first = pending === null;
    pending = JSON.parse(e.data);
    if (first) requestAnimationFrame(draw);
  };
</script>
</body>
</html>
"""


def frame(bot: Bot, label: str) -> bytes:
    """One screenful as JSON: each row is a list of [text, css classes] runs."""
    rows = []
    for row in cells(bot):
        runs = []
        for ch, fg, bg in row:
            cls = " ".join(k for k in (fg, bg) if k)
            if runs and runs[-1][1] == cls:
                runs[-1][0] += ch
            else:
                runs.append([ch, cls])
        rows.append(runs)
    return json.dumps({"rows": rows, **panel(bot, label)}).encode()


class WebView:
    """Serves the page on localhost and pushes every frame to open browser tabs."""

    def __init__(self, game_label, port: int = 8765, host: str = "127.0.0.1"):
        self.game_label = game_label
        self.latest = b""
        self.seq = 0
        self.changed = threading.Condition()
        view = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                if self.path == "/":
                    body = PAGE.encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                elif self.path == "/events":
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream")
                    self.send_header("Cache-Control", "no-cache")
                    self.end_headers()
                    view.stream(self.wfile)
                else:
                    self.send_error(404)

        self.server = ThreadingHTTPServer((host, port), Handler)
        self.server.daemon_threads = True
        self.url = f"http://{host if host != '0.0.0.0' else 'localhost'}:{port}/"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def stream(self, out, max_fps: float = 10) -> None:
        """Send the newest frame at most max_fps times a second; skipped frames are dropped."""
        seen = -1
        try:
            while True:
                time.sleep(1 / max_fps)
                with self.changed:
                    self.changed.wait_for(lambda: self.seq != seen, timeout=15)
                    data, now = self.latest, self.seq
                if now == seen:
                    out.write(b": still here\n\n")  # keeps idle connections open
                else:
                    seen = now
                    out.write(b"data: " + data + b"\n\n")
                out.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass

    def __call__(self, bot: Bot) -> None:
        data = frame(bot, self.game_label())
        with self.changed:
            self.latest, self.seq = data, self.seq + 1
            self.changed.notify_all()

    def close(self) -> None:
        # Never let a stuck shutdown keep the process alive; the server thread is a daemon anyway.
        stopper = threading.Thread(target=self.server.shutdown, daemon=True)
        stopper.start()
        stopper.join(timeout=1)
