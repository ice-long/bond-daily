from contextlib import contextmanager
from dataclasses import dataclass, asdict
import json
import os
from pathlib import Path
import time
from uuid import uuid4

from .config import now_cn
from .model import normalize
from .source import ChinaMoneyClient, SourceError
from .storage import Store


@contextmanager
def collection_lock(directory: Path):
    """OS-released lock: a crash cannot permanently lock out future collections."""
    path = directory / "collect.lock"
    with path.open("a+b") as handle:
        handle.seek(0, 2)
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise SourceError("另一个采集任务正在运行，请等待完成。") from exc
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


@dataclass
class CollectionResult:
    ok: bool
    message: str
    run_id: str | None = None
    source_date: str | None = None
    count: int = 0

    def to_dict(self):
        return asdict(self)


def write_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def collect(store: Store, client=None, clock=now_cn, progress=None) -> CollectionResult:
    callback = progress or (lambda _: None)
    own_client = client is None
    client = client or ChinaMoneyClient(callback)
    run_id = None
    try:
        with collection_lock(store.directory):
            start = clock()
            store.recover_interrupted(start.isoformat())
            run_id = uuid4().hex
            store.start_run(run_id, start.isoformat())
            try:
                payload = client.fetch_market()
                fetched = clock()
                raw_path = store.directory / "raw" / fetched.date().isoformat() / f"{run_id}.json"
                write_json(raw_path, {"fetched_at": fetched.isoformat(), "payload": payload})
                store.mark_raw(run_id, fetched.isoformat(), str(raw_path.relative_to(store.directory)))
                catalog, catalog_date = store.catalog()
                warnings = []
                if not catalog or catalog_date != fetched.date().isoformat():
                    try:
                        items, pages = client.fetch_catalog()
                        write_json(store.directory / "raw" / "catalog" / f"{run_id}.json",
                                   {"fetched_at": clock().isoformat(), "pages": pages})
                        store.save_catalog(items, fetched.date().isoformat())
                        catalog = {str(row["bondCode"]): row for row in items}
                    except (SourceError, ValueError, KeyError) as exc:
                        if getattr(client, "catalog_pages", None):
                            write_json(store.directory / "raw" / "catalog" / f"{run_id}_failed.json",
                                       {"fetched_at": clock().isoformat(), "error": str(exc),
                                        "pages": client.catalog_pages})
                        if not catalog:
                            raise SourceError(f"首次债券分类清单获取失败：{exc}") from exc
                        warnings.append(f"代码清单更新失败，使用 {catalog_date} 的缓存；新券可能尚未识别。{exc}")
                normalized = normalize(payload, catalog, fetched)
                if normalized["source_date"] != fetched.date().isoformat():
                    warnings.append(f"来源仍为 {normalized['source_date']} 的行情，未记为今天的新成交。")
                if normalized["unknown"]:
                    warnings.append(f"有 {len(normalized['unknown'])} 条疑似目标债券未能确认分类，已单独列出。")
                if any(row["quote_state"] != "当日行情" for row in normalized["records"]):
                    warnings.append("部分个券为旧行情或未披露更新时间，请结合行情状态查看。")
                store.finish_run(run_id, normalized, warnings, clock().isoformat())
                callback("采集、校验和保存已完成。")
                return CollectionResult(True, "采集完成", run_id, normalized["source_date"],
                                        len(normalized["records"]))
            except Exception as exc:
                store.fail_run(run_id, str(exc)[:1500], clock().isoformat())
                return CollectionResult(False, str(exc), run_id)
    except SourceError as exc:
        return CollectionResult(False, str(exc), run_id)
    finally:
        if own_client:
            client.close()
