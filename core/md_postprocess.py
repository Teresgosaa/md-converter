"""Постобработка MD-файлов после конвертации Docling.

Исправляет систематические артефакты:
  - потерянные суперскрипты (мм 2 → мм²)
  - слипшиеся единицы измерения (380Вна → 380 В на)
  - теги formula-not-decoded → предупреждение
  - разорванные абзацы          [только aggressive]
  - таблицы с merged заголовками [только aggressive]

Основная точка входа: postprocess(text, aggressive=False) -> str
"""
from __future__ import annotations

import re
import logging

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 1. Верхние индексы: мм 2 → мм², мм 3 → мм³
#
# Применяем ТОЛЬКО после единиц из белого списка + пробел + 2|3 +
# пробел/пунктуация/конец строки. Никаких других контекстов.
# ---------------------------------------------------------------------------

_SUP_UNITS = r"(?:мм|см|дм|км|м)"
# (?<!\w) — lookbehind: единица не должна быть суффиксом слова («контактом» ≠ «м»)
# Lookahead: после цифры — пробел/пунктуация/конец строки, не буква и не цифра
_SUP_RE = re.compile(
    rf"(?<!\w)({_SUP_UNITS}) ([23])(?=[ ,;.()\[\]\n\r\t]|$)"
)
_SUP_CLEANUP = re.compile(r"([²³]) +([,;.)\]])")
_SUP_MAP = {"2": "²", "3": "³"}


def fix_superscripts(text: str) -> str:
    def _replace(m: re.Match) -> str:
        return m.group(1) + _SUP_MAP[m.group(2)]

    new, n = _SUP_RE.subn(_replace, text)
    new = _SUP_CLEANUP.sub(r"\1\2", new)
    if n:
        log.info(f"[суперскрипты] {n} замен")
    return new


# ---------------------------------------------------------------------------
# 2. Слипшиеся единицы: 380 Вна → 380 В на, 1 Апри → 1 А при
#
# Составные единицы (кВт, Гц, …) проверяются ДО одиночных (В, А) —
# порядок в alternation гарантирует, что кВт не распадётся на кВ + т.
# ---------------------------------------------------------------------------

# Порядок важен: от длинных к коротким, чтобы regex не «съедал» часть единицы
_GLUED_UNITS = (
    "кВт|МВт|мВт|кВА|МВА|кВАр|МВАр"   # составные с В
    "|кГц|МГц"                           # составные с Гц
    "|кОм|МОм"                           # сопротивление
    "|кВ|мВ|мА|кА"                       # составные с одной буквой
    "|Вт|Гц|Ом"                          # трёхбуквенные одиночные
    "|В|А"                               # одиночные — строго последними
)
_GLUED_RE = re.compile(
    rf"(\d)\s*({_GLUED_UNITS})([а-яёА-ЯЁ])"
)


def fix_glued_units(text: str) -> str:
    def _replace(m: re.Match) -> str:
        return f"{m.group(1)} {m.group(2)} {m.group(3)}"

    new, n = _GLUED_RE.subn(_replace, text)
    if n:
        log.info(f"[слипшиеся единицы] {n} замен")
    return new


# ---------------------------------------------------------------------------
# 3. Разорванное «Вт»: «В т» / «кВ т» / «МВ т» после числа  [strict]
# ---------------------------------------------------------------------------

_SPLIT_WATT_RE = re.compile(
    r"(\d[.,]?\d*)\s*(кВ|МВ|мВ|В)\s+т(?!\w)"
)


def fix_split_watt(text: str) -> str:
    def _replace(m: re.Match) -> str:
        return f"{m.group(1)} {m.group(2)}т"

    new, n = _SPLIT_WATT_RE.subn(_replace, text)
    if n:
        log.info(f"[разорванный Вт] {n} замен")
    return new


# ---------------------------------------------------------------------------
# 4. formula-not-decoded → предупреждение  [strict]
# ---------------------------------------------------------------------------

_FORMULA_RE = re.compile(r"<!--\s*formula-not-decoded\s*-->")


def fix_formula_tags(text: str) -> str:
    new, n = _FORMULA_RE.subn(
        "⚠️ [Нераспознанный элемент — см. оригинал PDF]", text
    )
    if n:
        log.info(f"[formula-not-decoded] {n} замен")
    return new


# ---------------------------------------------------------------------------
# 4. Разорванные абзацы  [aggressive — рискованно для сложных документов]
#
# Строка без точки/двоеточия в конце + следующая непустая начинается
# со строчной буквы → склеить.
# ---------------------------------------------------------------------------

_ENDS_WITHOUT_STOP = re.compile(r"[^.:\]\)\?!…»\"]\s*$")
_STARTS_LOWERCASE = re.compile(r"^[а-яёa-z]")


