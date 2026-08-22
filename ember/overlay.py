"""Transparent always-on-top Windows body for Ember's v2 pet atlas."""
from __future__ import annotations

import ctypes
import math
import queue
import random
import threading
import time
from pathlib import Path
from typing import Any

from PIL import Image


CELL_WIDTH = 192
CELL_HEIGHT = 208
FRAME_COUNTS = {
    "idle": 6,
    "running-right": 8,
    "running-left": 8,
    "waving": 4,
    "jumping": 5,
    "failed": 8,
    "waiting": 6,
    "running": 6,
    "review": 6,
    "look-row-9": 8,
    "look-row-10": 8,
}
ROW_INDEX = {name: index for index, name in enumerate(FRAME_COUNTS)}
FRAME_INTERVAL_MS = {
    "idle": 360,
    "running-right": 145,
    "running-left": 145,
    "waving": 240,
    "jumping": 170,
    "failed": 280,
    "waiting": 340,
    "running": 260,
    "review": 320,
}
ONE_SHOT_ANIMATIONS = {"waving", "jumping", "failed"}


class SpriteAtlas:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.image = Image.open(self.path).convert("RGBA")
        if self.image.size != (CELL_WIDTH * 8, CELL_HEIGHT * 11):
            raise ValueError(f"Expected a 1536x2288 Ember v2 atlas, got {self.image.size}")

    def frames(self, animation: str) -> list[Image.Image]:
        row = ROW_INDEX[animation]
        return [
            self.image.crop((column * CELL_WIDTH, row * CELL_HEIGHT,
                             (column + 1) * CELL_WIDTH, (row + 1) * CELL_HEIGHT))
            for column in range(FRAME_COUNTS[animation])
        ]

    def look_frame(self, degrees: float) -> Image.Image:
        index = int(round((degrees % 360) / 22.5)) % 16
        row = "look-row-9" if index < 8 else "look-row-10"
        return self.frames(row)[index % 8]


def direction_degrees(dx: float, dy: float) -> float:
    """Convert screen-space delta to the atlas's clockwise, up-is-zero angle."""
    if dx == 0 and dy == 0:
        return 180.0
    return math.degrees(math.atan2(dx, -dy)) % 360


