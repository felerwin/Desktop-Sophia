"""Transparent Ember body with animated idle/walking and static reactions."""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import math
import queue
import random
import threading
import time
import traceback
from pathlib import Path
from typing import Any

from PIL import Image


CELL_WIDTH = 192
CELL_HEIGHT = 208
REACTION_NAMES = {
    "idle", "listening", "thinking", "speaking", "amused", "excited",
    "concerned", "startled", "moving-left", "moving-right",
    "laughing", "facepalming", "embarrassed", "shy", "worried", "crying", "smug",
    "proud", "curious", "determined", "sleepy", "annoyed", "confused",
    "skeptical", "affectionate", "relieved", "mischievous",
}
ANIMATION_INTERVAL_MS = {"idle": 360, "moving-left": 145, "moving-right": 145}
TICK_INTERVAL_MS = 50
REACTION_SEQUENCE_MS = 900


class ReactionImages:
    """Loads Ember's independent transparent PNG reaction assets."""

    def __init__(self, directory: str | Path):
        self.directory = Path(directory)
        missing = [name for name in REACTION_NAMES if not (self.directory / f"{name}.png").is_file()]
        if missing:
            raise ValueError(f"Missing static Ember reactions: {', '.join(sorted(missing))}")
        self.animations = self.directory.parent / "animations"
        missing_animations = [
            name for name in ANIMATION_INTERVAL_MS
            if not list((self.animations / name).glob("*.png"))
        ]
        if missing_animations:
            raise ValueError(f"Missing Ember animations: {', '.join(sorted(missing_animations))}")

    def reaction(self, name: str) -> Image.Image:
        reaction = name if name in REACTION_NAMES else "idle"
        return Image.open(self.directory / f"{reaction}.png").convert("RGBA")

    def look(self, degrees: float) -> Image.Image:
        index = int(round((degrees % 360) / 22.5)) % 16
        angle = index * 22.5
        label = f"{angle:g}"
        return Image.open(self.directory / f"look-{label}.png").convert("RGBA")

    def animation(self, name: str) -> list[Image.Image]:
        if name not in ANIMATION_INTERVAL_MS:
            return []
        return [Image.open(path).convert("RGBA") for path in sorted((self.animations / name).glob("*.png"))]


def direction_degrees(dx: float, dy: float) -> float:
    """Convert screen-space delta to the atlas's clockwise, up-is-zero angle."""
    if dx == 0 and dy == 0:
        return 180.0
    return math.degrees(math.atan2(dx, -dy)) % 360


def target_destination(target) -> tuple[int, int]:
    """Return window coordinates from a target that may also carry focus metadata."""
    return target[0], target[1]


def is_headpat_point(x: int, y: int, width: int, height: int) -> bool:
    """Small upper-center hitbox; the remainder of Ember stays click-through."""
    return width * 0.24 <= x <= width * 0.76 and 0 <= y <= height * 0.42


