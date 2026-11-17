"""Low-frequency reads of the same public endpoints used by ChinaMoney."""
from collections.abc import Callable
import time

import requests

from .config import API_ROOT, SOURCE_URL


class SourceError(RuntimeError):
    pass


class ChinaMoneyClient:
    def __init__(self, progress: Callable[[str], None] | None = None,
                 session=None, sleep=time.sleep):
        self.session = session or requests.Session()
        self.session.headers.update({
            "User-Agent": "BondDaily/1.0 (personal daily market observation)",
            "Referer": SOURCE_URL,
            "Accept": "application/json",
        })
        self.progress = progress or (lambda _: None)
        self.sleep = sleep
        self.last_request = 0.0

    def close(self):
        self.session.close()

    def _post(self, endpoint: str, payload: dict) -> dict:
        last_error = ""
        for attempt in range(3):
            delay = 0.4 - (time.monotonic() - self.last_request)
            if delay > 0:
                self.sleep(delay)
            try:
                self.last_request = time.monotonic()
                response = self.session.post(API_ROOT + endpoint, data=payload,
                                             timeout=(8, 25))
                if response.status_code in (401, 403):
                    raise SourceError(f"来源拒绝访问（HTTP {response.status_code}），请稍后重试。")
                response.raise_for_status()
                value = response.json()
                if not isinstance(value, dict):
                    raise ValueError("响应不是 JSON 对象")
                head = value.get("head") or {}
                if str(head.get("rep_code", "200")) not in ("200", "0", "0000"):
                    raise SourceError(f"来源业务错误：{head.get('rep_message') or head.get('rep_code')}")
                if (value.get("data") or {}).get("errorMsg"):
                    raise SourceError(str(value["data"]["errorMsg"]))
                return value
            except SourceError:
                raise
            except (requests.RequestException, ValueError) as exc:
                last_error = str(exc)
                if attempt < 2:
                    self.progress(f"请求未成功，准备第 {attempt + 2}/3 次尝试…")
                    self.sleep(1.5 * (2 ** attempt))
        raise SourceError(f"来源请求失败，已尝试 3 次：{last_error[:300]}")

    def fetch_market(self) -> dict:
        self.progress("正在读取最新成交行情…")
        return self._post("cm-u-md-bond/CbtPri", {
            "flag": "1", "lang": "cn", "bondName": "",
        })

    def fetch_catalog(self) -> tuple[list[dict], list[dict]]:
        """Fetch both complete target categories; never cache a partial catalog."""
        catalog, raw_pages = [], []
        self.catalog_pages = raw_pages
        for category_code, title in (("100001", "国债"), ("100003", "政策性金融债")):
            page, pages, expected = 1, 1, None
            rows = []
            while page <= pages:
                self.progress(f"正在核对{title}代码清单 · 第 {page}/{pages} 页…")
                response = self._post("cm-u-bond-md/BondMarketInfoList2", {
                    "pageNo": str(page), "pageSize": "15", "bondType": category_code,
                    "bondName": "", "bondCode": "", "issueEnty": "",
                    "bondSpclPrjctVrty": "", "couponType": "", "issueYear": "",
                    "entyDefinedCode": "", "rtngShrt": "",
                })
                raw_pages.append(response)
                data = response.get("data") or {}
                try:
                    current_pages, current_total = int(data["pageTotal"]), int(data["total"])
                except (KeyError, ValueError, TypeError) as exc:
                    raise SourceError("债券清单分页结构发生变化，未更新缓存。") from exc
                if not 1 <= current_pages <= 1000 or current_total < 1:
                    raise SourceError("债券清单为空或分页数量异常，未更新缓存。")
                if expected is not None and (current_pages != pages or current_total != expected):
                    raise SourceError("读取过程中债券清单数量发生变化，请重新采集。")
                if "pageNo" in data and int(data["pageNo"]) != page - 1:
                    raise SourceError("来源返回的清单页码不符，未更新缓存。")
                pages, expected = current_pages, current_total
                items = data.get("resultList")
                if not isinstance(items, list) or not items:
                    raise SourceError("债券清单有空页，未更新缓存。")
                for item in items:
                    if str(item.get("bondTypeCode")) != category_code:
                        raise SourceError("来源债券类型与查询条件不符。")
                rows.extend(items)
                page += 1
            codes = [str(row.get("bondCode") or "") for row in rows]
            if not all(codes) or len(codes) != expected:
                raise SourceError(f"债券清单数量或代码校验失败：收到 {len(codes)} 条，预期 {expected} 条。")
            # Historical catalog entries can share a code (e.g. reopening records).
            # A code is usable only when its official type and issuer agree.
            by_code = {}
            for item in rows:
                code = str(item["bondCode"])
                previous = by_code.get(code)
                if previous:
                    identity = lambda row: (row.get("bondTypeCode"), row.get("entyDefinedCode") or row.get("entyFullName"))
                    if identity(previous) != identity(item):
                        raise SourceError(f"代码 {code} 对应不同官方券种或发行人，需核查来源。")
                    if str(previous.get("issueStartDate") or "") >= str(item.get("issueStartDate") or ""):
                        continue
                by_code[code] = item
            catalog.extend(by_code.values())
        return catalog, raw_pages
