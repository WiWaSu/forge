from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .. import names


TITLE_KINDS = {6: "title", 23: "note"}


@dataclass
class Row:
    height_mm: float
    cells: list[tuple]


@dataclass
class Sheet:
    title: str
    pages: list[list[Row]] = field(default_factory=list)


def fmt(value: Any, digits: int = 2) -> str:
    if value is None:
        return "—"
    text = f"{float(value):.{digits}f}"
    return text.rstrip("0").rstrip(".") if "." in text else text


def _q(obj: Any, key: str) -> Any:
    item = (obj or {}).get(key)
    return item.get("value") if isinstance(item, dict) else None


def steel_name(grade: str) -> str:
    grade = str(grade or "").strip()
    if not grade:
        return ""
    return grade if grade.lower().startswith("ст") else "Сталь " + grade


def wrap_line(text: str, limit: int) -> list[str]:
    out: list[str] = []
    line = ""
    for word in str(text).split():
        if line and len(line) + 1 + len(word) > limit:
            out.append(line)
            line = word
        else:
            line = f"{line} {word}" if line else word
    if line:
        out.append(line)
    return out


def body_rows(data: dict[str, Any]) -> list[dict[str, Any]]:
    by_code = {t["code"]: t for t in data.get("transitions") or []}
    rows: list[dict[str, Any]] = []
    for op in (data.get("route") or {}).get("operations") or []:
        rows.append({"sym": "А", "cells": {17: op.get("number", ""), 18: op.get("name", "")}})
        equipment = names.equipment_name(op.get("equipment") or "")
        if equipment and equipment != "—":
            rows.append({"sym": "Б", "cells": {20: equipment}})
        for t in (by_code.get(op.get("code")) or {}).get("transitions") or []:
            for piece in wrap_line(f"{t.get('number')}. {t.get('text', '')}", 100):
                rows.append({"sym": "О", "wide": piece})
    return rows


def _block_rows(block: dict[str, Any]) -> list[list[Any]]:
    return block.get("rows") or [[1, block["columns"]]] * block["lines"]


def _block_width(block: dict[str, Any]) -> float:
    return sum(w for _, w in _block_rows(block)[0][1])


def _block_lines(block: Any, lines: int, values: dict[int, str]) -> list[list[tuple]]:
    if not isinstance(block, dict):
        return [[(block, "", "void")]] * lines
    rows, labels = _block_rows(block), block.get("labels")
    out = [[(_block_width(block), "", "void")]] * (lines - sum(span for span, _ in rows))
    for i, (span, cells) in enumerate(rows):
        line = []
        for j, (no, w) in enumerate(cells):
            text, kind = (labels[i], "cap") if labels and j == 0 else (values.get(no, ""), TITLE_KINDS.get(no, ""))
            line.append((w, text, kind, span) if span > 1 else (w, text, kind))
        out.append(line)
        out.extend([[(w, "", "under") for _, w in cells]] * (span - 1))
    return out


def _zone(blocks: list[Any], line_mm: float, values: dict[int, str]) -> list[Row]:
    lines = max(sum(span for span, _ in _block_rows(b)) for b in blocks if isinstance(b, dict))
    parts = [_block_lines(b, lines, values) for b in blocks]
    return [Row(line_mm, [c for part in parts for c in part[i]]) for i in range(lines)]


def _band(block: dict[str, Any], values: dict[int, str]) -> Row:
    return Row(block["height_mm"], [(w, values.get(no, ""), TITLE_KINDS.get(no, "")) for no, w in block["columns"]])


def _title(forms: dict[str, Any], doc: str, standard: str, form: str, part: dict[str, Any],
           page: int, pages: int, note: str = "") -> tuple[list[Row], list[Row], Row]:
    tb, width = forms["title_block"], forms["sheet"]["field_width_mm"]
    b, line = tb["blocks"], tb["line_mm"]
    code, name = tb["documents"][doc]
    values = {2: part.get("designation") or "", 6: part.get("name") or "", 26: str(pages), 27: str(page),
              28: code, 29: name, 23: note if page == 1 else ""}
    gap = round(width - _block_width(b["Б4ф1"]) - _block_width(b["Б3ф1в"]) - _block_width(b["Б3ф1а"]), 3)
    top = [Row(tb["head_mm"], [(width, f"{standard}    Форма {form}", "head")]),
           *_zone([b["Б4ф1"], gap, b["Б3ф1в"], b["Б3ф1а"]], line, values)]
    if page == 1:
        field_rows = [_band(b["Б5ф1"], values), *_zone([b["Б2ф1"], b["Б1ф1"]], line, values)]
    else:
        blank = round(width - 2 * _block_width(b["Б3ф1б"]) - _block_width(b["Б1ф1а"]), 3)
        field_rows = [_band(b["Б5ф1а"], values),
                      *_zone([{"rows": [[3, [[0, blank]]]]}, b["Б3ф1б"], b["Б3ф1б"], b["Б1ф1а"]], line, values)]
    return top, field_rows, _band(b["Б6ф1"], values)