class EmberOverlay:
    """Owns a small Tk overlay on a dedicated UI thread."""

    STATE_ANIMATIONS = {
        "idle": "idle",
        "listening": "waiting",
        "thinking": "running",
        "speaking": "idle",
        "amused": "waving",
        "excited": "jumping",
        "concerned": "failed",
        "startled": "failed",
        "pointing": "idle",
        "moving": "running-right",
    }

    def __init__(
        self, atlas_path: str | Path, scale: float = 1.0,
        wander: bool = True, wander_min_seconds: float = 22.0,
        wander_max_seconds: float = 50.0,
    ):
        self.atlas = SpriteAtlas(atlas_path)
        self.scale = max(0.5, min(2.0, float(scale)))
        self.wander = bool(wander)
        self.wander_min_seconds = max(8.0, float(wander_min_seconds))
        self.wander_max_seconds = max(self.wander_min_seconds, float(wander_max_seconds))
        self.commands: queue.Queue[dict[str, Any]] = queue.Queue()
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._stopped = threading.Event()
        self.error: str | None = None

    def start(self, timeout: float = 5.0) -> bool:
        if self._thread and self._thread.is_alive():
            return True
        self._thread = threading.Thread(target=self._run, name="ember-overlay", daemon=True)
        self._thread.start()
        self._ready.wait(timeout)
        return self.error is None and self._ready.is_set()

    def submit(self, command: dict[str, Any]) -> None:
        if not self._stopped.is_set():
            self.commands.put(dict(command))

    def stop(self) -> None:
        self._stopped.set()
        self.commands.put({"action": "quit"})
        if self._thread:
            self._thread.join(timeout=3)

    def _run(self) -> None:
        try:
            import tkinter as tk
            from PIL import ImageTk

            root = tk.Tk()
            root.title("Ember Body")
            root.overrideredirect(True)
            transparent = "#010203"
            root.configure(bg=transparent)
            width = int(CELL_WIDTH * self.scale)
            height = int(CELL_HEIGHT * self.scale)
            screen_width = root.winfo_screenwidth()
            screen_height = root.winfo_screenheight()
            x = max(0, screen_width - width - 36)
            y = max(0, screen_height - height - 72)
            root.geometry(f"{width}x{height}+{x}+{y}")
            label = tk.Label(root, bg=transparent, borderwidth=0, highlightthickness=0)
            label.pack(fill="both", expand=True)
            root.update()
            root.wm_attributes("-transparentcolor", transparent)
            root.attributes("-topmost", True)
            self._make_click_through(root.winfo_id())

            animation = "idle"
            frame_index = 0
            frames = self._scaled_frames(animation)
            photo = None
            target: tuple[int, int, int, int] | None = None
            look_angle: float | None = None
            one_shot = False
            next_wander_at = time.monotonic() + random.uniform(
                self.wander_min_seconds, self.wander_max_seconds
            )

            def set_animation(name: str) -> None:
                nonlocal animation, frame_index, frames, look_angle, one_shot
                animation = name if name in FRAME_COUNTS else "idle"
                frame_index = 0
                look_angle = None
                one_shot = animation in ONE_SHOT_ANIMATIONS
                frames = self._scaled_frames(animation)

            def tick() -> None:
                nonlocal x, y, target, frame_index, photo, look_angle, next_wander_at
                while True:
                    try:
                        command = self.commands.get_nowait()
                    except queue.Empty:
                        break
                    action = command.get("action")
                    if action == "quit":
                        root.destroy()
                        return
                    if action == "state":
                        desired = self.STATE_ANIMATIONS.get(str(command.get("state")), "idle")
                        if desired != animation:
                            set_animation(desired)
                    elif action == "point_at":
                        raw = command.get("target") or {}
                        focus_x = int(float(raw.get("x", 0.5)) * screen_width)
                        focus_y = int(float(raw.get("y", 0.5)) * screen_height)
                        tx = focus_x - width - 20 if focus_x >= screen_width // 2 else focus_x + 20
                        ty = focus_y - height // 2
                        target = (
                            max(0, min(screen_width - width, tx)),
                            max(0, min(screen_height - height, ty)),
                            focus_x,
                            focus_y,
                        )

                if (
                    self.wander and target is None and animation == "idle"
                    and time.monotonic() >= next_wander_at
                ):
                    destination_x = random.randint(24, max(24, screen_width - width - 24))
                    destination_y = max(0, screen_height - height - 72)
                    target = (
                        destination_x,
                        destination_y,
                        destination_x + width // 2,
                        destination_y + height // 3,
                    )
                    next_wander_at = time.monotonic() + random.uniform(
                        self.wander_min_seconds, self.wander_max_seconds
                    )

                if target:
                    dx, dy = target[0] - x, target[1] - y
                    distance = math.hypot(dx, dy)
                    if distance > 8:
                        step = min(18.0, distance)
                        x += int(round(dx / distance * step))
                        y += int(round(dy / distance * step))
                        desired = "running-right" if dx >= 0 else "running-left"
                        if animation != desired:
                            set_animation(desired)
                        root.geometry(f"{width}x{height}+{x}+{y}")
                    else:
                        x, y = target
                        root.geometry(f"{width}x{height}+{x}+{y}")
                        look_angle = direction_degrees(
                            target[2] - (x + width // 2),
                            target[3] - (y + height // 2),
                        )
                        target = None
                        set_animation("idle")

                if look_angle is not None:
                    frame = self.atlas.look_frame(look_angle)
                    if self.scale != 1.0:
                        frame = frame.resize((width, height), Image.Resampling.LANCZOS)
                else:
                    frame = frames[frame_index % len(frames)]
                    frame_index += 1
                    if one_shot and frame_index >= len(frames):
                        set_animation("idle")
                photo = ImageTk.PhotoImage(frame)
                label.configure(image=photo)
                root.after(FRAME_INTERVAL_MS.get(animation, 280), tick)

            self._ready.set()
            tick()
            root.mainloop()
        except Exception as exc:
            self.error = str(exc)
            self._ready.set()
        finally:
            self._stopped.set()

    def _scaled_frames(self, animation: str) -> list[Image.Image]:
        frames = self.atlas.frames(animation)
        if self.scale == 1.0:
            return frames
        size = (int(CELL_WIDTH * self.scale), int(CELL_HEIGHT * self.scale))
        return [frame.resize(size, Image.Resampling.LANCZOS) for frame in frames]

    @staticmethod
    def _make_click_through(hwnd: int) -> None:
        if not hasattr(ctypes, "windll"):
            return
        user32 = ctypes.windll.user32
        hwnd = user32.GetAncestor(hwnd, 2) or hwnd
        get_style = getattr(user32, "GetWindowLongPtrW", user32.GetWindowLongW)
        set_style = getattr(user32, "SetWindowLongPtrW", user32.SetWindowLongW)
        ex_style = get_style(hwnd, -20)
        set_style(hwnd, -20, ex_style | 0x00000020 | 0x00000080 | 0x08000000)
        user32.SetWindowPos(hwnd, -1, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0010 | 0x0040)
