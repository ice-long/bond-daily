import argparse
import json
from pathlib import Path
import sys

from .config import data_directory
from .service import collect
from .storage import Store


def main():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="国债及政金债本地行情采集")
    parser.add_argument("command", choices=("collect", "status"))
    parser.add_argument("--data-dir", type=Path, default=data_directory())
    args = parser.parse_args()
    store = Store(args.data_dir)
    if args.command == "collect":
        result = collect(store, progress=lambda message: print(message, file=sys.stderr, flush=True))
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
        return 0 if result.ok else 1
    dates = store.dates()
    print(json.dumps({"dates": dates, "latest_run": store.daily_run(dates[0]) if dates else None,
                      "database": str(store.path)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
