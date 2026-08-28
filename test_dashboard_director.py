import tempfile
import threading
import unittest
from pathlib import Path

from dashboard_server import DashboardHub


class DashboardDirectorTests(unittest.TestCase):
    def test_simulation_is_offline_and_does_not_require_live_services(self):
        with tempfile.TemporaryDirectory() as folder:
            hub = DashboardHub(Path(folder), {}, threading.Event())
            result = hub.simulate_director({"event_type": "boss_start", "salience": 9})
        self.assertEqual(result["decision"]["intent"], "warn_or_rally")
        self.assertTrue(result["performance"]["interrupt"])


if __name__ == "__main__":
    unittest.main()
