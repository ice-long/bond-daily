import json
import math

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from bonddesk.config import CATEGORIES, SOURCE_URL, data_directory, now_cn
from bonddesk.exports import csv_bytes, xlsx_bytes
from bonddesk.service import collect
from bonddesk.storage import Store

st.set_page_config(page_title="利率债 · 每日观察", page_icon="◈", layout="wide")
st.html("""
<style>
.block-container {padding-top:2.2rem; padding-bottom:3rem; max-width:1600px;}
[data-testid="stSidebar"] {border-right:1px solid #e2e8f0;}
[data-testid="stMetric"] {background:white; border:1px solid #e3e9ef; padding:1.1rem 1.3rem; border-radius:14px;}
[data-testid="stMetricLabel"] {color:#63758a;}
.hero {padding:1.8rem 2rem; background:#142c44; color:#f7fafc; border-radius:18px; margin-bottom:1.4rem;}
.hero .eyebrow {font-size:11px; letter-spacing:3px; color:#81ccd0; margin-bottom:10px;}
.hero h1 {font-size:30px; margin:0; color:#fff; font-weight:650; letter-spacing:1px;}
.hero p {color:#c2d0df; margin:10px 0 0; font-size:14px;}
.quiet {color:#63758a; font-size:13px; line-height:1.7;}
[data-testid="stDataFrame"] {border-radius:10px;}
div[data-testid="stTabs"] button {font-size:15px;}
</style>
<div class="hero"><div class="eyebrow">INTERBANK · DAILY OBSERVATIONS</div>
<h1>利率债 · 每日观察</h1><p>国债与政策性金融债的成交行情、本地历史和每日复盘。</p></div>
""")

store = Store(data_directory())
dates = store.dates()

with st.sidebar:
    st.markdown("### 观察范围")
    st.caption("中国银行间市场 · 公开行情")
    chosen_date = st.selectbox("行情日期", dates, index=0) if dates else None
    categories = st.multiselect("券种", CATEGORIES, default=list(CATEGORIES))
    search = st.text_input("代码 / 简称", placeholder="例如 260205、国开、贴现")
    tenor = st.selectbox("剩余期限", ["全部", "1 年以内", "1–3 年", "3–5 年", "5–10 年", "10 年及以上"])
    only_current = st.checkbox("仅显示当日更新的个券", value=False)
    st.divider()
    st.markdown("**数据口径**")
    st.caption("这里保存的是来源发布的成交行情快照。净价与收益率按来源展示，未标为正式收盘价。")
    st.caption("交易量单位沿用来源的“亿”，未另行推导成交金额。空值表示未取得该字段。")
    st.link_button("查看来源网页 ↗", SOURCE_URL, use_container_width=True)
    with st.expander("本地保存与备份"):
        st.caption("关闭页面后历史仍会保留。退出应用后，可复制整个 data 文件夹备份。")
        st.code(str(store.directory), language=None)

action, context = st.columns([1.25, 4])
with action:
    clicked = st.button("↻  采集最新行情", type="primary", use_container_width=True)
with context:
    st.caption("手动采集 · 浏览和筛选不会请求新行情。首次建立官方代码清单可能需要几分钟。")
    st.caption("建议每日晚间固定时段采集；漏采日期会保留缺口。所有时间均为北京时间。")
if clicked:
    with st.status("正在采集…", expanded=True) as status:
        result = collect(store, progress=lambda message: status.update(label=message))
        status.update(label="采集完成" if result.ok else "采集未成功",
                      state="complete" if result.ok else "error", expanded=not result.ok)
    st.session_state["collection_result"] = result.to_dict()
    st.rerun()

if "collection_result" in st.session_state:
    result = st.session_state["collection_result"]
    if result["ok"]:
        st.success(f"已保存 {result['source_date']} 的 {result['count']} 只目标债券行情。")
    else:
        st.error(f"本次采集未成功：{result['message']}。此前成功保存的数据不受影响。")

if not dates:
    st.info("还没有本地行情。点击“采集最新行情”，开始积累你的第一份每日记录。")
    st.markdown("#### 从一份真实快照开始")
    st.write("采集后可以按券种和期限筛选、查看个券历史，并将当前结果导出为 Excel 或 CSV。")
    recent = store.recent_runs()
    if not recent.empty:
        st.dataframe(recent[["started_at", "status", "error"]].rename(columns={
            "started_at": "开始时间", "status": "状态", "error": "原因"}), hide_index=True, width="stretch")
    st.stop()

