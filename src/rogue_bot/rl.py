"""Reinforcement learning: train a PPO agent on RogueEnv and watch it play.

Everything the agent knows comes from training. Checkpoints live in
~/.local/share/rogue-bot/rl/, and training resumes from the latest one, so every
run keeps improving the same agent.
"""

import argparse
import json
import time
from pathlib import Path

import torch
from stable_baselines3 import PPO
from stable_baselines3.common.callbacks import BaseCallback
from stable_baselines3.common.monitor import Monitor
from stable_baselines3.common.torch_layers import BaseFeaturesExtractor
from stable_baselines3.common.vec_env import SubprocVecEnv
from torch import nn

from .env import ACTION_NAMES, ACTIONS, RogueEnv
from .knowledge import DEFAULT_PATH as BOOK_PATH

RL_DIR = BOOK_PATH.parent / "rl"
MODEL = RL_DIR / "model.zip"
EPISODES = RL_DIR / "episodes.jsonl"


class ScreenNet(BaseFeaturesExtractor):
    """Learned character embeddings over two views, joined with the status numbers.

    The full map (with a layer for squares already visited) is read at full resolution
    before being condensed, so a lit room, its doors and its monsters are seen in detail;
    a close-up centred on the agent has its own layers for what's right next to it.
    """

    def __init__(self, space, features_dim: int = 256):
        super().__init__(space, features_dim)
        rows, cols = space["screen"].shape
        lrows, lcols = space["local"].shape
        self.embed = nn.Embedding(128, 16)
        self.full = nn.Sequential(
            nn.Conv2d(17, 32, 3, padding=1), nn.ReLU(),
            nn.Conv2d(32, 32, 3, padding=1, stride=2), nn.ReLU(),
            nn.Conv2d(32, 64, 3, padding=1, stride=2), nn.ReLU(),
            nn.Conv2d(64, 64, 3, padding=1, stride=2), nn.ReLU(), nn.Flatten())
        self.near = nn.Sequential(
            nn.Conv2d(16, 32, 3, padding=1), nn.ReLU(),
            nn.Conv2d(32, 64, 3, padding=1, stride=2), nn.ReLU(), nn.Flatten())
        with torch.no_grad():
            full_out = self.full(torch.zeros(1, 17, rows, cols)).shape[1]
            near_out = self.near(torch.zeros(1, 16, lrows, lcols)).shape[1]
        self.status = nn.Sequential(nn.Linear(space["status"].shape[0], 32), nn.ReLU())
        self.head = nn.Sequential(nn.Linear(full_out + near_out + 32, 512), nn.ReLU(),
                                  nn.Linear(512, features_dim), nn.ReLU())

    def chars(self, grid):
        return self.embed(grid.long().clamp(0, 127)).permute(0, 3, 1, 2)

    def forward(self, obs):
        visits = (obs["visits"].float() / 10).unsqueeze(1)
        full = self.full(torch.cat([self.chars(obs["screen"]), visits], dim=1))
        near = self.near(self.chars(obs["local"]))
        return self.head(torch.cat([full, near, self.status(obs["status"])], dim=1))


class EpisodeLog(BaseCallback):
    """Log every finished game and checkpoint the model regularly."""

    def __init__(self, every: int):
        super().__init__()
        self.every, self.last_save, self.recent = every, 0, []

    def _on_step(self) -> bool:
        for info in self.locals["infos"]:
            if "episode" in info and "depth" in info:
                row = {"time": time.time(), "timesteps": self.num_timesteps,
                       "return": round(float(info["episode"]["r"]), 2),
                       **{k: info[k] for k in ("depth", "gold", "xlevel", "steps", "cause")}}
                with open(EPISODES, "a") as f:
                    f.write(json.dumps(row) + "\n")
                self.recent = (self.recent + [row])[-100:]
        if self.num_timesteps - self.last_save >= self.every:
            self.last_save = self.num_timesteps
            self.model.save(MODEL)
            if self.recent:
                depths = [r["depth"] for r in self.recent]
                returns = [r["return"] for r in self.recent]
                print(f"{self.num_timesteps:>10,} steps | last {len(self.recent)} games: "
                      f"avg depth {sum(depths) / len(depths):.2f}, best {max(depths)}, "
                      f"avg return {sum(returns) / len(returns):.1f} | saved", flush=True)
        return True


