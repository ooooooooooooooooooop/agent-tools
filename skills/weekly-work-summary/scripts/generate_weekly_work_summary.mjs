import crypto from "node:crypto";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { spawnSync } from "node:child_process";
import { FileBlob, SpreadsheetFile, Workbook } from "@oai/artifact-tool";

const SOURCE_DIR = "C:\\Desktop\\日报";
const OUTPUT_DIR = "C:\\Desktop\\共享";
const SHEET_NAME = "工作总结";

const HOLIDAY_RANGES_2026 = [
  ["2026-01-01", "2026-01-03"],
  ["2026-02-15", "2026-02-23"],
  ["2026-04-04", "2026-04-06"],
  ["2026-05-01", "2026-05-05"],
  ["2026-06-19", "2026-06-21"],
  ["2026-09-25", "2026-09-27"],
  ["2026-10-01", "2026-10-07"],
];
const ADJUSTED_WORKDAYS_2026 = [
  "2026-01-04",
  "2026-02-14",
  "2026-02-28",
  "2026-05-09",
  "2026-09-20",
  "2026-10-10",
];

function parseIsoDate(value) {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value)) throw new Error(`日期格式必须为 YYYY-MM-DD：${value}`);
  const [year, month, day] = value.split("-").map(Number);
  const date = new Date(Date.UTC(year, month - 1, day));
  if (date.getUTCFullYear() !== year || date.getUTCMonth() !== month - 1 || date.getUTCDate() !== day) {
    throw new Error(`无效日期：${value}`);
  }
  return date;
}

function isoDate(date) {
  return date.toISOString().slice(0, 10);
}

function addDays(date, amount) {
  const result = new Date(date.getTime());
  result.setUTCDate(result.getUTCDate() + amount);
  return result;
}

function datesBetween(start, end) {
  const result = [];
  const endDate = parseIsoDate(end);
  for (let cursor = parseIsoDate(start); cursor <= endDate; cursor = addDays(cursor, 1)) {
    result.push(isoDate(cursor));
  }
  return result;
}

const CALENDAR_BY_YEAR = new Map([
  [2026, {
    holidays: new Set(HOLIDAY_RANGES_2026.flatMap(([start, end]) => datesBetween(start, end))),
    adjustedWorkdays: new Set(ADJUSTED_WORKDAYS_2026),
  }],
]);

function getArgument(name) {
  const index = process.argv.indexOf(name);
  if (index < 0) return null;
  const value = process.argv[index + 1];
  if (!value || value.startsWith("--")) throw new Error(`${name} 缺少参数值`);
  return value;
}

function shanghaiToday() {
  const formatter = new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Shanghai",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  });
  const parts = Object.fromEntries(formatter.formatToParts(new Date()).map((part) => [part.type, part.value]));
  return `${parts.year}-${parts.month}-${parts.day}`;
}

function mondayOf(dateString) {
  const date = parseIsoDate(dateString);
  const offset = (date.getUTCDay() + 6) % 7;
  return addDays(date, -offset);
}

function getWorkdays(weekStart) {
  const weekDates = Array.from({ length: 7 }, (_, index) => isoDate(addDays(parseIsoDate(weekStart), index)));
  const workdays = [];
  for (const date of weekDates) {
    const calendar = CALENDAR_BY_YEAR.get(Number(date.slice(0, 4)));
    if (!calendar) throw new Error(`未配置中国实际工作日历：${date.slice(0, 4)} 年`);
    const dayOfWeek = parseIsoDate(date).getUTCDay();
    const isWorkday = calendar.adjustedWorkdays.has(date) || (dayOfWeek !== 0 && dayOfWeek !== 6 && !calendar.holidays.has(date));
    if (isWorkday) workdays.push(date);
  }
  if (workdays.length === 0) throw new Error(`本周没有可汇总的中国实际工作日：${weekStart}`);
  return workdays;
}

function monthDayPattern(dateString) {
  const date = parseIsoDate(dateString);
  return `0?${date.getUTCMonth() + 1}\\.0?${date.getUTCDate()}`;
}

function compactDate(dateString) {
  return dateString.replaceAll("-", "");
}

async function listSourceFiles() {
  try {
    const entries = await fs.readdir(SOURCE_DIR, { withFileTypes: true });
    return entries.filter((entry) => entry.isFile() && entry.name.toLowerCase().endsWith(".xlsx")).map((entry) => entry.name);
  } catch (error) {
    throw new Error(`日报目录不可用：${SOURCE_DIR}；${error?.message ?? error}`);
  }
}

function selectOne(files, pattern, label) {
  const matches = files.filter((file) => pattern.test(file));
  if (matches.length !== 1) {
    throw new Error(`${label}源文件数量必须为 1，实际为 ${matches.length}：${matches.join("、") || "无"}`);
  }
  return path.join(SOURCE_DIR, matches[0]);
}

function resolveSources(workdays) {
  const start = workdays[0];
  const end = workdays[workdays.length - 1];
  const startMonthDay = monthDayPattern(start);
  const endMonthDay = monthDayPattern(end);
  const compactRange = `${compactDate(start)}-${compactDate(end)}`;
  return { start, end, startMonthDay, endMonthDay, compactRange };
}