def _close(forms: dict[str, Any], top: list[Row], out: list[Row], bottom: Row) -> list[Row]:
    rest = forms["title_block"]["field_height_mm"] - sum(r.height_mm for r in out)
    filler = [Row(round(rest, 3), [(forms["sheet"]["field_width_mm"], "", "")])] if rest > 0.01 else []
    return top + out + filler + [bottom]


def _material_text(calc: dict[str, Any]) -> str:
    blank, material, stock = calc.get("blank") or {}, calc.get("material") or {}, calc.get("stock") or {}
    part = calc.get("part") or {}
    return " / ".join([
        f"{stock.get('profile') or 'Круг'} {fmt(_q(blank, 'diameter'), 0)} {stock.get('gost') or ''}".strip(),
        f"{steel_name(material.get('grade') or part.get('material_grade'))} {material.get('gost') or ''}".strip(),
    ])


def _m02(calc: dict[str, Any]) -> dict[int, str]:
    forging, blank = calc.get("forging") or {}, calc.get("blank") or {}
    ratio = _q(blank, "consumption_ratio")
    return {
        4: "кг",
        5: fmt(_q(forging, "mass_part"), 2),
        6: "1",
        7: fmt(_q(blank, "mass_blank"), 2),
        8: fmt(ratio, 2) if ratio else "",
        9: "Прокат",
        10: f"Круг {fmt(_q(blank, 'diameter'), 0)}×{fmt(_q(blank, 'length'), 0)}",
        11: "1",
        12: fmt(_q(blank, "mass_blank"), 2),
    }


def _paginate(body: list[dict[str, Any]], first: int, rest: int) -> list[list[dict[str, Any]]]:
    pages, taken, capacity = [], 0, first
    while True:
        pages.append(body[taken:taken + capacity])
        taken += capacity
        capacity = rest
        if taken >= len(body):
            return pages


def _cells(form: dict[str, Any], key: str, values: dict[int, str], label: str) -> list[tuple[float, str, str]]:
    return [(w, label if no == 1 else str(values.get(no, "")), "") for no, w in form["rows"][key]]


def _captions(form: dict[str, Any], key: str, graph_names: dict[int, str], skip: tuple[int, ...]) -> list[tuple[float, str, str]]:
    return [(w, "" if no in skip else graph_names.get(no, ""), "cap") for no, w in form["rows"][key]]


def route_card(data: dict[str, Any], forms: dict[str, Any]) -> Sheet:
    sheet_spec, rh, ch = forms["sheet"], forms["row_height_mm"], forms["caption_row_height_mm"]
    card = forms["route_card"]
    graph_names = {g["no"]: g["name"] for g in card["graphs"]}
    calc = data["calc"]
    part = calc.get("part") or {}
    width = sheet_spec["field_width_mm"]
    symbol_w = card["forms"]["1"]["rows"]["М01"][0][1]
    wide = width - symbol_w

    pages = _paginate(body_rows(data), card["forms"]["1"]["body_rows"], card["forms"]["1б"]["body_rows"])
    sheet = Sheet("МК ГОСТ 3.1118")
    for index, rows in enumerate(pages):
        name = "1" if index == 0 else "1б"
        form = card["forms"][name]
        top, out, bottom = _title(forms, "route_card", "ГОСТ 3.1118-82", name, part, index + 1, len(pages))
        if index == 0:
            out.append(Row(rh, _cells(form, "М01", {2: _material_text(calc)}, "М01")))
            out.append(Row(ch, _captions(form, "М02", graph_names, (1, 13))))
            out.append(Row(rh, _cells(form, "М02", _m02(calc), "М02")))
        for key in form["captions"]:
            if key != "М02":
                out.append(Row(ch, _captions(form, key, graph_names, (1, 13))))
        number = 3 if index == 0 else 1
        for row in rows:
            label = f"{row['sym']}{number:02d}"
            number += 1
            if "wide" in row:
                out.append(Row(rh, [(symbol_w, label, ""), (wide, row["wide"], "")]))
            else:
                out.append(Row(rh, _cells(form, row["sym"], row["cells"], label)))
        for _ in range(len(rows), form["body_rows"]):
            out.append(Row(rh, _cells(form, "А", {}, f"{number:02d}")))
            number += 1
        sheet.pages.append(_close(forms, top, out, bottom))
    return sheet