def make_env(max_steps: int, read_wait: float):
    return lambda: Monitor(RogueEnv(max_steps=max_steps, read_wait=read_wait),
                           info_keywords=("depth", "gold", "xlevel", "steps", "cause"))


def train() -> None:
    ap = argparse.ArgumentParser(description="Train the reinforcement-learning rogue agent.")
    ap.add_argument("--steps", type=int, default=2_000_000, help="training moves this run")
    ap.add_argument("--envs", type=int, default=24, help="games played in parallel")
    ap.add_argument("--max-steps", type=int, default=3000, help="moves per game before it's cut off")
    ap.add_argument("--read-wait-ms", type=float, default=30,
                    help="max wait for rogue to answer; moves into walls print nothing")
    ap.add_argument("--device", default="auto", help="cpu, cuda, or auto")
    ap.add_argument("--fresh", action="store_true", help="start a new agent instead of resuming")
    args = ap.parse_args()

    RL_DIR.mkdir(parents=True, exist_ok=True)
    torch.set_num_threads(16)
    envs = SubprocVecEnv([make_env(args.max_steps, args.read_wait_ms / 1000) for _ in range(args.envs)],
                         start_method="forkserver")
    if MODEL.exists() and not args.fresh:
        model = PPO.load(MODEL, env=envs, device=args.device)
        print(f"resuming {MODEL} after {model.num_timesteps:,} moves", flush=True)
    else:
        model = PPO("MultiInputPolicy", envs, device=args.device, n_steps=256, batch_size=1536,
                    n_epochs=6, gamma=0.995, gae_lambda=0.95, ent_coef=0.01, learning_rate=3e-4,
                    policy_kwargs={"features_extractor_class": ScreenNet,
                                   "net_arch": {"pi": [256], "vf": [256]}})
        print(f"new agent on {model.device}", flush=True)
    try:
        model.learn(args.steps, callback=EpisodeLog(every=50_000), reset_num_timesteps=False)
    except KeyboardInterrupt:
        print("stopping")
    finally:
        model.save(MODEL)
        print(f"saved {MODEL} at {model.num_timesteps:,} moves", flush=True)
        envs.close()


class Watcher:
    """Just enough of a Bot for the terminal and browser views to draw an RL game."""

    def __init__(self, env: RogueEnv):
        from collections import deque

        from .bot import Result
        from .level import LevelMap
        self.env, self.map, self.result = env, LevelMap(), Result()
        self.trail, self.target, self.note = deque(maxlen=40), None, ""
        self.depth = 0

    @property
    def messages(self):
        return self.env.messages[-3:]

    def lines(self):
        return self.env.term.lines()

    def update(self, action: int, reward: float, steps: int) -> None:
        from .level import LevelMap, find_player, parse_status
        lines = self.lines()
        st, pos = parse_status(lines[23]), find_player(lines)
        if st and st.depth != self.depth:
            self.depth, self.map = st.depth, LevelMap()
            self.trail.clear()
        if pos:
            self.map.update(lines, pos)
            if not self.trail or self.trail[-1] != pos:
                self.trail.append(pos)
        if st:
            self.result.depth = max(self.result.depth, st.depth)
            self.result.gold, self.result.xlevel = st.gold, st.xlevel
        self.result.steps = steps
        self.note = f"RL agent: {ACTION_NAMES[action]} ({ACTIONS[action]}), reward {reward:+.2f}"


def play_rl_game(render=None, delay: float = 0.0, max_steps: int = 30000,
                 read_wait: float = 0.1, read_idle: float = 0.015, model_path: Path = MODEL):
    """Play one game with the trained agent; returns a Result like the rule bot's."""
    model = PPO.load(model_path, device="cpu")
    env = RogueEnv(max_steps=max_steps, read_wait=read_wait, read_idle=read_idle)
    started = time.time()
    obs, _ = env.reset()
    watcher = Watcher(env)
    done, steps = False, 0
    try:
        while not done:
            action, _ = model.predict(obs, deterministic=False)
            obs, r, terminated, truncated, info = env.step(action)
            steps += 1
            done = terminated or truncated
            if not done:
                watcher.update(int(action), r, steps)
                if render:
                    render(watcher)
                    if delay:
                        time.sleep(delay)
        watcher.result.cause = info.get("cause", "")
        watcher.result.depth = max(watcher.result.depth, info.get("depth", 0))
        watcher.result.seconds = round(time.time() - started, 1)
        return watcher.result
    finally:
        env.close()
