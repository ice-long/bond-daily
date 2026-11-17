from copy import deepcopy
from datetime import datetime
from io import BytesIO, StringIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock

from openpyxl import load_workbook
import pandas as pd
import requests

from bonddesk.config import CHINA_TZ
from bonddesk.exports import csv_bytes, xlsx_bytes
from bonddesk.model import category_for, normalize
from bonddesk.service import collect, collection_lock
from bonddesk.source import ChinaMoneyClient, SourceError
from bonddesk.storage import Store


AT = datetime(2026, 11, 16, 21, 0, tzinfo=CHINA_TZ)
CATALOG = [
    {"bondCode": "260001", "bondName": "26附息国债01", "bondTypeCode": "100001", "entyFullName": "财政部"},
    {"bondCode": "09260207", "bondName": "26国开清发07", "bondTypeCode": "100003", "entyFullName": "国家开发银行(原:国家开发银行股份有限公司)"},
    {"bondCode": "260401", "bondName": "26农发01", "bondTypeCode": "100003", "entyFullName": "中国农业发展银行"},
    {"bondCode": "260301", "bondName": "26进出01", "bondTypeCode": "100003", "entyFullName": "中国进出口银行"},
]
CAT_MAP = {row["bondCode"]: row for row in CATALOG}


def market(date="2026-11-16", volume="12.5000"):
    return {"data": {"showDateEN": datetime.fromisoformat(date).strftime("%d/%m/%Y") + " 20:00"},
            "records": [{"bondcode": row["bondCode"], "abdAssetEncdShrtDesc": row["bondName"],
                         "termToMaturity": "9.30Y", "dmiLatestRate": "101.50",
                         "dmiLatestContraRate": "1.7439", "dmiWghtdContraRate": "1.7135",
                         "bp": "4.25", "dmiTtlTradedAmnt": volume,
                         "showDate": date + " 18:33:06"} for row in CATALOG]}


class FakeClient:
    def __init__(self, payload=None, error=None, catalog_error=None):
        self.payload = market() if payload is None else payload
        self.error, self.catalog_error = error, catalog_error
        self.catalog_calls = 0

    def fetch_market(self):
        if self.error:
            raise SourceError(self.error)
        return self.payload

    def fetch_catalog(self):
        self.catalog_calls += 1
        if self.catalog_error:
            raise SourceError(self.catalog_error)
        return CATALOG, [{"data": {"resultList": CATALOG}}]


