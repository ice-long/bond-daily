"use strict";

function parseCsv(text) {
  const rows = [];
  let row = [];
  let field = "";
  let quoted = false;
  let closed = false;
  for (let index = text.charCodeAt(0) === 0xfeff ? 1 : 0; index < text.length; index++) {
    const character = text[index];
    if (quoted) {
      if (character !== '"') field += character;
      else if (text[index + 1] === '"') {
        field += '"';
        index++;
      } else {
        quoted = false;
        closed = true;
      }
    } else if (character === "," || character === "\r" || character === "\n") {
      row.push(field);
      field = "";
      closed = false;
      if (character !== ",") {
        if (row.some((cell) => cell.trim())) rows.push(row);
        row = [];
        if (character === "\r" && text[index + 1] === "\n") index++;
      }
    } else if (character === '"' && !field && !closed) {
      quoted = true;
    } else if (closed || character === '"') {
      throw new Error("CSV 字段格式错误，请选择本机看板原始导出的文件。");
    } else {
      field += character;
    }
  }
  if (quoted) throw new Error("CSV 内容不完整，请重新导出。");
  row.push(field);
  if (row.some((cell) => cell.trim())) rows.push(row);
  return rows;
}

function parseBondSnapshot(text, categories, columns) {
  const [headers, ...rows] = parseCsv(text);
  if (!headers || !rows.length) throw new Error("CSV 中没有可查看的行情。");
  const aliases = {
    "待偿期（年）": "待偿期（年，折算）",
    "涨跌（BP）": "涨跌（BP，来源）",
    "交易量（亿）": "交易量（亿，来源口径）",
    "来源时间": "来源更新时间（北京时间）",
  };
  const find = (label) => headers.findIndex((value) => value.trim() === label || value.trim() === aliases[label]);
  const required = ["行情日期", ...columns.map(([, label]) => label)];
  if (required.some((label) => find(label) < 0)) {
    throw new Error("缺少行情日期或个券字段，请使用本机看板导出的 CSV。");
  }
  const value = (row, label) => row[find(label)]?.trim() ?? "";
  const day = value(rows[0], "行情日期");
  const parsedDay = new Date(`${day}T00:00:00Z`);
  if (!/^\d{4}-\d{2}-\d{2}$/.test(day) || !Number.isFinite(+parsedDay) || parsedDay.toISOString().slice(0, 10) !== day) {
    throw new Error("行情日期应为有效的 YYYY-MM-DD。");
  }
  const sourceTime = value(rows[0], "来源时间");
  const source = value(rows[0], "来源") || "本机 CSV";
  const dataKind = value(rows[0], "数据性质") || "成交行情快照";
  const numericColumns = columns.slice(3).map(([key, label]) => [key, label, find(label)]);
  const codes = new Set();
  const records = rows.map((row, index) => {
    if (row.length !== headers.length || value(row, "行情日期") !== day || value(row, "来源时间") !== sourceTime) {
      throw new Error(`第 ${index + 2} 条记录的列数、行情日期或来源时间不一致。请选择同一批次的导出。`);
    }
    const record = {
      code: value(row, "债券代码"),
      name: value(row, "债券简称"),
      category: value(row, "券种"),
      quote_state: value(row, "行情状态"),
    };
    if (!record.code || codes.has(record.code) || !record.name || !categories.includes(record.category)) {
      throw new Error(`第 ${index + 2} 条记录的代码、简称或券种无效，或债券代码重复。`);
    }
    codes.add(record.code);
    for (const [key, label, column] of numericColumns) {
      const cell = row[column].trim();
      const amount = cell ? Number(cell) : null;
      if (amount !== null && !Number.isFinite(amount)) throw new Error(`${record.code} 的${label}格式错误。`);
      record[key] = amount;
    }
    return record;
  });
  return {date: day, source_time: sourceTime, source, dataKind, mode: "local", records};
}

async function openLocalSnapshots(files, categories, columns) {
  const snapshots = new Map();
  for (const file of files) {
    let snapshot;
    try {
      snapshot = parseBondSnapshot(await file.text(), categories, columns);
    } catch (error) {
      throw new Error(`${file.name}。${error.message}`);
    }
    if (snapshots.has(snapshot.date)) throw new Error(`${snapshot.date} 有多个文件，请为该日期保留一个。`);
    snapshots.set(snapshot.date, snapshot);
  }
  return snapshots;
}

function localBondSeries(snapshots, code) {
  const series = [];
  for (const [date, snapshot] of snapshots) {
    const row = snapshot.records.find((record) => record.code === code);
    if (row && (!row.quote_state || row.quote_state === "当日行情")) {
      series.push({date, latest_yield: row.latest_yield, net_price: row.net_price, volume: row.volume});
    }
  }
  return series.sort((a, b) => a.date.localeCompare(b.date));
}
