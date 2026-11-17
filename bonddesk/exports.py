from io import BytesIO

import pandas as pd
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from .config import SOURCE_URL

LABELS = {
    "source_date": "行情日期", "code": "债券代码", "name": "债券简称",
    "category": "券种", "issuer": "发行人", "term_raw": "待偿期（来源）",
    "term_years": "待偿期（年，折算）", "net_price": "成交净价（元）",
    "latest_yield": "最新收益率（%）", "weighted_yield": "加权收益率（%）",
    "change_bp": "涨跌（BP，来源）", "volume": "交易量（亿，来源口径）",
    "quote_state": "行情状态", "record_time": "个券更新时间（北京时间）",
    "source_time": "来源更新时间（北京时间）", "fetched_at": "采集时间（北京时间）",
}


def export_frame(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.reindex(columns=list(LABELS)).copy()
    result = result.rename(columns=LABELS)
    result["数据性质"] = "成交行情快照；非正式收盘价"
    result["来源"] = SOURCE_URL
    return result


def csv_bytes(frame: pd.DataFrame) -> bytes:
    data = export_frame(frame)
    # Prevent spreadsheet applications from interpreting source text as formulas.
    for col in data.columns:
        data[col] = data[col].map(lambda value: "'" + value if isinstance(value, str)
                                  and value.lstrip().startswith(("=", "+", "-", "@")) else value)
    return data.to_csv(index=False, lineterminator="\r\n").encode("utf-8-sig")


def xlsx_bytes(frame: pd.DataFrame) -> bytes:
    data = export_frame(frame)
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "成交行情快照"
    sheet.append(data.columns.tolist())
    for record in data.itertuples(index=False, name=None):
        sheet.append([None if pd.isna(value) else value for value in record])
    for row in sheet:
        for cell in row:
            if isinstance(cell.value, str):
                cell.data_type = "s"
            cell.font = Font(name="微软雅黑", size=10, color="172B43")
            cell.alignment = Alignment(vertical="center")
            if cell.row > 1 and isinstance(cell.value, (int, float)):
                cell.number_format = "0.0000"
        sheet.row_dimensions[row[0].row].height = 23
    for cell in sheet[1]:
        cell.fill = PatternFill("solid", fgColor="172B43")
        cell.font = Font(name="微软雅黑", size=10, bold=True, color="FFFFFF")
    for col in range(1, len(data.columns) + 1):
        sheet.column_dimensions[get_column_letter(col)].width = 22
    for col in (3, 5, 14, 15, 16, 17):
        sheet.column_dimensions[get_column_letter(col)].width = 30
    sheet.column_dimensions[get_column_letter(len(data.columns))].width = 62
    sheet.freeze_panes = "D2"
    sheet.auto_filter.ref = sheet.dimensions
    sheet.sheet_view.showGridLines = False
    output = BytesIO()
    workbook.save(output)
    return output.getvalue()