async function importWorkbook(filePath) {
  try {
    return await SpreadsheetFile.importXlsx(await FileBlob.load(filePath));
  } catch (error) {
    throw new Error(`无法读取源 Excel：${filePath}；${error?.message ?? error}`);
  }
}

function rangeForRows(firstRow, rowCount) {
  return `A${firstRow}:C${firstRow + rowCount - 1}`;
}

function normalizeSentence(value, label) {
  if (typeof value !== "string") throw new Error(`${label}不是文本：${String(value)}`);
  const text = value.replace(/\r?\n/g, " ").replace(/\s+/g, " ").trim();
  if (!text) throw new Error(`${label}为空`);
  if (!/[\u3400-\u9fff]/u.test(text)) throw new Error(`${label}不含中文：${text}`);
  const terminals = text.match(/[。！？]/gu) ?? [];
  if (terminals.length > 1 || (terminals.length === 1 && !/[。！？]$/u.test(text))) {
    throw new Error(`${label}不是单句中文总结：${text}`);
  }
  return terminals.length === 0 ? `${text}。` : text;
}

function excelSerialToIso(value) {
  if (typeof value !== "number") return null;
  return isoDate(new Date(Date.UTC(1899, 11, 30) + value * 86400000));
}

function assertDateIfExcelSerial(value, expected, label) {
  const actual = excelSerialToIso(value);
  if (actual && actual !== expected) throw new Error(`${label}日期不匹配：实际=${actual}，预期=${expected}`);
}

async function readPersonRows(filePath, sheetName, firstRow, workdays, person, validateDateSerial = false) {
  const workbook = await importWorkbook(filePath);
  let rows;
  try {
    rows = workbook.worksheets.getItem(sheetName).getRange(rangeForRows(firstRow, workdays.length)).values;
  } catch (error) {
    throw new Error(`${person}源文件缺少固定工作表或范围：${filePath}；${error?.message ?? error}`);
  }
  if (rows.length !== workdays.length) throw new Error(`${person}源文件行数不匹配：实际=${rows.length}，预期=${workdays.length}；${filePath}`);
  return rows.map((row, index) => {
    if (validateDateSerial) assertDateIfExcelSerial(row[0], workdays[index], `${person}第${index + 1}行`);
    return normalizeSentence(row[2], `${person}第${index + 1}个工作日`);
  });
}

function checkTargetLock(targetPath) {
  const result = spawnSync(
    "powershell.exe",
    [
      "-NoProfile",
      "-NonInteractive",
      "-Command",
      "$p=$env:WEEKLY_TARGET; try { $s=[System.IO.File]::Open($p,[System.IO.FileMode]::Open,[System.IO.FileAccess]::ReadWrite,[System.IO.FileShare]::None); $s.Dispose(); exit 0 } catch { [Console]::Error.WriteLine($_.Exception.Message); exit 7 }",
    ],
    { env: { ...process.env, WEEKLY_TARGET: targetPath }, encoding: "utf8" },
  );
  if (result.error) throw new Error(`目标文件占用检查失败：${result.error.message}`);
  if (result.status !== 0) {
    const reason = (result.stderr || result.stdout || "未知原因").trim();
    throw new Error(`目标文件被占用或不可写：${targetPath}；${reason}`);
  }
}

async function prepareOutput(outputPath) {
  try {
    await fs.mkdir(OUTPUT_DIR, { recursive: true });
  } catch (error) {
    throw new Error(`共享目录不可用：${OUTPUT_DIR}；${error?.message ?? error}`);
  }
  try {
    const stat = await fs.stat(outputPath);
    if (stat.isDirectory()) throw new Error(`目标路径是目录，不是文件：${outputPath}`);
    checkTargetLock(outputPath);
  } catch (error) {
    if (error?.code === "ENOENT") return;
    throw error;
  }
}

function buildWorkbook(workdays, summaries) {
  const workbook = Workbook.create();
  const sheet = workbook.worksheets.add(SHEET_NAME);
  const endRow = workdays.length + 1;
  const values = [
    ["工作日期", "陈鹏", "胥帅杰", "徐文彬"],
    ...workdays.map((date, index) => [date, summaries.chen[index], summaries.xushuai[index], summaries.xuwenbin[index]]),
  ];
  sheet.getRange(`A1:D${endRow}`).values = values;
  sheet.showGridLines = false;
  sheet.getRange("A1:D1").format = {
    fill: "#D9EAF7",
    font: { bold: true, color: "#1F2937" },
    borders: { preset: "all", style: "thin", color: "#B7C9D6" },
  };
  sheet.getRange(`A2:D${endRow}`).format.borders = { preset: "all", style: "thin", color: "#D9D9D9" };
  sheet.getRange(`B2:C${endRow}`).format.wrapText = false;
  sheet.getRange(`D2:D${endRow}`).format.wrapText = true;
  sheet.getRange(`A1:A${endRow}`).format.columnWidth = 14;
  sheet.getRange(`B1:B${endRow}`).format.columnWidth = 34;
  sheet.getRange(`C1:C${endRow}`).format.columnWidth = 26;
  sheet.getRange(`D1:D${endRow}`).format.columnWidth = 90;
  sheet.getRange("A1:D1").format.rowHeight = 24;
  sheet.getRange(`A2:D${endRow}`).format.rowHeight = 60;
  sheet.getRange(`A2:A${endRow}`).format.numberFormat = "yyyy-mm-dd";
  sheet.freezePanes.freezeRows(1);
  return workbook;
}