class ModelTests(unittest.TestCase):
    def test_all_four_categories_and_zero_prefix(self):
        data = normalize(market(), CAT_MAP, AT)
        self.assertEqual({row["category"] for row in data["records"]}, {"国债", "国开债", "农发债", "进出口行债"})
        self.assertEqual(data["records"][1]["code"], "09260207")
        self.assertEqual(data["records"][0]["volume"], 12.5)

    def test_missing_is_not_zero_and_negative_yield_is_valid(self):
        payload = market()
        payload["records"][0].update(dmiTtlTradedAmnt="---", bp=None, dmiLatestContraRate="-0.05")
        data = normalize(payload, CAT_MAP, AT)
        self.assertIsNone(data["records"][0]["volume"])
        self.assertEqual(data["missing"]["volume"], 1)
        self.assertEqual(data["records"][0]["latest_yield"], -0.05)

    def test_old_date_and_stale_individual_quote(self):
        payload = market("2026-11-13")
        payload["records"][0]["showDate"] = "2026-11-12 15:00:00"
        data = normalize(payload, CAT_MAP, AT)
        self.assertEqual(data["source_date"], "2026-11-13")
        self.assertEqual(data["records"][0]["quote_state"], "旧行情")

    def test_bad_date_empty_response_and_schema_changes_rejected(self):
        cases = [market("2026-11-17"), {"data": {}, "records": []}, market()]
        cases[2]["records"][0].pop("dmiTtlTradedAmnt")
        for payload in cases:
            with self.subTest(payload=payload), self.assertRaises(SourceError):
                normalize(payload, CAT_MAP, AT)

    def test_bad_values_and_conflicting_dates_rejected(self):
        for field, value in (("dmiLatestRate", "error"), ("dmiTtlTradedAmnt", "-2"),
                             ("showDate", "2026-11-17 10:00:00")):
            payload = market()
            payload["records"][0][field] = value
            with self.subTest(field=field), self.assertRaises(SourceError):
                normalize(payload, CAT_MAP, AT)

    def test_duplicates_never_increase_volume(self):
        payload = market()
        payload["records"].append(deepcopy(payload["records"][0]))
        result = normalize(payload, CAT_MAP, AT)
        self.assertEqual(len(result["records"]), 4)
        self.assertEqual(result["duplicates"], 1)
        payload["records"][-1]["dmiLatestRate"] = "102"
        with self.assertRaises(SourceError):
            normalize(payload, CAT_MAP, AT)

    def test_complementary_duplicates_preserve_disclosed_volume(self):
        payload = market()
        partial = dict(payload["records"][0], dmiTtlTradedAmnt="---")
        payload["records"].insert(0, partial)
        result = normalize(payload, CAT_MAP, AT)
        self.assertEqual(len(result["records"]), 4)
        self.assertEqual(result["records"][0]["volume"], 12.5)
        self.assertEqual(payload["records"][0]["dmiTtlTradedAmnt"], "---")
        self.assertEqual(result["duplicates"], 1)

    def test_name_does_not_override_official_classification(self):
        payload = market()
        payload["records"].append(dict(payload["records"][0], bondcode="999999", abdAssetEncdShrtDesc="26国开新券"))
        result = normalize(payload, CAT_MAP, AT)
        self.assertEqual(len(result["unknown"]), 1)
        self.assertEqual(len(result["records"]), 4)
        self.assertIsNone(category_for({"bondTypeCode": "100003", "entyFullName": "未知发行人"}))


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(Path(self.temp.name))

    def perform(self, client=None):
        return collect(self.store, client or FakeClient(), clock=lambda: AT)

    def test_repeat_capture_persistence_and_raw_audit(self):
        first = self.perform()
        second = self.perform(FakeClient(market(volume="15")))
        self.assertTrue(first.ok and second.ok)
        reopened = Store(Path(self.temp.name))
        run = reopened.daily_run("2026-11-16")
        self.assertEqual(run["id"], second.run_id)
        frame = reopened.quotes(run["id"])
        self.assertEqual(len(frame), 4)
        self.assertEqual(frame.volume.sum(), 60)
        self.assertEqual(len(reopened.recent_runs()), 2)
        self.assertEqual(len(reopened.history("09260207")), 1)
        self.assertTrue((Path(self.temp.name) / run["raw_path"]).exists())

    def test_failure_preserves_last_good_result(self):
        first = self.perform()
        failed = self.perform(FakeClient(error="网络超时"))
        self.assertFalse(failed.ok)
        self.assertEqual(self.store.daily_run("2026-11-16")["id"], first.run_id)
        self.assertEqual(self.store.recent_runs().iloc[0]["status"], "failed")

    def test_first_catalog_failure_does_not_publish(self):
        result = self.perform(FakeClient(catalog_error="清单分页失败"))
        self.assertFalse(result.ok)
        self.assertEqual(self.store.dates(), [])

    def test_cached_catalog_fallback_is_visible(self):
        self.store.save_catalog(CATALOG, "2026-11-13")
        result = self.perform(FakeClient(catalog_error="清单分页失败"))
        self.assertTrue(result.ok)
        warnings = json.loads(self.store.daily_run("2026-11-16")["warnings_json"])
        self.assertIn("2026-11-13", warnings[0])

    def test_old_date_never_creates_today_and_is_flagged(self):
        result = self.perform(FakeClient(market("2026-11-13")))
        self.assertTrue(result.ok)
        self.assertEqual(self.store.dates(), ["2026-11-13"])
        self.assertIn("未记为今天", self.store.daily_run("2026-11-13")["warnings_json"])

    def test_no_carry_forward_when_bond_absent_in_new_batch(self):
        self.perform()
        payload = market()
        payload["records"] = payload["records"][1:]
        self.assertTrue(self.perform(FakeClient(payload)).ok)
        hist = self.store.history("260001")
        self.assertTrue(pd.isna(hist.iloc[0]["net_price"]))

    def test_older_source_update_cannot_replace_newer_snapshot(self):
        first = self.perform()
        payload = market(volume="2")
        payload["data"]["showDateEN"] = "16/11/2026 19:00"
        self.perform(FakeClient(payload))
        self.assertEqual(self.store.daily_run("2026-11-16")["id"], first.run_id)

    def test_concurrent_collections_are_blocked(self):
        with collection_lock(self.store.directory):
            result = self.perform()
        self.assertFalse(result.ok)
        self.assertIn("正在运行", result.message)

    def test_interrupted_run_recovered_without_publishing(self):
        self.store.start_run("interrupted", AT.isoformat())
        self.assertTrue(self.perform().ok)
        with self.store.connect() as conn:
            row = conn.execute("SELECT status FROM runs WHERE id='interrupted'").fetchone()
        self.assertEqual(row[0], "failed")