run = store.daily_run(chosen_date)
frame = store.quotes(run["id"])
if chosen_date != now_cn().date().isoformat():
    st.info(f"当前查看 {chosen_date} 的本地记录。该日期不会被改写成今天。")
for warning in json.loads(run["warnings_json"] or "[]"):
    st.warning(warning)
st.caption(f"行情日期 {chosen_date}　｜　来源更新 {run['source_time'].replace('T',' ').replace('+08:00','')}　｜　采集于 {run['fetched_at'].replace('T',' ').replace('+08:00','').split('.')[0]}")

cards = st.columns(4)
for column, category in zip(cards, CATEGORIES):
    column.metric(category, f"{int((frame.category == category).sum())} 只")
st.caption("以上为本次来源中已识别的目标债券数量，不代表所有存续债券或全市场成交覆盖。")

filtered = frame[frame.category.isin(categories)].copy()
if search.strip():
    filtered = filtered[filtered.code.str.contains(search.strip(), regex=False, case=False) |
                        filtered.name.str.contains(search.strip(), regex=False, case=False)]
limits = {"1 年以内": (0, 1), "1–3 年": (1, 3), "3–5 年": (3, 5), "5–10 年": (5, 10),
          "10 年及以上": (10, math.inf)}
if tenor in limits:
    lower, upper = limits[tenor]
    filtered = filtered[(filtered.term_years >= lower) & (filtered.term_years < upper)]
if only_current:
    filtered = filtered[filtered.quote_state == "当日行情"]
filtered = filtered.sort_values(["volume", "code"], ascending=[False, True], na_position="last")

overview, history_tab, quality = st.tabs(["每日行情", "个券轨迹", "数据质量与采集记录"])

with overview:
    left, right = st.columns([3, 2])
    with left:
        st.markdown(f"#### 成交行情　·　{len(filtered)} 只")
    with right:
        xls_col, csv_col = st.columns(2)
        xls_col.download_button("↓ Excel", data=xlsx_bytes(filtered),
                               file_name=f"利率债行情_{chosen_date}.xlsx",
                               mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                               use_container_width=True, disabled=filtered.empty)
        csv_col.download_button("↓ CSV", data=csv_bytes(filtered),
                               file_name=f"利率债行情_{chosen_date}.csv", mime="text/csv",
                               use_container_width=True, disabled=filtered.empty)
    display_columns = ["code", "name", "category", "term_raw", "net_price", "latest_yield",
                       "weighted_yield", "change_bp", "volume", "quote_state", "record_time"]
    st.dataframe(filtered[display_columns], hide_index=True, width="stretch", height=500,
                 column_config={
                     "code": st.column_config.TextColumn("债券代码", width="small"),
                     "name": st.column_config.TextColumn("债券简称", width="medium"),
                     "category": "券种", "term_raw": "待偿期",
                     "net_price": st.column_config.NumberColumn("成交净价 / 元", format="%.4f"),
                     "latest_yield": st.column_config.NumberColumn("最新收益率 / %", format="%.4f"),
                     "weighted_yield": st.column_config.NumberColumn("加权收益率 / %", format="%.4f"),
                     "change_bp": st.column_config.NumberColumn("涨跌 / BP", format="%+.2f"),
                     "volume": st.column_config.NumberColumn("交易量 / 亿", format="%.4f"),
                     "quote_state": "行情状态", "record_time": "个券更新时间",
                 })
    if filtered.empty:
        st.info("当前筛选条件下未取得行情。这不代表这些债券的成交量为零。")
    st.caption("表格与导出使用相同筛选结果。Excel 会保留债券代码的前导零；CSV 导入 Excel 时，请将代码列设为文本。")
    st.caption("期限筛选使用来源待偿期；月/日单位仅按 12 月、365 日折算成年，用于筛选。")