def forging_card(data: dict[str, Any], forms: dict[str, Any]) -> Sheet:
    sheet_spec, rh, ch = forms["sheet"], forms["row_height_mm"], forms["caption_row_height_mm"]
    card, route = forms["forging_card"], forms["route_card"]
    graph_names = {g["no"]: g["name"] for g in route["graphs"]}
    graph_names.update({g["no"]: g["name"] for g in card["graphs"] if g.get("name")})
    calc = data["calc"]
    part, blank = calc.get("part") or {}, calc.get("blank") or {}
    width = sheet_spec["field_width_mm"]
    first = card["forms"]["1"]
    symbol_w = first["rows"]["М01"][0][1]
    wide = width - symbol_w

    m02 = _m02(calc)
    m02[31] = fmt(_q(blank, "forgings_per_blank"), 0)
    utilization = _q(blank, "forging_utilization")
    m02[32] = fmt(utilization, 2) if utilization else ""

    items = blank.get("material_items") or []
    groups = [items[i:i + 4] for i in range(0, len(items), 4)] or [[]]

    pages = _paginate(body_rows(data), first["body_rows"] - (len(groups) - 1), card["forms"]["1а"]["body_rows"])
    sheet = Sheet("КТП ГОСТ 3.1403")
    for index, rows in enumerate(pages):
        name = "1" if index == 0 else "1а"
        form = card["forms"][name]
        top, out, bottom = _title(forms, "forging_card", "ГОСТ 3.1403-85", name, part, index + 1, len(pages),
                                  "Графы трудового нормирования (УТ, КР, КОИД, ОП, Кшт, Тп.з, Тшт) не рассчитываются. "
                                  "Графа 8 — заводской КИМ (масса заготовки на массу детали), графа 32 — КИП по стандарту.")
        number = 1
        if index == 0:
            m01 = []
            for no, w in first["rows"]["М01"]:
                if no == 1:
                    m01.append((w, f"М{number:02d}", ""))
                elif no == 2:
                    m01.append((w, _material_text(calc), ""))
                elif no == 0:
                    m01.append((w, first["labels"]["М01"]["0"], "cap"))
                else:
                    m01.append((w, fmt(_q(blank, "forge_ratio"), 2), ""))
            out.append(Row(rh, m01))
            number += 1
            out.append(Row(ch, _captions(first, "М02", graph_names, (1,))))
            out.append(Row(rh, _cells(first, "М02", m02, f"М{number:02d}")))
            number += 1
            for chunk in groups:
                cells = [(symbol_w, f"М{number:02d}", "")]
                for i, (_, w) in enumerate(first["rows"]["М03"][1:]):
                    item = chunk[i // 3] if i // 3 < len(chunk) else None
                    if not item:
                        text = ""
                    elif i % 3 == 0:
                        text = item.get("name", "")
                    elif i % 3 == 1:
                        text = fmt(_q(item, "mass"), 2)
                    else:
                        text = fmt(_q(item, "percent"), 1)
                    cells.append((w, text, ""))
                out.append(Row(rh, cells))
                number += 1
        for key in form["captions"]:
            if key != "М02":
                out.append(Row(ch, _captions(form, key, graph_names, (1,))))
        for row in rows:
            label = f"{row['sym']}{number:02d}"
            number += 1
            if "wide" in row:
                out.append(Row(rh, [(symbol_w, label, ""), (wide, row["wide"], "")]))
            else:
                out.append(Row(rh, _cells(form, row["sym"], row["cells"], label)))
        total = form["body_rows"] + (first["first_body_row"] - 1 if index == 0 else 0)
        while number - 1 < total:
            out.append(Row(rh, _cells(form, "А", {}, f"{number:02d}")))
            number += 1
        sheet.pages.append(_close(forms, top, out, bottom))
    return sheet
