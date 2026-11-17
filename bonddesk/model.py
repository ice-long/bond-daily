from datetime import datetime
from decimal import Decimal, InvalidOperation
import math
import re

from .config import CHINA_TZ
from .source import SourceError

EMPTY = {"", "-", "--", "---", "nan", "NaN", "null", "None"}
METRICS = {
    "net_price": "dmiLatestRate",
    "latest_yield": "dmiLatestContraRate",
    "weighted_yield": "dmiWghtdContraRate",
    "change_bp": "bp",
    "volume": "dmiTtlTradedAmnt",
}


def number(value, field: str):
    if value is None or str(value).strip() in EMPTY:
        return None
    try:
        result = Decimal(str(value).strip().replace(",", ""))
    except InvalidOperation as exc:
        raise SourceError(f"{field} 出现无法识别的数值：{str(value)[:60]}") from exc
    if not result.is_finite():
        raise SourceError(f"{field} 出现非有限数值。")
    if field in ("volume", "net_price") and result < 0:
        raise SourceError(f"{field} 出现负值，需核查来源。")
    converted = float(result)
    if not math.isfinite(converted):
        raise SourceError(f"{field} 的数值超出可保存范围。")
    return converted


def category_for(item: dict) -> str | None:
    if str(item.get("bondTypeCode")) == "100001":
        return "国债"
    if str(item.get("bondTypeCode")) != "100003":
        return None
    issuer = str(item.get("entyFullName") or "").strip()
    for key, category in (("国家开发银行", "国开债"), ("中国农业发展银行", "农发债"),
                          ("中国进出口银行", "进出口行债")):
        if issuer.startswith(key):
            return category
    return None


def source_time(payload: dict) -> datetime:
    value = (payload.get("data") or {}).get("showDateEN")
    if not value:
        raise SourceError("来源未提供包含年份的行情日期，不能归入每日数据。")
    for pattern in ("%d/%m/%Y %H:%M", "%d/%m/%Y %H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(value, pattern).replace(tzinfo=CHINA_TZ)
        except (ValueError, TypeError):
            continue
    raise SourceError(f"无法识别来源行情日期：{value}")


def remaining_years(value) -> float | None:
    match = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*([YMD])\s*", str(value or ""), re.I)
    if not match:
        return None
    amount, unit = float(match[1]), match[2].upper()
    return amount / {"Y": 1, "M": 12, "D": 365}[unit]


def merge_quote(first: dict, second: dict) -> dict:
    """Coalesce missing fields only; never sum or silently replace conflicting prices."""
    merged = dict(first)
    field_names = {field: metric for metric, field in METRICS.items()}
    critical = (*field_names, "showDate", "termToMaturity", "abdAssetEncdShrtDesc", "inptTp")
    for key in critical:
        left, right = first.get(key), second.get(key)
        left_empty = left is None or str(left).strip() in EMPTY
        right_empty = right is None or str(right).strip() in EMPTY
        if not left_empty and not right_empty:
            a = number(left, field_names[key]) if key in field_names else str(left).strip()
            b = number(right, field_names[key]) if key in field_names else str(right).strip()
            if a != b:
                raise SourceError(f"债券 {first['bondcode']} 出现相互冲突的重复行情（{key}）。")
        elif left_empty and not right_empty:
            merged[key] = right
    return merged


def normalize(payload: dict, catalog: dict[str, dict], fetched_at: datetime) -> dict:
    published = source_time(payload)
    if published.date() > fetched_at.date():
        raise SourceError("来源日期晚于本机北京时间，请核对时间后重试。")
    raw = payload.get("records")
    if not isinstance(raw, list) or not raw:
        raise SourceError("来源未返回行情记录，已有成功数据保持不变。")
    records, unknown, duplicates, seen = [], [], 0, {}
    for item in raw:
        if not isinstance(item, dict) or not item.get("bondcode"):
            raise SourceError("行情记录缺少债券代码，来源结构可能发生变化。")
        code = str(item["bondcode"])
        if code in seen:
            duplicates += 1
            if category_for(catalog.get(code, {})):
                seen[code] = merge_quote(seen[code], item)
            continue
        seen[code] = dict(item)
    excluded = 0
    for code, item in seen.items():
        info = catalog.get(code)
        name = str(item.get("abdAssetEncdShrtDesc") or "")
        category = category_for(info or {})
        if not category:
            if info or re.search(r"国债|国开|农发|进出", name):
                unknown.append({"code": code, "name": name,
                                "reason": "官方清单未匹配到已确认的券种及发行人", "raw": item})
            else:
                excluded += 1
            continue
        missing_keys = set(METRICS.values()) - item.keys()
        if missing_keys:
            raise SourceError("行情接口缺少预期字段：" + ", ".join(sorted(missing_keys)))
        row_time = str(item.get("showDate") or "").strip()
        state = "时间未披露"
        parsed = None
        if row_time and row_time not in EMPTY:
            try:
                parsed = datetime.fromisoformat(row_time).replace(tzinfo=CHINA_TZ)
            except ValueError as exc:
                raise SourceError(f"债券 {code} 的更新时间格式异常。") from exc
            if parsed.date() > published.date():
                raise SourceError(f"债券 {code} 的更新时间与来源日期冲突。")
            state = "当日行情" if parsed.date() == published.date() else "旧行情"
        record = {
            "code": code, "name": name or info["bondName"], "category": category,
            "issuer": info.get("entyFullName", ""),
            "term_raw": str(item.get("termToMaturity") or ""),
            "term_years": remaining_years(item.get("termToMaturity")),
            "record_time": parsed.isoformat() if parsed else None, "quote_state": state,
        }
        record.update({key: number(item[field], key) for key, field in METRICS.items()})
        records.append(record)
    if not records:
        raise SourceError("没有识别到国债或三类政金债，未发布本次结果。")
    missing = {key: sum(row[key] is None for row in records) for key in METRICS}
    return {"source_time": published.isoformat(), "source_date": published.date().isoformat(),
            "records": records, "unknown": unknown, "missing": missing,
            "raw_count": len(raw), "excluded_count": excluded, "duplicates": duplicates}
