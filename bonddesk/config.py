from datetime import datetime, timedelta, timezone
from pathlib import Path
import os

ROOT = Path(__file__).resolve().parent.parent
CHINA_TZ = timezone(timedelta(hours=8))
SOURCE_URL = "https://www.chinamoney.com.cn/chinese/mkdatabond/"
API_ROOT = "https://www.chinamoney.com.cn/ags/ms/"
CATEGORIES = ("国债", "国开债", "农发债", "进出口行债")


def now_cn() -> datetime:
    return datetime.now(CHINA_TZ)


def data_directory() -> Path:
    return Path(os.environ.get("BOND_DATA_DIR", str(ROOT / "data"))).resolve()
