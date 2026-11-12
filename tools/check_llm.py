from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from engine.calc import pipeline
from engine.llm import baseline, generate, stepwise
from engine.llm.client import DEFAULT_HOST, DEFAULT_MODEL, OllamaClient
from engine.models import Part

SAMPLES = Path(__file__).resolve().parents[1] / "knowledge" / "examples" / "parts.json"


def print_route(route: dict, title: str) -> None:
    print(f"\n  {title}")
    print(f"  {'№':<5}{'код':<16}{'наименование':<34}оборудование")
    print("  " + "-" * 74)
    for op in route["operations"]:
        print(f"  {op['number']:<5}{op['code']:<16}{op['name'][:33]:<34}{op['equipment']}")
    if route.get("summary"):
        print(f"\n  Итог: {route['summary']}")


def run_mode(calc, client: OllamaClient, mode: str, show_raw: bool) -> tuple[bool, float]:
    title = "ОДИН ЗАПРОС НА ВЕСЬ МАРШРУТ" if mode == "single" else "ПО ЧАСТЯМ: ЗАПРОС НА КАЖДОЕ РЕШЕНИЕ"
    print(f"\n--- {title} ---")

    started = time.monotonic()
    if mode == "single":
        result = generate.generate_route(calc, client)
    else:
        result = stepwise.build(calc, client)
    elapsed = time.monotonic() - started

    print(f"Источник маршрута: {result.source}   запросов: {result.attempts}   "
          f"время: {elapsed:.1f} с")

    if result.decisions:
        print()
        print(stepwise.decisions_table(result, show_raw=show_raw))

    if result.errors:
        print("\nОшибки, на которые модель переспрашивали:")
        for i, batch in enumerate(result.errors, 1):
            print(f"  попытка {i}:")
            for err in batch:
                print(f"    - {err}")

    if result.fallback_reason:
        print(f"\nОткат на правила: {result.fallback_reason}")

    if show_raw and result.raw_responses:
        print("\nСырой ответ модели (последний):")
        print("  " + result.raw_responses[-1][:2000].replace("\n", "\n  "))

    print_route(result.route, f"МАРШРУТ ({result.source})")

    if result.source in ("llm", "stepwise"):
        diff = generate.compare(result.route, baseline.build(calc))
        print("\n  Сравнение с эталоном по правилам:")
        print(f"    операций: модель {diff['operations_llm']}, правила {diff['operations_baseline']}")
        print(f"    совпадение состава: {diff['match_percent']}%")
        print(f"    порядок общих операций совпадает: {'да' if diff['same_order'] else 'нет'}")
        if diff["extra_in_llm"]:
            print(f"    модель добавила: {', '.join(diff['extra_in_llm'])}")
        if diff["missing_in_llm"]:
            print(f"    модель пропустила: {', '.join(diff['missing_in_llm'])}")

    print()
    return result.source in ("llm", "stepwise"), elapsed


def run_part(part: Part, client: OllamaClient, show_raw: bool, mode: str) -> list[tuple[str, bool, float]]:
    print("=" * 78)
    print(f"{part.name}   сталь {part.material_grade}   программа {part.annual_program} шт./год")
    print("=" * 78)

    calc = pipeline.calculate(part)
    print(f"\nТехпроцесс: {calc.process}")

    modes = ["single", "stepwise"] if mode == "both" else [mode]
    return [(m, *run_mode(calc, client, m, show_raw)) for m in modes]


def main() -> int:
    ap = argparse.ArgumentParser(description="Прогон маршрута через локальную модель")
    ap.add_argument("part", nargs="?", type=int, help="номер типовой детали 1..5")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--host", default=DEFAULT_HOST)
    ap.add_argument("--raw", action="store_true", help="показать сырой ответ модели")
    ap.add_argument("--mode", choices=["single", "stepwise", "both"], default="single",
                    help="single — один запрос на маршрут; stepwise — по частям; both — оба")
    args = ap.parse_args()

    client = OllamaClient(model=args.model, host=args.host)
    ok, message = client.available()
    print(message)
    if not ok:
        print("\nБез Ollama маршрут строится детерминированными правилами — "
              "система работает, но нейросеть не участвует.")
        return 1

    data = json.loads(SAMPLES.read_text(encoding="utf-8"))
    parts = [Part.from_dict(p) for p in data["parts"]]
    if args.part:
        parts = [parts[args.part - 1]]

    print()
    rows: list[tuple[str, bool, float]] = []
    for part in parts:
        rows.extend(run_part(part, client, args.raw, args.mode))

    print("=" * 78)
    print("ИТОГ")
    for mode in ("single", "stepwise"):
        subset = [r for r in rows if r[0] == mode]
        if not subset:
            continue
        good = sum(1 for r in subset if r[1])
        total_time = sum(r[2] for r in subset)
        name = "один запрос" if mode == "single" else "по частям  "
        print(f"  {name}: принято {good} из {len(subset)}, "
              f"суммарно {total_time:.1f} с, в среднем {total_time / len(subset):.1f} с на деталь")

    if any(not r[1] for r in rows):
        print("\nГде маршрут собран правилами — смотри ошибки выше. "
              "По ним правятся instructions/system.json и route_card.json.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
