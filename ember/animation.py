"""Ember expanded animation player.

Integrated from the fixed animation pack. Loads individual transparent PNG frames,
uses real delta-time playback, and avoids atlas bleed/particle artifacts.
"""
from pathlib import Path
from typing import Dict, List, Optional
import pygame


class EmberAnimation:
    def __init__(self, frames_dir: str | Path, fps: float = 24.0):
        self.frames_dir = Path(frames_dir)
        self.fps = fps
        self.frame_duration = 1.0 / fps
        self.frames: List[pygame.Surface] = []
        for i in range(24):
            path = self.frames_dir / f"{i:02d}.png"
            if not path.exists():
                raise FileNotFoundError(f"Missing Ember frame: {path}")
            self.frames.append(pygame.image.load(str(path)))
        self.frame_index = 0
        self.timer = 0.0
        self.playing = True
        self.loop = True
        self._converted = False

    def ensure_converted(self):
        if not self._converted:
            self.frames = [f.convert_alpha() for f in self.frames]
            self._converted = True

    def update(self, dt: float):
        if not self.playing or not self.frames:
            return
        self.timer += dt
        while self.timer >= self.frame_duration:
            self.timer -= self.frame_duration
            self.frame_index += 1
            if self.frame_index >= len(self.frames):
                if self.loop:
                    self.frame_index = 0
                else:
                    self.frame_index = len(self.frames) - 1
                    self.playing = False

    def get_surface(self) -> pygame.Surface:
        return self.frames[self.frame_index]

    def reset(self):
        self.frame_index = 0
        self.timer = 0.0
        self.playing = True


class EmberAnimator:
    ANIMATIONS = {
        "laughing-fit": "laughing-fit",
        "gremlin-plotting": "gremlin-plotting",
        "celebration-dance": "celebration-dance",
        "facepalm": "facepalm",
        "point-left-right": "point-left-right",
        "frantic-pointing": "frantic-pointing",
        "present-both-hands": "present-both-hands",
    }

    def __init__(self, pack_root: str | Path):
        self.pack_root = Path(pack_root)
        self.frames_root = self.pack_root / "frames"
        if not self.frames_root.exists():
            raise FileNotFoundError(f"Ember frames folder not found: {self.frames_root}")
        self.animations: Dict[str, EmberAnimation] = {}
        self.current: Optional[EmberAnimation] = None
        self.current_name: Optional[str] = None
        for name, folder in self.ANIMATIONS.items():
            path = self.frames_root / folder / "hold24"
            if path.exists():
                self.animations[name] = EmberAnimation(path, fps=24.0)

    def play(self, name: str, loop: bool = True):
        if name not in self.animations:
            raise ValueError(f"Unknown Ember animation: {name}; available={list(self.animations)}")
        self.current = self.animations[name]
        self.current_name = name
        self.current.loop = loop
        self.current.reset()
        self.current.ensure_converted()

    def update(self, dt: float):
        if self.current:
            self.current.update(dt)

    def get_surface(self) -> Optional[pygame.Surface]:
        if not self.current:
            return None
        self.current.ensure_converted()
        return self.current.get_surface()

    def get_size(self):
        if self.current and self.current.frames:
            return self.current.frames[0].get_size()
        return (192, 208)

    def ensure_all_converted(self):
        for anim in self.animations.values():
            anim.ensure_converted()