def fix_torn_paragraphs(text: str) -> str:
    lines = text.split("\n")
    result = []
    i = 0
    joined = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith("|") or line.startswith("#") or line.startswith("<!--"):
            result.append(line)
            i += 1
            continue

        j = i + 1
        while j < len(lines) and lines[j].strip() == "":
            j += 1

        if (
            j < len(lines)
            and _ENDS_WITHOUT_STOP.search(line.rstrip())
            and line.strip()
            and _STARTS_LOWERCASE.match(lines[j].strip())
            and not lines[j].startswith("|")
            and not lines[j].startswith("#")
        ):
            result.append(line.rstrip() + " " + lines[j].lstrip())
            joined += 1
            i = j + 1
        else:
            result.append(line)
            i += 1

    if joined:
        log.info(f"[разорванные абзацы] {joined} склеено")
    return "\n".join(result)


# ---------------------------------------------------------------------------
# 5. Таблицы с объединёнными заголовками  [aggressive — рискованно]
# ---------------------------------------------------------------------------

def _parse_table_rows(lines: list[str]) -> list[list[str]]:
    rows = []
    for line in lines:
        if not line.startswith("|"):
            break
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        rows.append(cells)
    return rows


def _is_separator_row(cells: list[str]) -> bool:
    return all(re.fullmatch(r"-+", c) for c in cells if c)


def _is_repeated(cells: list[str]) -> bool:
    non_empty = [c for c in cells if c]
    return len(non_empty) > 1 and len(set(non_empty)) == 1


def _cells_have_repetitions(cells: list[str]) -> bool:
    non_empty = [c for c in cells if c]
    return len(non_empty) > 1 and len(set(non_empty)) < len(non_empty)


def _shorten_group(text: str) -> str:
    text = text.strip()
    if "постоянного" in text.lower():
        return "Пост. ток"
    if "переменного" in text.lower():
        return "Перем. ток"
    words = text.split()
    return " ".join(words[:3]) if len(words) > 3 else text


def fix_merged_headers(text: str) -> str:
    lines = text.split("\n")
    result = []
    i = 0
    fixed = 0

    while i < len(lines):
        if not lines[i].startswith("|"):
            result.append(lines[i])
            i += 1
            continue

        table_start = i
        while i < len(lines) and lines[i].startswith("|"):
            i += 1
        table_lines = lines[table_start:i]

        rows = _parse_table_rows(table_lines)
        if len(rows) < 3:
            result.extend(table_lines)
            continue

        sep_idx = next(
            (idx for idx, r in enumerate(rows) if _is_separator_row(r)), None
        )
        if sep_idx is None:
            result.extend(table_lines)
            continue

        header_rows = rows[:sep_idx]
        data_rows = rows[sep_idx + 1:]

        has_repeats = any(_is_repeated(r) or _cells_have_repetitions(r) for r in header_rows)
        if not has_repeats or len(header_rows) < 2:
            result.extend(table_lines)
            continue

        values_row_idx = None
        for idx in range(len(header_rows) - 1, -1, -1):
            cells = header_rows[idx]
            non_empty = [c for c in cells if c]
            if non_empty and all(re.match(r"[\d,.\s]+$", c) for c in non_empty):
                values_row_idx = idx
                break

        if values_row_idx is None or values_row_idx == 0:
            result.extend(table_lines)
            continue

        values_row = header_rows[values_row_idx]
        group_rows = header_rows[:values_row_idx]
        n_cols = len(rows[0])

        col0 = next((r[0] for r in group_rows if r and r[0]), "")
        new_header = [col0]
        for col in range(1, n_cols):
            val = values_row[col] if col < len(values_row) else ""
            group = next((gr[col] for gr in group_rows if col < len(gr) and gr[col]), "")
            if group and val:
                new_header.append(f"{_shorten_group(group)} {val} В")
            elif val:
                new_header.append(val)
            elif group:
                new_header.append(group)
            else:
                new_header.append("")

        def _fmt_row(cells: list[str]) -> str:
            return "| " + " | ".join(cells) + " |"

        sep = "| " + " | ".join(["---"] * n_cols) + " |"
        new_table = [_fmt_row(new_header), sep] + [_fmt_row(dr) for dr in data_rows]
        result.extend(new_table)
        fixed += 1

    if fixed:
        log.info(f"[объединённые заголовки] {fixed} таблиц исправлено")
    return "\n".join(result)


# ---------------------------------------------------------------------------
# Конвейеры
# ---------------------------------------------------------------------------

# Всегда безопасные правила
PIPELINE_STRICT = [
    fix_split_watt,
    fix_superscripts,
    fix_glued_units,
    fix_formula_tags,
    fix_torn_paragraphs,
]

# Рискованные правила (включаются флагом --aggressive)
PIPELINE_AGGRESSIVE = [
    fix_merged_headers,
]


def postprocess(text: str, aggressive: bool = False) -> str:
    pipeline = PIPELINE_STRICT + (PIPELINE_AGGRESSIVE if aggressive else [])
    for fn in pipeline:
        text = fn(text)
    return text


