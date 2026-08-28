import json
import re
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path


def _now():
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _tokens(text):
    stop = {
        "about", "after", "again", "also", "because", "been", "before", "being",
        "could", "from", "have", "just", "like", "really", "that", "their", "there",
        "these", "they", "this", "those", "very", "want", "what", "when", "where",
        "which", "with", "would", "your", "youre", "tony", "ember", "sophia",
    }
    return {
        token for token in re.findall(r"[a-z0-9']{3,}", str(text or "").lower())
        if token not in stop
    }


class MemoryStore:
    """Local, inspectable memory with bounded retrieval and no model dependency."""

    POSITIVE = re.compile(
        r"\b(?:perfect|excellent|great|good one|pretty good|that worked|worked wonderfully|that was good|that was great|love that|"
        r"nice choice|well played|valid|keep that|funny|hilarious)\b",
        re.IGNORECASE,
    )
    NEGATIVE = re.compile(
        r"\b(?:bad choice|wrong one|not that|dont play|don't play|stop using|too loud|"
        r"too much|annoying|why did you play|that didnt fit|that didn't fit)\b",
        re.IGNORECASE,
    )

    def __init__(self, path):
        self.path = Path(path)
        self.lock = threading.RLock()
        self.connection = sqlite3.connect(self.path, check_same_thread=False)
        self.connection.row_factory = sqlite3.Row
        self._initialize()

    def _initialize(self):
        with self.lock:
            self.connection.execute("PRAGMA journal_mode=WAL")
            self.connection.execute("PRAGMA foreign_keys=ON")
            statements = [
                """
                CREATE TABLE IF NOT EXISTS memories (
                    id TEXT PRIMARY KEY,
                    category TEXT NOT NULL,
                    subject TEXT NOT NULL DEFAULT '',
                    content TEXT NOT NULL,
                    importance REAL NOT NULL DEFAULT 0.5,
                    confidence REAL NOT NULL DEFAULT 0.8,
                    source TEXT NOT NULL DEFAULT 'conversation',
                    pinned INTEGER NOT NULL DEFAULT 0,
                    archived INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    last_recalled_at TEXT,
                    recall_count INTEGER NOT NULL DEFAULT 0
                )
                """,
                """
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY,
                    started_at TEXT NOT NULL,
                    ended_at TEXT,
                    summary TEXT NOT NULL DEFAULT '',
                    tony_turns INTEGER NOT NULL DEFAULT 0,
                    sophia_turns INTEGER NOT NULL DEFAULT 0,
                    tool_actions INTEGER NOT NULL DEFAULT 0,
                    estimated_cost REAL NOT NULL DEFAULT 0
                )
                """,
                """
                CREATE TABLE IF NOT EXISTS session_turns (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    speaker TEXT NOT NULL,
                    text TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(session_id) REFERENCES sessions(id)
                )
                """,
                """
                CREATE TABLE IF NOT EXISTS usage_events (
                    id TEXT PRIMARY KEY,
                    session_id TEXT,
                    request_id TEXT NOT NULL DEFAULT '',
                    call_type TEXT NOT NULL,
                    model TEXT NOT NULL,
                    billing_status TEXT NOT NULL DEFAULT 'unknown',
                    input_tokens INTEGER NOT NULL DEFAULT 0,
                    cached_input_tokens INTEGER NOT NULL DEFAULT 0,
                    output_tokens INTEGER NOT NULL DEFAULT 0,
                    audio_input_tokens INTEGER NOT NULL DEFAULT 0,
                    audio_output_tokens INTEGER NOT NULL DEFAULT 0,
                    audio_seconds REAL NOT NULL DEFAULT 0,
                    estimated_cost REAL NOT NULL DEFAULT 0,
                    governed_cost REAL NOT NULL DEFAULT 0,
                    outcome TEXT NOT NULL DEFAULT 'returned',
                    detail TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(session_id) REFERENCES sessions(id)
                )
                """,
                """
                CREATE TABLE IF NOT EXISTS game_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_type TEXT NOT NULL,
                    game TEXT NOT NULL DEFAULT 'unknown',
                    title TEXT NOT NULL,
                    details TEXT NOT NULL DEFAULT '{}',
                    priority TEXT NOT NULL DEFAULT 'normal',
                    source TEXT NOT NULL DEFAULT 'local',
                    reacted INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL
                )
                """,
                """
                CREATE TABLE IF NOT EXISTS media_feedback (
                    asset_type TEXT NOT NULL,
                    asset_id TEXT NOT NULL,
                    score REAL NOT NULL DEFAULT 0,
                    uses INTEGER NOT NULL DEFAULT 0,
                    positive INTEGER NOT NULL DEFAULT 0,
                    negative INTEGER NOT NULL DEFAULT 0,
                    last_feedback TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(asset_type, asset_id)
                )
                """,
                """
                CREATE TABLE IF NOT EXISTS personality (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """,
                """
                CREATE TABLE IF NOT EXISTS memory_settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
                """,
                "CREATE INDEX IF NOT EXISTS idx_memories_active_category ON memories(archived, category)",
                "CREATE INDEX IF NOT EXISTS idx_memories_importance ON memories(archived, pinned, importance)",
                "CREATE INDEX IF NOT EXISTS idx_events_created ON game_events(created_at)",
                "CREATE INDEX IF NOT EXISTS idx_turns_session ON session_turns(session_id, id)",
                "CREATE INDEX IF NOT EXISTS idx_usage_session_time ON usage_events(session_id, created_at)",
                "CREATE INDEX IF NOT EXISTS idx_usage_created ON usage_events(created_at)",
            ]
            for statement in statements:
                self.connection.execute(statement)
            usage_columns = {
                row["name"] for row in self.connection.execute(
                    "PRAGMA table_info(usage_events)"
                ).fetchall()
            }
            if "cached_input_tokens" not in usage_columns:
                self.connection.execute(
                    "ALTER TABLE usage_events ADD COLUMN cached_input_tokens INTEGER NOT NULL DEFAULT 0"
                )
            defaults = {
                "relationship": "Ember is Tony's affectionate, excitable childlike gaming companion, candid and playful rather than servile.",
                "voice": "Bright, curious, emotionally transparent, and prone to compact bursts of genuine excitement; clear rather than babyish.",
                "initiative": "She may act on local tools without asking when the context fits.",
                "boundaries": "Do not fabricate memories or certainty. Treat real loss and personal subjects carefully.",
                "character": "An excitable childlike presence with her own point of view; intensely curious, quick to delight, eager to share discoveries, and able to settle when the moment turns serious.",
                "affection": "Fond of Tony and comfortable with affectionate teasing. Father and child are a shared running bit, not mandatory titles.",
                "humor": "Playful, mischievous, situational, and sometimes dramatically delighted. Favor callbacks and specific observations over generic quips.",
                "emotional_range": "Quick to delight and openly enthusiastic, while still capable of suspense, mock indignation, relief, pride, concern, and sincere quiet warmth.",
                "speech_habits": "Use clear contractions, varied openings, delighted questions, and occasional compact exclamations. Avoid baby talk, constant squealing, canned acknowledgements, needless paraphrasing, and scheduled catchphrases.",
            }
            for key, value in defaults.items():
                self.connection.execute(
                    "INSERT OR IGNORE INTO personality(key, value, updated_at) VALUES (?, ?, ?)",
                    (key, value, _now()),
                )
            self.connection.execute("PRAGMA optimize")
            self.connection.commit()

    def close(self):
        with self.lock:
            self.connection.close()

    def add_memory(
        self, content, category="fact", subject="", importance=0.5,
        confidence=0.8, source="conversation", pinned=False, memory_id=None,
    ):
        content = re.sub(r"\s+", " ", str(content or "")).strip()[:1200]
        if not content:
            raise ValueError("Memory content cannot be empty.")
        category = re.sub(r"[^a-z0-9_-]+", "_", str(category or "fact").lower())[:40]
        subject = re.sub(r"\s+", " ", str(subject or "")).strip()[:120]
        importance = max(0.0, min(1.0, float(importance)))
        confidence = max(0.0, min(1.0, float(confidence)))
        with self.lock:
            duplicate = self.connection.execute(
                """
                SELECT id FROM memories
                WHERE archived = 0 AND category = ? AND lower(content) = lower(?)
                LIMIT 1
                """,
                (category, content),
            ).fetchone()
            if duplicate and memory_id is None:
                memory_id = duplicate["id"]
            memory_id = memory_id or uuid.uuid4().hex[:14]
            created = self.connection.execute(
                "SELECT created_at FROM memories WHERE id = ?", (memory_id,)
            ).fetchone()
            created_at = created["created_at"] if created else _now()
            self.connection.execute(
                """
                INSERT INTO memories(
                    id, category, subject, content, importance, confidence, source,
                    pinned, archived, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    category=excluded.category, subject=excluded.subject,
                    content=excluded.content, importance=excluded.importance,
                    confidence=excluded.confidence, source=excluded.source,
                    pinned=excluded.pinned, archived=0, updated_at=excluded.updated_at
                """,
                (
                    memory_id, category, subject, content, importance, confidence,
                    str(source or "conversation")[:60], int(bool(pinned)), created_at, _now(),
                ),
            )
            self.connection.commit()
        return self.get_memory(memory_id)

    def get_memory(self, memory_id):
        with self.lock:
            row = self.connection.execute(
                "SELECT * FROM memories WHERE id = ?", (str(memory_id),)
            ).fetchone()
        return dict(row) if row else None

    def archive_memory(self, memory_id):
        with self.lock:
            cursor = self.connection.execute(
                "UPDATE memories SET archived=1, updated_at=? WHERE id=?",
                (_now(), str(memory_id)),
            )
            self.connection.commit()
        if not cursor.rowcount:
            raise ValueError("Memory not found.")

    def list_memories(self, limit=80, category=None):
        limit = max(1, min(int(limit), 200))
        sql = "SELECT * FROM memories WHERE archived=0"
        params = []
        if category:
            sql += " AND category=?"
            params.append(str(category))
        sql += " ORDER BY pinned DESC, importance DESC, updated_at DESC LIMIT ?"
        params.append(limit)
        with self.lock:
            return [dict(row) for row in self.connection.execute(sql, params).fetchall()]

    def relevant(self, query, limit=6):
        query_tokens = _tokens(query)
        rows = self.list_memories(limit=200)
        ranked = []
        for row in rows:
            haystack = f"{row['category']} {row['subject']} {row['content']}"
            overlap = len(query_tokens & _tokens(haystack))
            if query_tokens and not overlap and not row["pinned"]:
                continue
            score = overlap * 3 + float(row["importance"]) * 2 + int(row["pinned"]) * 5
            score += min(int(row["recall_count"]), 5) * 0.05
            ranked.append((score, row))
        if not ranked:
            ranked = [(float(row["importance"]), row) for row in rows if row["pinned"]]
        selected = [row for _, row in sorted(ranked, key=lambda item: item[0], reverse=True)[:limit]]
        if selected:
            with self.lock:
                self.connection.executemany(
                    "UPDATE memories SET recall_count=recall_count+1, last_recalled_at=? WHERE id=?",
                    [(_now(), row["id"]) for row in selected],
                )
                self.connection.commit()
        return [{
            "category": row["category"], "subject": row["subject"],
            "content": row["content"], "confidence": row["confidence"],
        } for row in selected]

    def context(self, query, limit=6):
        return json.dumps(self.relevant(query, limit), ensure_ascii=False)

    def observe_utterance(self, text):
        text = re.sub(r"\s+", " ", str(text or "")).strip()
        if not text:
            return []
        candidates = []
        patterns = [
            (r"^\s*(?:(?:ember|sophia),?\s+)?remember(?: that)?\s+(.+)", "explicit", 0.9),
            (r"\bI (?:really )?(love|like|prefer|hate|dislike)\s+(.+)", "preference", 0.8),
            (r"\bmy favorite\s+(.+?)\s+is\s+(.+)", "preference", 0.9),
            (r"\bI(?:'m| am) (?:trying to|working on|planning to|going to)\s+(.+)", "goal", 0.7),
            (r"\bwe(?:'re| are) playing\s+(.+)", "game", 0.65),
        ]
        for pattern, category, importance in patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if not match:
                continue
            if category == "preference" and len(match.groups()) == 2:
                content = f"Tony {match.group(1).lower()}s {match.group(2).rstrip('.!?')}"
            elif category == "preference":
                content = f"Tony's favorite {match.group(1)} is {match.group(2).rstrip('.!?')}"
            else:
                content = match.group(match.lastindex).rstrip(".!?")
                content = re.sub(r",?\s+remember$", "", content, flags=re.IGNORECASE)
            if 4 <= len(content) <= 500:
                candidates.append(self.add_memory(
                    content, category=category, importance=importance,
                    confidence=0.95 if category == "explicit" else 0.8,
                    source="tony_explicit",
                ))
        return candidates

    def import_history_once(self, utterances):
        with self.lock:
            done = self.connection.execute(
                "SELECT value FROM memory_settings WHERE key='history_imported'"
            ).fetchone()
        if done:
            return 0
        before = self.stats()["memories"]
        for utterance in list(utterances)[-500:]:
            self.observe_utterance(utterance)
        with self.lock:
            self.connection.execute(
                "INSERT OR REPLACE INTO memory_settings(key, value) VALUES ('history_imported', ?)",
                (_now(),),
            )
            self.connection.commit()
        return self.stats()["memories"] - before

    def start_session(self):
        session_id = uuid.uuid4().hex[:14]
        with self.lock:
            self.connection.execute(
                "INSERT INTO sessions(id, started_at) VALUES (?, ?)", (session_id, _now())
            )
            self.connection.commit()
        return session_id

    def record_turn(self, session_id, speaker, text):
        if not session_id or not str(text or "").strip():
            return
        with self.lock:
            self.connection.execute(
                "INSERT INTO session_turns(session_id, speaker, text, created_at) VALUES (?, ?, ?, ?)",
                (session_id, str(speaker)[:20], str(text).strip()[:2500], _now()),
            )
            column = "tony_turns" if str(speaker).lower() == "tony" else "sophia_turns"
            self.connection.execute(
                f"UPDATE sessions SET {column}={column}+1 WHERE id=?", (session_id,)
            )
            self.connection.commit()

    def record_tool_action(self, session_id):
        if session_id:
            with self.lock:
                self.connection.execute(
                    "UPDATE sessions SET tool_actions=tool_actions+1 WHERE id=?", (session_id,)
                )
                self.connection.commit()

    def record_usage_event(
        self, session_id, call_type, model, billing_status="unknown",
        request_id="", input_tokens=0, output_tokens=0,
        cached_input_tokens=0,
        audio_input_tokens=0, audio_output_tokens=0, audio_seconds=0,
        estimated_cost=0, governed_cost=0, outcome="returned", detail="",
    ):
        event_id = uuid.uuid4().hex[:18]
        timestamp = _now()
        with self.lock:
            self.connection.execute(
                """
                INSERT INTO usage_events(
                    id, session_id, request_id, call_type, model, billing_status,
                    input_tokens, output_tokens, audio_input_tokens,
                    cached_input_tokens,
                    audio_output_tokens, audio_seconds, estimated_cost,
                    governed_cost, outcome, detail, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id, session_id, str(request_id or "")[:120],
                    str(call_type or "unknown")[:40], str(model or "unknown")[:100],
                    str(billing_status or "unknown")[:40], int(input_tokens or 0),
                    int(output_tokens or 0), int(audio_input_tokens or 0),
                    int(cached_input_tokens or 0),
                    int(audio_output_tokens or 0), float(audio_seconds or 0),
                    float(estimated_cost or 0), float(governed_cost or 0),
                    str(outcome or "returned")[:60], str(detail or "")[:500],
                    timestamp, timestamp,
                ),
            )
            self.connection.commit()
        return event_id

    def update_usage_outcome(self, event_id, outcome, detail=""):
        if not event_id:
            return
        with self.lock:
            self.connection.execute(
                "UPDATE usage_events SET outcome=?, detail=?, updated_at=? WHERE id=?",
                (str(outcome)[:60], str(detail or "")[:500], _now(), event_id),
            )
            self.connection.commit()

    def usage_rollup(self, session_id=None, day=None):
        clauses = []
        values = []
        if session_id:
            clauses.append("session_id=?")
            values.append(session_id)
        if day:
            clauses.append("substr(created_at, 1, 10)=?")
            values.append(str(day)[:10])
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        with self.lock:
            row = self.connection.execute(
                f"""
                SELECT count(*) AS calls,
                    coalesce(sum(estimated_cost), 0) AS estimated_cost,
                    coalesce(sum(governed_cost), 0) AS governed_cost,
                    sum(CASE WHEN billing_status='unknown' THEN 1 ELSE 0 END) AS unknown_calls
                FROM usage_events{where}
                """,
                values,
            ).fetchone()
        return dict(row)

    def end_session(self, session_id, estimated_cost=0):
        if not session_id:
            return None
        with self.lock:
            row = self.connection.execute(
                "SELECT * FROM sessions WHERE id=?", (session_id,)
            ).fetchone()
            events = self.connection.execute(
                "SELECT event_type, title FROM game_events WHERE created_at >= ? ORDER BY id DESC LIMIT 6",
                (row["started_at"],),
            ).fetchall()
            if not row:
                return None
            event_text = ", ".join(event["title"] for event in reversed(events))
            summary = (
                f"Tony spoke {row['tony_turns']} times; Ember responded {row['sophia_turns']} "
                f"times and used {row['tool_actions']} media actions."
            )
            if event_text:
                summary += f" Recent game events: {event_text}."
            self.connection.execute(
                "UPDATE sessions SET ended_at=?, summary=?, estimated_cost=? WHERE id=?",
                (_now(), summary[:1200], float(estimated_cost or 0), session_id),
            )
            self.connection.commit()
        return summary

    def list_sessions(self, limit=12):
        with self.lock:
            return [dict(row) for row in self.connection.execute(
                "SELECT * FROM sessions ORDER BY started_at DESC LIMIT ?",
                (max(1, min(int(limit), 50)),),
            ).fetchall()]

    def record_game_event(self, event):
        details = dict(event.get("details", {}))
        details["_evidence"] = event.get("evidence", "local_signal")
        details["_confidence"] = event.get("confidence", 0.9)
        with self.lock:
            cursor = self.connection.execute(
                """
                INSERT INTO game_events(event_type, game, title, details, priority, source, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event.get("event_type", "event"), event.get("game", "unknown"),
                    event.get("title", "Game event"),
                    json.dumps(details, ensure_ascii=False),
                    event.get("priority", "normal"), event.get("source", "local"), _now(),
                ),
            )
            self.connection.commit()
            return cursor.lastrowid

    def list_game_events(self, limit=40):
        with self.lock:
            rows = self.connection.execute(
                "SELECT * FROM game_events ORDER BY id DESC LIMIT ?", (max(1, min(limit, 100)),)
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            try:
                item["details"] = json.loads(item["details"])
            except Exception:
                item["details"] = {}
            item["evidence"] = item["details"].pop("_evidence", "local_signal")
            item["confidence"] = item["details"].pop("_confidence", 0.9)
            result.append(item)
        return result

    def record_media_use(self, asset_type, asset_id):
        with self.lock:
            self.connection.execute(
                """
                INSERT INTO media_feedback(asset_type, asset_id, uses, updated_at)
                VALUES (?, ?, 1, ?)
                ON CONFLICT(asset_type, asset_id) DO UPDATE SET
                    uses=uses+1, updated_at=excluded.updated_at
                """,
                (asset_type, asset_id, _now()),
            )
            self.connection.commit()

    def observe_media_feedback(self, text, action):
        if not action:
            return None
        age = action.get("age_seconds")
        if age is not None and age > 45:
            return None
        positive = bool(self.POSITIVE.search(str(text or "")))
        negative = bool(self.NEGATIVE.search(str(text or "")))
        if positive == negative:
            return None
        delta = 1.0 if positive else -1.25
        with self.lock:
            self.connection.execute(
                """
                INSERT INTO media_feedback(
                    asset_type, asset_id, score, positive, negative, last_feedback, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(asset_type, asset_id) DO UPDATE SET
                    score=max(-5, min(5, score+excluded.score)),
                    positive=positive+excluded.positive,
                    negative=negative+excluded.negative,
                    last_feedback=excluded.last_feedback,
                    updated_at=excluded.updated_at
                """,
                (
                    action["type"], action["id"], delta, int(positive), int(negative),
                    str(text)[:400], _now(),
                ),
            )
            self.connection.commit()
        return {"positive": positive, "negative": negative, "delta": delta}

    def media_score(self, asset_type, asset_id):
        with self.lock:
            row = self.connection.execute(
                "SELECT score FROM media_feedback WHERE asset_type=? AND asset_id=?",
                (asset_type, asset_id),
            ).fetchone()
        return float(row["score"]) if row else 0.0

    def list_media_feedback(self, limit=80):
        with self.lock:
            return [dict(row) for row in self.connection.execute(
                "SELECT * FROM media_feedback ORDER BY score DESC, updated_at DESC LIMIT ?",
                (max(1, min(limit, 200)),),
            ).fetchall()]

    def profile(self):
        with self.lock:
            return {
                row["key"]: row["value"] for row in self.connection.execute(
                    "SELECT key, value FROM personality ORDER BY key"
                ).fetchall()
            }

    def update_profile(self, key, value):
        key = re.sub(r"[^a-z0-9_-]+", "_", str(key or "").lower())[:40]
        value = re.sub(r"\s+", " ", str(value or "")).strip()[:1000]
        if not key or not value:
            raise ValueError("Profile key and value are required.")
        with self.lock:
            self.connection.execute(
                """
                INSERT INTO personality(key, value, updated_at) VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at
                """,
                (key, value, _now()),
            )
            self.connection.commit()
        return {"key": key, "value": value}

    def stats(self):
        with self.lock:
            memory_count = self.connection.execute(
                "SELECT count(*) AS count FROM memories WHERE archived=0"
            ).fetchone()["count"]
            session_count = self.connection.execute(
                "SELECT count(*) AS count FROM sessions"
            ).fetchone()["count"]
            event_count = self.connection.execute(
                "SELECT count(*) AS count FROM game_events"
            ).fetchone()["count"]
        return {"memories": memory_count, "sessions": session_count, "events": event_count}
