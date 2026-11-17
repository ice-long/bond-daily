"""Build the shareable dashboard from synthetic or authorized daily snapshots."""

import argparse
from collections import defaultdict
from datetime import date, datetime
import json
import math
import os
from pathlib import Path
import re
import shutil
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from bonddesk.config import CATEGORIES, CHINA_TZ, ROOT


MAX_FEED_BYTES = 25 * 1024 * 1024
NUMERIC_FIELDS = ("term_years", "net_price", "latest_yield", "weighted_yield", "change_bp", "volume")
TREND_FIELDS = ("net_price", "latest_yield", "volume")


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def fetch_feed():
    url = os.environ.get("BOND_FEED_URL", "").strip()
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
        raise ValueError("实时发布需要在 BOND_FEED_URL 中配置获准分发的 HTTPS 数据地址。")
    headers = {"Accept": "application/json", "User-Agent": "BondDailyPages/1.0"}
    token = os.environ.get("BOND_FEED_TOKEN", "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        with build_opener(NoRedirect()).open(Request(url, headers=headers), timeout=30) as response:
            content = response.read(MAX_FEED_BYTES + 1)
    except HTTPError as exc:
        raise ValueError(f"行情源返回 HTTP {exc.code}。请检查访问权限及地址。") from None
    except (URLError, TimeoutError, OSError):
        raise ValueError("无法读取行情源，请检查网络及源服务状态。") from None
    if len(content) > MAX_FEED_BYTES:
        raise ValueError("行情源文件超过 25 MB。请缩短历史范围。")
    return json.loads(content)


def normalize_feed(feed):
    if not isinstance(feed, dict) or feed.get("schema_version") != 1:
        raise ValueError("行情文件需要 schema_version: 1。")
    source = feed.get("source")
    if not isinstance(source, str) or not 1 <= len(source.strip()) <= 100:
        raise ValueError("行情文件需要数据来源名称 source。")
    snapshots = feed.get("snapshots")
    if not isinstance(snapshots, list) or not 1 <= len(snapshots) <= 366:
        raise ValueError("行情文件需要 1 至 366 个交易日快照。")
    daily = {}
    for snapshot in snapshots:
        if not isinstance(snapshot, dict) or not isinstance(snapshot.get("date"), str):
            raise ValueError("快照缺少交易日期。")
        day = snapshot["date"]
        if date.fromisoformat(day).isoformat() != day or day in daily:
            raise ValueError("交易日期必须为唯一的 YYYY-MM-DD。")
        if not isinstance(snapshot.get("source_time"), str):
            raise ValueError(f"{day} 快照缺少来源时间。")
        stamp = datetime.fromisoformat(snapshot["source_time"])
        if stamp.tzinfo is None or stamp.astimezone(CHINA_TZ).date().isoformat() != day:
            raise ValueError(f"{day} 快照的来源时间必须带时区，且与北京时间交易日期一致。")
        rows = snapshot.get("records")
        if not isinstance(rows, list) or not rows:
            raise ValueError(f"{day} 快照没有个券数据。")
        records = []
        codes = set()
        for row in rows:
            if not isinstance(row, dict):
                raise ValueError(f"{day} 债券记录格式错误。")
            code, name, category = (row.get(key) for key in ("code", "name", "category"))
            if not isinstance(code, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,32}", code) or code in codes:
                raise ValueError(f"{day} 存在无效或重复的债券代码。代码须为字符串。")
            if not isinstance(name, str) or not 1 <= len(name.strip()) <= 120 or category not in CATEGORIES:
                raise ValueError(f"{day} 的 {code} 缺少简称或券种。")
            codes.add(code)
            record = {"code": code, "name": name.strip(), "category": category}
            for field in NUMERIC_FIELDS:
                value = row.get(field)
                if value is not None and (type(value) not in (int, float) or not math.isfinite(value)):
                    raise ValueError(f"{day} 的 {code} 有非数字字段 {field}。")
                if value is not None and field in ("term_years", "net_price", "volume") and value < 0:
                    raise ValueError(f"{day} 的 {code} 有负值字段 {field}。")
                record[field] = value
            records.append(record)
        daily[day] = {"date": day, "source_time": stamp.astimezone(CHINA_TZ).isoformat(), "records": records}
    return source.strip(), [daily[day] for day in sorted(daily, reverse=True)]


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")) + "\n", encoding="utf-8")


def build_site(output, feed, mode):
    source, daily = normalize_feed(feed)
    if output.exists() and any(output.iterdir()):
        raise ValueError("请指定新的空输出目录。")
    output.mkdir(parents=True, exist_ok=True)
    for filename in ("index.html", "style.css", "dashboard.js"):
        shutil.copyfile(ROOT / "web" / filename, output / filename)
    (output / ".nojekyll").touch()
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    metadata = {"schema_version": 1, "mode": mode, "source": source}
    manifest = {**metadata, "dates": [day["date"] for day in daily]}
    if re.fullmatch(r"[A-Za-z0-9-]+/[A-Za-z0-9_.-]+", repo):
        manifest["repository"] = repo
    folder = output / "data"
    write_json(folder / "index.json", manifest)
    write_json(folder / "latest.json", {**metadata, **daily[0]})
    series = defaultdict(list)
    for day in daily:
        write_json(folder / f"{day['date']}.json", {**metadata, **day})
    for day in reversed(daily):
        for row in day["records"]:
            series[row["code"]].append({"date": day["date"], **{key: row[key] for key in TREND_FIELDS}})
    for code, points in series.items():
        write_json(folder / "series" / f"{code}.json", points)
    return daily[0]["date"], len(daily[0]["records"])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("demo", "live"), default="demo")
    parser.add_argument("--output", type=Path, default=ROOT / "_site")
    args = parser.parse_args()
    try:
        if args.mode == "demo":
            feed = json.loads((ROOT / "web" / "demo-feed.json").read_text(encoding="utf-8"))
        else:
            feed = fetch_feed()
        latest, count = build_site(args.output, feed, args.mode)
    except (ValueError, OSError, OverflowError) as exc:
        print(f"构建失败。{exc}", file=sys.stderr)
        return 1
    print(f"Built {args.mode} site. Latest snapshot: {latest}, {count} bonds.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
