import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

from bonddesk.service import collect
from bonddesk.storage import Store
from test_core import FakeClient, AT


class DashboardTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.env = patch.dict(os.environ, {"BOND_DATA_DIR": self.temp.name})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.store = Store(Path(self.temp.name))
        self.app_file = str(Path(__file__).resolve().parents[1] / "app.py")

    def test_empty_state_is_usable_without_network(self):
        with patch("bonddesk.source.ChinaMoneyClient.fetch_market", side_effect=AssertionError("unexpected network")):
            app = AppTest.from_file(self.app_file, default_timeout=20).run()
        self.assertEqual(len(app.exception), 0)
        self.assertIn("还没有本地行情", app.info[0].value)
        self.assertIn("采集最新行情", app.button[0].label)

    def test_filter_history_and_empty_search_without_new_collection(self):
        self.assertTrue(collect(self.store, FakeClient(), clock=lambda: AT).ok)
        with patch("bonddesk.source.ChinaMoneyClient.fetch_market", side_effect=AssertionError("unexpected network")):
            app = AppTest.from_file(self.app_file, default_timeout=20).run()
            self.assertEqual(len(app.exception), 0)
            self.assertEqual(len(app.dataframe[0].value), 4)
            app.text_input[0].set_value("09260207").run()
            self.assertEqual(len(app.exception), 0)
            self.assertEqual(app.dataframe[0].value.code.tolist(), ["09260207"])
            app.radio[0].set_value("成交净价").run()
            self.assertEqual(len(app.exception), 0)
            app.text_input[0].set_value("没有这只债").run()
            self.assertEqual(len(app.exception), 0)
            self.assertEqual(len(app.dataframe[0].value), 0)
        self.assertEqual(len(self.store.recent_runs()), 1)


if __name__ == "__main__":
    unittest.main()
