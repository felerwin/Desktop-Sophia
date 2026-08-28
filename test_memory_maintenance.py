import tempfile
import unittest
from pathlib import Path

from memory_store import MemoryStore


class MemoryMaintenanceTests(unittest.TestCase):
    def test_maintenance_only_archives_stale_low_value_unpinned_memory(self):
        with tempfile.TemporaryDirectory() as folder:
            store = MemoryStore(Path(folder) / "memory.db")
            stale = store.add_memory("forgettable detail", importance=0.1)
            pinned = store.add_memory("important pinned detail", importance=0.1, pinned=True)
            valuable = store.add_memory("valuable detail", importance=0.9)
            store.connection.execute(
                "UPDATE memories SET updated_at='2000-01-01T00:00:00+00:00'"
            )
            store.connection.commit()
            preview = store.maintain(stale_days=30)
            self.assertEqual(preview["ids"], [stale["id"]])
            self.assertEqual(len(store.list_memories()), 3)
            applied = store.maintain(stale_days=30, apply=True)
            self.assertEqual(applied["count"], 1)
            remaining = {item["id"] for item in store.list_memories()}
            self.assertIn(pinned["id"], remaining)
            self.assertIn(valuable["id"], remaining)
            self.assertNotIn(stale["id"], remaining)
            store.close()


if __name__ == "__main__":
    unittest.main()