function hasRealFormulaErrors(ndjson) {
  const text = ndjson?.trim() ?? "";
  return text && !/matched 0 entries/i.test(text) && !/no entries/i.test(text);
}

async function main() {
  const requestedWeekStart = getArgument("--week-start");
  const weekStart = requestedWeekStart ? parseIsoDate(requestedWeekStart) : mondayOf(shanghaiToday());
  if (weekStart.getUTCDay() !== 1) throw new Error(`周起始日期必须是周一：${isoDate(weekStart)}`);
  const workdays = getWorkdays(isoDate(weekStart));
  const period = resolveSources(workdays);
  const files = await listSourceFiles();
  const chenPath = selectOne(files, new RegExp(`^日报-陈鹏${period.startMonthDay}-${period.endMonthDay}\\.xlsx$`, "u"), "陈鹏");
  const xushuaiPath = selectOne(files, new RegExp(`^胥${period.startMonthDay}-${period.endMonthDay}\\.xlsx$`, "u"), "胥帅杰");
  const xuwenbinPath = selectOne(files, new RegExp(`^论文创新统计工作总结_${period.compactRange}\\.xlsx$`, "u"), "徐文彬");
  const summaries = {
    chen: await readPersonRows(chenPath, "Sheet1", 2, workdays, "陈鹏"),
    xushuai: await readPersonRows(xushuaiPath, "Sheet1", 1, workdays, "胥帅杰"),
    xuwenbin: await readPersonRows(xuwenbinPath, "Summary", 4, workdays, "徐文彬", true),
  };
  const outputPath = path.join(OUTPUT_DIR, `工作总结_${period.start.replaceAll("-", "")}-${period.end.replaceAll("-", "")}.xlsx`);
  await prepareOutput(outputPath);
  const workbook = buildWorkbook(workdays, summaries);
  try {
    const output = await SpreadsheetFile.exportXlsx(workbook);
    await output.save(outputPath);
  } catch (error) {
    throw new Error(`目标文件写入失败：${outputPath}；${error?.message ?? error}`);
  }

  const previewPath = path.join(process.cwd(), `工作总结_${period.start.replaceAll("-", "")}-${period.end.replaceAll("-", "")}.png`);
  const verifiedWorkbook = await importWorkbook(outputPath);
  const verifiedValues = verifiedWorkbook.worksheets.getItem(SHEET_NAME).getRange(`A1:D${workdays.length + 1}`).values;
  if (verifiedValues.length !== workdays.length + 1) throw new Error(`导出后工作日行数不匹配：${verifiedValues.length - 1}`);
  for (let index = 0; index < workdays.length; index += 1) {
    const expected = [workdays[index], summaries.chen[index], summaries.xushuai[index], summaries.xuwenbin[index]];
    const actual = verifiedValues[index + 1];
    if (JSON.stringify(actual) !== JSON.stringify(expected)) throw new Error(`导出后第${index + 1}个工作日内容不匹配`);
  }
  const errors = await verifiedWorkbook.inspect({
    kind: "match",
    searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A",
    options: { useRegex: true, maxResults: 300 },
    summary: "final formula error scan",
  });
  if (hasRealFormulaErrors(errors.ndjson)) throw new Error(`发现公式错误：${errors.ndjson}`);
  const inspected = await verifiedWorkbook.inspect({
    kind: "table",
    sheetId: SHEET_NAME,
    range: `A1:D${workdays.length + 1}`,
    include: "values,formulas",
    tableMaxRows: workdays.length + 1,
    tableMaxCols: 4,
    maxChars: 8000,
  });
  const preview = await verifiedWorkbook.render({ sheetName: SHEET_NAME, autoCrop: "all", scale: 1, format: "png" });
  await fs.writeFile(previewPath, new Uint8Array(await preview.arrayBuffer()));
  try {
    await fs.unlink(`${outputPath}.inspect.ndjson`);
  } catch (error) {
    if (error?.code !== "ENOENT") throw error;
  }
  const hash = crypto.createHash("sha256").update(await fs.readFile(outputPath)).digest("hex").toUpperCase();
  console.log(JSON.stringify({
    status: "PASS",
    shanghaiDate: shanghaiToday(),
    weekStart: period.start,
    weekEnd: period.end,
    workdays,
    sources: { chen: chenPath, xushuai: xushuaiPath, xuwenbin: xuwenbinPath },
    output: outputPath,
    preview: previewPath,
    formulaErrors: 0,
    sha256: hash,
    inspected: inspected.ndjson,
  }, null, 2));
}

main().catch((error) => {
  console.error(`FAILED: ${error?.message ?? error}`);
  process.exitCode = 1;
});
