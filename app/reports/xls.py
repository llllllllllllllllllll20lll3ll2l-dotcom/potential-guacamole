"""Рабочая таблица XLS (колонки согласованы) и текстовые отчёты."""
from datetime import datetime
from pathlib import Path

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from .. import config

HEADERS = ["Дата", "Задача", "Исполнитель", "Статус", "Результат", "Время"]
WIDTHS = [18, 46, 16, 12, 60, 12]


def now_str() -> str:
    return datetime.now().strftime("%d.%m.%Y %H:%M")


def human_duration(seconds: float) -> str:
    s = int(seconds)
    if s < 60:
        return f"{s} сек"
    return f"{s // 60} мин {s % 60} сек"


def ensure_xls() -> None:
    if config.XLS_PATH.exists():
        return
    wb = Workbook()
    ws = wb.active
    ws.title = "Отчёт"
    ws.append(HEADERS)
    fill = PatternFill("solid", fgColor="1F4E78")
    for i, w in enumerate(WIDTHS, start=1):
        ws.column_dimensions[get_column_letter(i)].width = w
        cell = ws.cell(row=1, column=i)
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = fill
        cell.alignment = Alignment(horizontal="center", vertical="center")
    ws.freeze_panes = "A2"
    wb.save(config.XLS_PATH)


def append_row(task: str, executor: str, status: str, result: str, seconds: float) -> None:
    ensure_xls()
    wb = load_workbook(config.XLS_PATH)
    ws = wb.active
    ws.append([now_str(), task[:500], executor, status, str(result)[:1000],
               human_duration(seconds)])
    for cell in ws[ws.max_row]:
        cell.alignment = Alignment(vertical="top", wrap_text=(cell.column == 5))
    wb.save(config.XLS_PATH)


def write_text_report(task: str, report: str, steps_log: list[str]) -> Path:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = config.REPORTS_DIR / f"otchet_{ts}.txt"
    body = [
        f"ОТЧЁТ О РАБОТЕ  ({datetime.now().strftime('%d.%m.%Y %H:%M:%S')})",
        "=" * 60,
        f"ЗАДАЧА: {task}",
        "",
        "ХОД РАБОТЫ:",
        *[f"  • {line}" for line in steps_log],
        "",
        "ИТОГОВЫЙ ОТЧЁТ ОРКЕСТРАТОРА:",
        report,
    ]
    path.write_text("\n".join(body), encoding="utf-8")
    return path
