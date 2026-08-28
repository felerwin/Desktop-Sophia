import ctypes
from ctypes import wintypes
import threading
import time
from collections import deque

import mss


class WowPixelBridge:
    """Decode Ember Insight's visible, one-way pixel telemetry grid."""

    PALETTE = [
        (0, 0, 0), (255, 255, 255), (255, 0, 0), (0, 255, 0),
        (0, 0, 255), (255, 255, 0), (0, 255, 255), (255, 0, 255),
        (255, 128, 0), (128, 0, 255), (128, 255, 0), (255, 64, 128),
        (0, 128, 128), (0, 0, 128), (128, 128, 128), (128, 64, 0),
    ]
    MARKER = (2, 3, 4, 5, 6, 7, 1, 15)
    GRID = 12

    def __init__(self, config, on_packet=None, on_status=None):
        self.config = config
        self.on_packet = on_packet
        self.on_status = on_status
        self.stop_event = threading.Event()
        self.thread = None
        self.status = "searching"
        self._reported_status = None
        self.last_error = None
        self.last_packet_at = None
        self.last_sequence = None
        self.origin = None
        self.cell_size = None
        self.capture_origin = None
        self.window_detected = False
        self.monitor_index = None
        self.state = {}
        self.gear = {}
        self.recent = deque(maxlen=30)

    def start(self):
        if self.thread and self.thread.is_alive():
            return
        self.stop_event.clear()
        self.thread = threading.Thread(target=self._run, name="ember-wow-pixels", daemon=True)
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=2)

    def snapshot(self):
        age = time.time() - self.last_packet_at if self.last_packet_at else None
        return {
            "status": self.status,
            "last_packet_seconds_ago": round(age, 1) if age is not None else None,
            "origin": list(self.origin) if self.origin else None,
            "cell_size": self.cell_size,
            "capture_origin": list(self.capture_origin) if self.capture_origin else None,
            "window_detected": self.window_detected,
            "monitor": self.monitor_index,
            "state": dict(self.state),
            "gear": [self.gear[key] for key in sorted(self.gear)],
            "recent": list(self.recent)[:12],
            "last_error": self.last_error,
        }

    def _set_status(self, status):
        self.status = status
        if status == self._reported_status:
            return
        self._reported_status = status
        if self.on_status:
            self.on_status({
                "status": status,
                "origin": list(self.origin) if self.origin else None,
                "cell_size": self.cell_size,
                "capture_origin": (
                    list(self.capture_origin) if self.capture_origin else None
                ),
                "window_detected": self.window_detected,
                "monitor": self.monitor_index,
                "last_error": self.last_error,
            })

    @staticmethod
    def _window_origin():
        try:
            user32 = ctypes.windll.user32
            matches = []
            callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

            def visit(window, _):
                length = user32.GetWindowTextLengthW(window)
                if length and user32.IsWindowVisible(window):
                    title = ctypes.create_unicode_buffer(length + 1)
                    user32.GetWindowTextW(window, title, length + 1)
                    if "world of warcraft" in title.value.lower():
                        matches.append(window)
                        return False
                return True

            user32.EnumWindows(callback_type(visit), 0)
            window = matches[0] if matches else None
            if not window:
                return None
            point = wintypes.POINT(0, 0)
            if not user32.ClientToScreen(window, ctypes.byref(point)):
                return None
            return point.x, point.y
        except Exception:
            return None

    @classmethod
    def _nearest(cls, rgb):
        red, green, blue = rgb
        best_index = 0
        best_distance = None
        for index, color in enumerate(cls.PALETTE):
            distance = (
                (red - color[0]) ** 2 + (green - color[1]) ** 2 + (blue - color[2]) ** 2
            )
            if best_distance is None or distance < best_distance:
                best_index, best_distance = index, distance
        return best_index, best_distance or 0

    @classmethod
    def _marker_matches(cls, shot, origin_x, origin_y, cell_size):
        total_distance = 0
        try:
            for column, expected in enumerate(cls.MARKER):
                rgb = shot.pixel(
                    round(origin_x + (column + 0.5) * cell_size),
                    round(origin_y + 0.5 * cell_size),
                )
                actual, distance = cls._nearest(rgb)
                if actual != expected:
                    return False
                total_distance += distance
        except (IndexError, ValueError):
            return False
        return total_distance < 90000

    @staticmethod
    def _candidate_cell_sizes():
        # The addon draws eight game pixels per cell. Window maximization and
        # GPU scaling commonly turn that into fractional desktop pixels (for
        # example 1920x1080 -> 2560x1440 is 10.667 pixels per cell).
        preferred = [8.0, 32 / 3, 12.0, 6.0, 10.0, 11.0, 16.0]
        preferred.extend(value / 4 for value in range(16, 65))
        seen = set()
        result = []
        for value in preferred:
            key = round(value, 3)
            if key not in seen:
                seen.add(key)
                result.append(value)
        return result

    def _decode_candidate(self, shot, origin_x, origin_y, cell_size):
        symbols = []
        for index in range(self.GRID * self.GRID):
            row, column = divmod(index, self.GRID)
            rgb = shot.pixel(
                round(origin_x + (column + 0.5) * cell_size),
                round(origin_y + (row + 0.5) * cell_size),
            )
            symbol, _ = self._nearest(rgb)
            symbols.append(symbol)
        if tuple(symbols[: len(self.MARKER)]) != self.MARKER:
            return None
        data = bytearray()
        body = symbols[len(self.MARKER):]
        for index in range(0, len(body) - 1, 2):
            data.append(body[index] * 16 + body[index + 1])
        if len(data) < 5:
            return None
        version, sequence, packet_type, length = data[:4]
        if version != 1 or length > 63 or len(data) < 5 + length:
            return None
        raw = data[: 4 + length]
        expected_checksum = data[4 + length]
        if sum(raw) % 256 != expected_checksum:
            return None
        return sequence, packet_type, bytes(data[4: 4 + length])

    def _decode(self, shot):
        if self.origin and self.cell_size:
            decoded = self._decode_candidate(
                shot, self.origin[0], self.origin[1], self.cell_size
            )
            if decoded:
                return decoded
        search_margin = int(self.config.get("wow_pixel_search_margin", 16))
        for cell_size in self._candidate_cell_sizes():
            if cell_size * self.GRID > min(shot.width, shot.height):
                continue
            max_x = min(search_margin, int(shot.width - cell_size * self.GRID))
            max_y = min(search_margin, int(shot.height - cell_size * self.GRID))
            for origin_y in range(max(0, max_y) + 1):
                for origin_x in range(max(0, max_x) + 1):
                    if not self._marker_matches(
                        shot, origin_x, origin_y, cell_size
                    ):
                        continue
                    decoded = self._decode_candidate(
                        shot, origin_x, origin_y, cell_size
                    )
                    if decoded:
                        self.origin = (origin_x, origin_y)
                        self.cell_size = round(cell_size, 3)
                        return decoded
        self.origin = None
        self.cell_size = None
        return None

    @staticmethod
    def _u16(payload, offset):
        return payload[offset] + payload[offset + 1] * 256

    @staticmethod
    def _u32(payload, offset):
        return sum(payload[offset + index] << (8 * index) for index in range(4))

    @staticmethod
    def _text(payload, offset):
        return payload[offset:].decode("utf-8", errors="replace").strip("\x00 ")

    def _handle(self, sequence, packet_type, payload):
        if sequence == self.last_sequence:
            return
        self.last_sequence = sequence
        self.last_packet_at = time.time()
        self._set_status("live")
        event = None
        if packet_type == 1 and len(payload) >= 9:
            flags = payload[3]
            self.state = {
                "health": payload[0], "power": payload[1], "target_health": payload[2],
                "combat": bool(flags & 1), "resting": bool(flags & 2),
                "mounted": bool(flags & 4), "dead": bool(flags & 8),
                "has_target": bool(flags & 16), "hostile_target": bool(flags & 32),
                "level": payload[4], "target_level": payload[5],
                "target_classification": payload[6], "threat": payload[7],
                "group_size": payload[8],
            }
            event = {"kind": "state", **self.state}
        elif packet_type == 2 and len(payload) >= 8:
            item = {
                "kind": "gear", "slot": payload[0], "item_id": self._u32(payload, 1),
                "quality": payload[5], "item_level": self._u16(payload, 6),
                "name": self._text(payload, 8),
            }
            self.gear[item["slot"]] = item
            event = item
        elif packet_type == 3 and len(payload) >= 8:
            event = {
                "kind": "loot", "item_id": self._u32(payload, 0), "count": payload[4],
                "quality": payload[5], "item_level": self._u16(payload, 6),
                "name": self._text(payload, 8),
            }
        elif packet_type == 4:
            zone, _, subzone = self._text(payload, 0).partition("|")
            event = {"kind": "zone", "zone": zone, "subzone": subzone}
            self.state.update({"zone": zone, "subzone": subzone})
        elif packet_type == 5 and len(payload) >= 2:
            event = {
                "kind": "target", "level": payload[0],
                "classification": payload[1], "name": self._text(payload, 2),
            }
            self.state.update({
                "target_name": event["name"], "target_level": event["level"],
                "target_classification": event["classification"],
            })
        if event:
            if event["kind"] != "state":
                self.recent.appendleft({**event, "time": time.strftime("%H:%M:%S")})
            if self.on_packet:
                self.on_packet(event)

    def _run(self):
        try:
            with mss.MSS() as capture:
                while not self.stop_event.is_set():
                    monitor_index = int(self.config.get("monitor", 1))
                    monitor_index = max(1, min(monitor_index, len(capture.monitors) - 1))
                    self.monitor_index = monitor_index
                    monitor = capture.monitors[monitor_index]
                    window_origin = self._window_origin()
                    self.window_detected = window_origin is not None
                    left = window_origin[0] if window_origin else monitor["left"]
                    top = window_origin[1] if window_origin else monitor["top"]
                    self.capture_origin = (left, top)
                    capture_size = int(self.config.get("wow_pixel_capture_size", 260))
                    shot = capture.grab({
                        "left": left, "top": top,
                        "width": capture_size, "height": capture_size,
                    })
                    decoded = self._decode(shot)
                    if decoded:
                        self._handle(*decoded)
                    elif self.last_packet_at and time.time() - self.last_packet_at > 2:
                        self._set_status("signal_lost")
                    else:
                        self._set_status("searching")
                    self.stop_event.wait(0.2)
        except Exception as exc:
            self.last_error = str(exc)[:300]
            self._set_status("error")
