from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

BETA = Path(__file__).resolve().parents[1]
TABLES = BETA / "knowledge" / "reference" / "gost7505_tables.json"


def num(text: str) -> float | None:
    text = (text or "").strip().replace(",", ".")
    if text in ("", "-", "—", "св", "свыше"):
        return None
    return float(text)


def read_rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as f:
        sample = f.read(2048)
        f.seek(0)
        delim = ";" if sample.count(";") >= sample.count(",") else ","
        return list(csv.DictReader(f, delimiter=delim))


def build_matrix(rows: list[dict], value_keys: list[str], thick_ranges: list, size_ranges: list):
    values: dict[str, list] = {}
    for row in rows:
        idx = str(int(num(row["index"])))
        t = num(row.get("thickness_max", ""))
        s = num(row.get("size_max", ""))
        ti = thick_ranges.index(t) if t in thick_ranges else len(thick_ranges) - 1
        si = size_ranges.index(s) if s in size_ranges else len(size_ranges) - 1
        cell = [num(row[k]) for k in value_keys]
        matrix = values.setdefault(
            idx, [[None] * len(size_ranges) for _ in range(len(thick_ranges))]
        )
        matrix[ti][si] = cell[0] if len(cell) == 1 else cell
    return values


def fill_gaps(values: dict[str, list]) -> int:
    filled = 0
    for matrix in values.values():
        for row in matrix:
            last = None
            for i, cell in enumerate(row):
                if cell is None and last is not None:
                    row[i] = last
                    filled += 1
                elif cell is not None:
                    last = cell
    return filled


def check() -> int:
    data = json.loads(TABLES.read_text(encoding="utf-8"))
    meta = data["_meta"]
    print(f"filled = {meta.get('filled')}, verified = {meta.get('verified')}\n")

    ii = data["initial_index"]
    print(f"Таблица 2 (исходный индекс): метод {ii['method']}, verified = {ii['verified']}")
    print(f"  сверено на примерах из стандарта: {len(ii.get('cross_check', []))}")

    ma = data["main_allowances"]
    print(f"Таблица 3 (припуски): метод {ma['method']}, "
          f"рядов {len(ma['allowance_series'])}, длина ряда {len(ma['allowance_series']['rz100'])}")
    print(f"  проверенные индексы: {ma.get('verified_indices')}")

    lt = data["linear_tolerances"]
    print(f"Таблица 8 (допуски): метод {lt['method']}, длина ряда {len(lt['tolerance_series'])}")
    print(f"  проверенные индексы: {lt.get('verified_indices')}")

    aa = data["additional_allowances"]
    print(f"Таблицы 4-6 (доп. припуски): filled = {aa['filled']}")

    if not meta.get("verified"):
        print("\nЧисла восстановлены по двум перепечаткам стандарта и сверены между собой,")
        print("но не сверены с оригиналом. Что проверить — см. _meta.todo и knowledge/gost/README.txt")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Импорт таблиц ГОСТ 7505-89 из CSV")
    ap.add_argument("--allowances", type=Path, help="CSV с таблицей 3")
    ap.add_argument("--tolerances", type=Path, help="CSV с таблицей 8")
    ap.add_argument("--check", action="store_true", help="показать состояние таблиц")
    args = ap.parse_args()

    if args.check or (not args.allowances and not args.tolerances):
        return check()

    data = json.loads(TABLES.read_text(encoding="utf-8"))

    if args.allowances:
        block = data["main_allowances"]
        rows = read_rows(args.allowances)
        block["values"] = build_matrix(
            rows, ["allowance"], block["thickness_ranges_mm"], block["size_ranges_mm"]
        )
        gaps = fill_gaps(block["values"])
        block["filled"] = True
        print(f"Таблица 3: индексов {len(block['values'])}, строк {len(rows)}, "
              f"пустых ячеек заполнено по соседям: {gaps}")

    if args.tolerances:
        block = data["linear_tolerances"]
        rows = read_rows(args.tolerances)
        block["values"] = build_matrix(
            rows, ["tolerance", "upper", "lower"],
            block["thickness_ranges_mm"], block["size_ranges_mm"],
        )
        gaps = fill_gaps(block["values"])
        block["filled"] = True
        print(f"Таблица 8: индексов {len(block['values'])}, строк {len(rows)}, "
              f"пустых ячеек заполнено по соседям: {gaps}")

    both = data["main_allowances"]["filled"] and data["linear_tolerances"]["filled"]
    data["_meta"]["filled"] = bool(both)
    if both:
        data["_meta"]["todo"] = ["Проверить выборочно 5-10 значений по тексту стандарта"]

    TABLES.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"\nЗаписано: {TABLES}")
    print("Теперь движок берёт припуски и допуски из ГОСТ и помечает их standard=true.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