class EmberOverlay:
    """Owns a small Tk overlay on a dedicated UI thread."""

    STATE_REACTIONS = {
        "idle": "idle",
        "listening": "listening",
        "thinking": "thinking",
        "speaking": "speaking",
        "amused": "amused",
        "excited": "excited",
        "concerned": "concerned",
        "startled": "startled",
        "laughing": "laughing",
        "facepalming": "facepalming",
        "embarrassed": "embarrassed",
        "shy": "shy",
        "worried": "worried",
        "crying": "crying",
        "smug": "smug",
        "proud": "proud",
        "curious": "curious",
        "determined": "determined",
        "sleepy": "sleepy",
        "annoyed": "annoyed",
        "confused": "confused",
        "skeptical": "skeptical",
        "affectionate": "affectionate",
        "relieved": "relieved",
        "mischievous": "mischievous",
        "pointing": "idle",
        "moving": "moving-right",
    }

    def __init__(
        self, reactions_path: str | Path, scale: float = 1.0,
        wander: bool = True, wander_min_seconds: float = 22.0,
        wander_max_seconds: float = 50.0, interaction_handler=None,
    ):
        self.images = ReactionImages(reactions_path)
        self.scale = max(0.5, min(2.0, float(scale)))
        self.wander = bool(wander)
        self.wander_min_seconds = max(8.0, float(wander_min_seconds))
        self.wander_max_seconds = max(self.wander_min_seconds, float(wander_max_seconds))
        self.interaction_handler = interaction_handler
        self.commands: queue.Queue[dict[str, Any]] = queue.Queue()
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._stopped = threading.Event()
        self._reaction_cache: dict[str, Image.Image] = {}
        self._animation_cache: dict[str, list[Image.Image]] = {}
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
            self._make_headpat_hit_test(root.winfo_id(), width, height)

            reaction = "idle"
            frame = self._scaled_reaction(reaction)
            frames = self._scaled_animation(reaction)
            frame_index = 0
            next_frame_at = time.monotonic() + ANIMATION_INTERVAL_MS[reaction] / 1000
            photo = None
            target: tuple[int, int, int, int] | None = None
            target_kind = "wander"
            look_angle: float | None = None
            look_until = 0.0
            reaction_sequence: list[str] = []
            next_reaction_at = 0.0
            next_wander_at = time.monotonic() + random.uniform(
                self.wander_min_seconds, self.wander_max_seconds
            )

            def set_reaction(name: str) -> None:
                nonlocal reaction, frame, frames, frame_index, next_frame_at, look_angle
                reaction = name if name in REACTION_NAMES else "idle"
                look_angle = None
                frame = self._scaled_reaction(reaction)
                frames = self._scaled_animation(reaction)
                frame_index = 0
                next_frame_at = time.monotonic() + (
                    ANIMATION_INTERVAL_MS.get(reaction, TICK_INTERVAL_MS) / 1000
                )

            def start_sequence(states: list[str]) -> None:
                nonlocal reaction_sequence, next_reaction_at
                reaction_sequence = [
                    self.STATE_REACTIONS.get(state, "idle") for state in states
                ]
                if reaction_sequence:
                    set_reaction(reaction_sequence.pop(0))
                    next_reaction_at = time.monotonic() + REACTION_SEQUENCE_MS / 1000

            def on_headpat(event) -> None:
                nonlocal next_wander_at
                if not is_headpat_point(event.x, event.y, width, height):
                    return
                start_sequence(["startled", "affectionate", "shy", "affectionate"])
                next_wander_at = time.monotonic() + self.wander_min_seconds
                if self.interaction_handler is not None:
                    try:
                        self.interaction_handler("headpat")
                    except Exception:
                        traceback.print_exc()

            label.bind("<Button-1>", on_headpat)

            def recover_callback(exc_type, exc, tb) -> None:
                """Keep one failed Tk refresh callback from freezing Ember in place."""
                nonlocal target, next_wander_at
                self.error = f"overlay callback recovered: {exc_type.__name__}: {exc}"
                traceback.print_exception(exc_type, exc, tb)
                target = None
                set_reaction("idle")
                next_wander_at = time.monotonic() + random.uniform(
                    self.wander_min_seconds, self.wander_max_seconds
                )
                try:
                    root.after(TICK_INTERVAL_MS, tick)
                except tk.TclError:
                    pass

            root.report_callback_exception = recover_callback

            def tick() -> None:
                nonlocal x, y, target, target_kind, frame, frames, frame_index, next_frame_at, photo, look_angle, look_until, next_wander_at, reaction_sequence, next_reaction_at
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
                        reaction_sequence = []
                        desired = self.STATE_REACTIONS.get(str(command.get("state")), "idle")
                        if desired != reaction:
                            set_reaction(desired)
                    elif action == "sequence":
                        target = None
                        look_angle = None
                        start_sequence([str(state) for state in command.get("states") or []])
                    elif action == "point_at":
                        reaction_sequence = []
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
                        target_kind = "point"

                if (
                    self.wander and target is None and reaction == "idle"
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
                    target_kind = "wander"
                    next_wander_at = time.monotonic() + random.uniform(
                        self.wander_min_seconds, self.wander_max_seconds
                    )

                if target:
                    dx, dy = target[0] - x, target[1] - y
                    distance = math.hypot(dx, dy)
                    if distance > 8:
                        step = min(9.0, distance)
                        x += int(round(dx / distance * step))
                        y += int(round(dy / distance * step))
                        desired = "moving-right" if dx >= 0 else "moving-left"
                        if reaction != desired:
                            set_reaction(desired)
                        root.geometry(f"{width}x{height}+{x}+{y}")
                    else:
                        x, y = target_destination(target)
                        root.geometry(f"{width}x{height}+{x}+{y}")
                        if target_kind == "point":
                            look_angle = direction_degrees(
                                target[2] - (x + width // 2),
                                target[3] - (y + height // 2),
                            )
                            look_until = time.monotonic() + 3.5
                        else:
                            set_reaction("idle")
                        target = None

                if look_angle is not None and time.monotonic() >= look_until:
                    set_reaction("idle")

                if reaction_sequence and time.monotonic() >= next_reaction_at:
                    set_reaction(reaction_sequence.pop(0))
                    next_reaction_at = time.monotonic() + REACTION_SEQUENCE_MS / 1000
                elif not reaction_sequence and next_reaction_at and time.monotonic() >= next_reaction_at:
                    next_reaction_at = 0.0
                    set_reaction("idle")

                if look_angle is not None:
                    display_frame = self.images.look(look_angle)
                    if self.scale != 1.0:
                        display_frame = display_frame.resize((width, height), Image.Resampling.LANCZOS)
                else:
                    if frames:
                        now = time.monotonic()
                        if now >= next_frame_at:
                            frame_index = (frame_index + 1) % len(frames)
                            next_frame_at = now + ANIMATION_INTERVAL_MS[reaction] / 1000
                        display_frame = frames[frame_index]
                    else:
                        display_frame = frame
                photo = ImageTk.PhotoImage(display_frame)
                label.configure(image=photo)
                root.after(TICK_INTERVAL_MS, tick)

            self._ready.set()
            tick()
            root.mainloop()
        except Exception as exc:
            self.error = str(exc)
            self._ready.set()
        finally:
            self._stopped.set()

    def _scaled_reaction(self, reaction: str) -> Image.Image:
        cached = self._reaction_cache.get(reaction)
        if cached is not None:
            return cached
        frame = self.images.reaction(reaction)
        if self.scale != 1.0:
            size = (int(CELL_WIDTH * self.scale), int(CELL_HEIGHT * self.scale))
            frame = frame.resize(size, Image.Resampling.LANCZOS)
        self._reaction_cache[reaction] = frame
        return frame

    def _scaled_animation(self, reaction: str) -> list[Image.Image]:
        cached = self._animation_cache.get(reaction)
        if cached is not None:
            return cached
        frames = self.images.animation(reaction)
        if self.scale != 1.0:
            size = (int(CELL_WIDTH * self.scale), int(CELL_HEIGHT * self.scale))
            frames = [frame.resize(size, Image.Resampling.LANCZOS) for frame in frames]
        self._animation_cache[reaction] = frames
        return frames

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

    def _make_headpat_hit_test(self, hwnd: int, width: int, height: int) -> None:
        """Return mouse hits only over the head and pass all other clicks below."""
        if not hasattr(ctypes, "windll"):
            return
        user32 = ctypes.windll.user32
        hwnd = user32.GetAncestor(hwnd, 2) or hwnd
        get_style = getattr(user32, "GetWindowLongPtrW", user32.GetWindowLongW)
        set_style = getattr(user32, "SetWindowLongPtrW", user32.SetWindowLongW)
        get_style.restype = ctypes.c_ssize_t
        set_style.restype = ctypes.c_ssize_t
        user32.CallWindowProcW.restype = ctypes.c_ssize_t
        ex_style = get_style(hwnd, -20)
        set_style(hwnd, -20, (ex_style | 0x00080000 | 0x00000080 | 0x08000000) & ~0x00000020)
        WM_NCHITTEST, HTCLIENT, HTTRANSPARENT = 0x0084, 1, -1
        WNDPROC = ctypes.WINFUNCTYPE(
            ctypes.c_ssize_t, ctypes.c_void_p, ctypes.c_uint,
            ctypes.c_size_t, ctypes.c_ssize_t,
        )
        original = get_style(hwnd, -4)

        def window_proc(window, message, wparam, lparam):
            if message == WM_NCHITTEST:
                screen_x = ctypes.c_short(lparam & 0xFFFF).value
                screen_y = ctypes.c_short((lparam >> 16) & 0xFFFF).value
                rect = wintypes.RECT()
                user32.GetWindowRect(window, ctypes.byref(rect))
                if is_headpat_point(screen_x - rect.left, screen_y - rect.top, width, height):
                    return HTCLIENT
                return HTTRANSPARENT
            return user32.CallWindowProcW(original, window, message, wparam, lparam)

        callback = WNDPROC(window_proc)
        self._headpat_wndproc = callback
        self._original_wndproc = original
        set_style(hwnd, -4, ctypes.cast(callback, ctypes.c_void_p).value)
        user32.SetWindowPos(hwnd, -1, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0010 | 0x0040)