class ExportTests(unittest.TestCase):
    def test_filtered_rows_zero_prefix_and_no_formulas(self):
        records = normalize(market(), CAT_MAP, AT)["records"]
        frame = pd.DataFrame(records).iloc[[1]].copy()
        frame["source_date"] = "2026-11-16"
        frame["name"] = '=HYPERLINK("https://example.invalid")'
        wb = load_workbook(BytesIO(xlsx_bytes(frame)))
        sheet = wb.active
        self.assertEqual(sheet.max_row, 2)
        self.assertEqual(sheet["B2"].value, "09260207")
        self.assertEqual(sheet["B2"].data_type, "s")
        self.assertEqual(sheet["C2"].data_type, "s")
        csv = csv_bytes(frame).decode("utf-8-sig")
        read = pd.read_csv(StringIO(csv), dtype=str)
        self.assertEqual(read.iloc[0]["债券代码"], "09260207")
        self.assertTrue(read.iloc[0]["债券简称"].startswith("'="))
        self.assertIn("交易量（亿，来源口径）", csv)


class HttpTests(unittest.TestCase):
    def test_catalog_same_code_same_issuer_merges_but_conflict_rejects(self):
        client = ChinaMoneyClient(sleep=lambda _: None)
        self.addCleanup(client.close)
        first = dict(CATALOG[0], issueStartDate="2026-01-01")
        reopening = dict(first, issueStartDate="2026-02-01")
        gov_page = {"data": {"pageTotal": 1, "total": 2, "pageNo": 0,
                             "resultList": [first, reopening]}}
        policy_page = {"data": {"pageTotal": 1, "total": 3, "pageNo": 0,
                                "resultList": CATALOG[1:]}}
        client._post = Mock(side_effect=[gov_page, policy_page])
        rows, pages = client.fetch_catalog()
        self.assertEqual(len(rows), 4)
        self.assertEqual(rows[0]["issueStartDate"], "2026-02-01")
        self.assertEqual(len(pages[0]["data"]["resultList"]), 2)
        self.assertEqual(client._post.call_args_list[0].args[1]["pageSize"], "15")
        reopening["entyFullName"] = "不同发行人"
        client._post = Mock(return_value=gov_page)
        with self.assertRaises(SourceError):
            client.fetch_catalog()

    def test_transient_failure_retries_and_auth_failure_stops(self):
        session = Mock()
        session.headers = {}
        response = Mock(status_code=200)
        response.json.return_value = market()
        session.post.side_effect = [requests.ConnectionError("offline"), response]
        client = ChinaMoneyClient(session=session, sleep=lambda _: None)
        self.assertEqual(client.fetch_market(), market())
        self.assertEqual(session.post.call_count, 2)
        session.post.reset_mock()
        session.post.side_effect = [Mock(status_code=403)]
        with self.assertRaises(SourceError):
            client.fetch_market()
        self.assertEqual(session.post.call_count, 1)

    def test_catalog_rejects_incomplete_page(self):
        client = ChinaMoneyClient(sleep=lambda _: None)
        self.addCleanup(client.close)
        client._post = Mock(return_value={"data": {"pageTotal": 1, "total": 2, "resultList": CATALOG[:1]}})
        with self.assertRaises(SourceError):
            client.fetch_catalog()


if __name__ == "__main__":
    unittest.main()