with history_tab:
    st.markdown("#### 一只债券，逐日观察")
    catalog, _ = store.catalog()
    choices = [(code, row.get("bondName", code)) for code, row in catalog.items()]
    choice_names = dict(choices)
    code_choices = list(filtered.code) + [code for code, _ in choices if code not in set(filtered.code)]
    selected = st.selectbox("选择债券（可输入代码或简称搜索）", code_choices,
                            format_func=lambda code: f"{code} · {choice_names.get(code, code)}")
    hist = store.history(selected)
    if hist.net_price.notna().any() or hist.latest_yield.notna().any():
        st.caption("每个来源日期采用该日最新成功批次。旧行情及更新时间未披露的记录不进入趋势线；缺口不填零。")
        metric = st.radio("观察指标", ["最新收益率", "成交净价", "交易量"], horizontal=True)
        metric_key, unit = {"最新收益率": ("latest_yield", "%"), "成交净价": ("net_price", "元"), "交易量": ("volume", "亿")}[metric]
        plot = hist.set_index(pd.to_datetime(hist.source_date)).copy()
        plot.loc[plot.quote_state != "当日行情", metric_key] = float("nan")
        plot = plot.reindex(pd.date_range(plot.index.min(), plot.index.max(), freq="D"))
        plot["hover_time"] = plot.fetched_at.fillna("未采集")
        figure = go.Figure(go.Scatter(x=plot.index, y=plot[metric_key], mode="lines+markers",
                                      line={"color": "#087e8b", "width": 2.5},
                                      marker={"size": 8}, connectgaps=False,
                                      customdata=plot["hover_time"],
                                      hovertemplate=f"%{{x|%Y-%m-%d}}<br>{metric}：%{{y:.4f}} {unit}<br>采集：%{{customdata}}<extra></extra>"))
        figure.update_layout(height=350, margin={"l": 15, "r": 15, "t": 25, "b": 25},
                             paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="white",
                             font={"color": "#172b43"}, yaxis_title=f"{metric}（{unit}）",
                             xaxis_title=None)
        st.plotly_chart(figure, width="stretch")
        st.caption(f"本地已积累 {len(hist)} 个来源日期。横轴按自然日显示，休市和未采集日期可能形成缺口。")
        st.dataframe(hist[["source_date", "net_price", "latest_yield", "volume", "quote_state", "fetched_at"]].rename(columns={
            "source_date": "行情日期", "net_price": "成交净价（元）", "latest_yield": "最新收益率（%）",
            "volume": "交易量（亿）", "quote_state": "行情状态", "fetched_at": "采集时间"}),
                     hide_index=True, width="stretch")
    else:
        st.info("本地尚未取得这只债券的行情。未取得行情不等于没有成交。")

with quality:
    st.markdown("#### 本批次数据质量")
    st.write(f"来源返回 {run['raw_count']} 条行情，识别目标债券 {run['target_count']} 只，"
             f"其他债券 {run['excluded_count']} 只，待确认分类 {run['unknown_count']} 只。")
    missing_labels = {"net_price": "成交净价", "latest_yield": "最新收益率",
                      "weighted_yield": "加权收益率", "change_bp": "涨跌 BP", "volume": "交易量"}
    missing = json.loads(run["missing_json"] or "{}")
    st.dataframe(pd.DataFrame([{"字段": missing_labels[key], "缺失条数": value}
                              for key, value in missing.items()]), hide_index=True, width="stretch")
    unknown = store.unknown(run["id"])
    if not unknown.empty:
        st.markdown("**待确认分类：未计入目标行情**")
        st.dataframe(unknown.rename(columns={"code": "代码", "name": "简称", "reason": "原因"}),
                     hide_index=True, width="stretch")
    st.markdown("#### 最近采集记录")
    runs = store.recent_runs().copy()
    runs["status"] = runs.status.map({"success": "成功", "failed": "失败", "running": "采集中"})
    st.dataframe(runs[["started_at", "source_date", "status", "target_count", "error"]].rename(columns={
        "started_at": "开始时间", "source_date": "来源日期", "status": "状态",
        "target_count": "目标债券数", "error": "失败原因"}), hide_index=True, width="stretch")
    st.caption("来源同代码重复或互补记录按一致字段合并，不叠加交易量；有冲突的目标行情不会发布。失败批次不会替换成功结果，原始响应和批次记录保留在本地。")

st.divider()
st.caption("来源：中国货币网 · 从启用之日起积累 · 历史页仅查询本地数据 · 个人本机使用")
