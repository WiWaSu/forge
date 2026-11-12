from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

BETA = Path(__file__).resolve().parents[1]
SAMPLES = BETA / "engine" / "tests" / "parts_sample.json"

PY = sys.executable or "python"


def run(*args: str) -> int:
    print()
    return subprocess.call([PY, *args], cwd=str(BETA))


def part_names() -> list[str]:
    data = json.loads(SAMPLES.read_text(encoding="utf-8"))
    return [p["name"] for p in data["parts"]]


def choose_part() -> str | None:
    names = part_names()
    print("\n  Деталь:")
    for i, name in enumerate(names, 1):
        print(f"    {i}. {name}")
    print("    0. Все пять")
    raw = input("\n  Номер (Enter — все): ").strip()
    if raw in ("", "0"):
        return None
    if raw.isdigit() and 1 <= int(raw) <= len(names):
        return raw
    print("  Нет такой детали.")
    return choose_part()


def choose_mode() -> str:
    print("\n  Режим:")
    print("    1. Один запрос на весь маршрут")
    print("    2. По частям — отдельный запрос на каждое решение (точнее, но дольше)")
    print("    3. Оба подряд, со сравнением")
    raw = input("\n  Режим (Enter — один запрос): ").strip()
    return {"": "single", "1": "single", "2": "stepwise", "3": "both"}.get(raw, "single")


def yes(question: str) -> bool:
    return input(f"  {question} (д/Н): ").strip().lower() in ("д", "y", "да", "yes")


MENU = """
==============================================================
  FORGE — технологические карты ковки и штамповки
==============================================================

    1  ОТКРЫТЬ ИНТЕРФЕЙС  (браузер откроется сам)

  КОНСОЛЬ
    2  Расчёт поковки и заготовки
    3  Расчёт в JSON (то, что уходит нейросети)
    4  Построить маршрут через Ollama
    5  Проверить, доступна ли Ollama

  ПРОВЕРКА
    6  Прогнать все тесты
    7  Тесты расчётов
    8  Тесты нейросети
    9  Тесты интерфейса
   10  Состояние таблиц ГОСТ 7505

    0  Выход
"""


def all_tests() -> int:
    codes = []
    for title, module in (("Расчёты", "engine.tests.test_calc"),
                          ("Нейросеть", "engine.tests.test_llm"),
                          ("Интерфейс", "engine.tests.test_api")):
        print(f"\n--- {title} ---")
        codes.append(run("-m", module))
    print()
    print("Всё в порядке." if not any(codes) else "Есть падающие тесты — смотри выше.")
    return max(codes)


def check_ollama() -> int:
    sys.path.insert(0, str(BETA))
    from engine.llm.client import OllamaClient

    ok, message = OllamaClient().available()
    print(f"\n  {message}")
    if not ok:
        print("\n  Что делать:")
        print("    1) открыть отдельное окно и выполнить:  ollama serve")
        print("    2) один раз загрузить модель:           ollama pull qwen2.5:7b-instruct-q4_K_M")
    return 0


def main() -> int:
    while True:
        print(MENU)
        choice = input("  Выбор: ").strip()

        if choice == "0":
            return 0

        elif choice == "1":
            print("\n  Интерфейс откроется в браузере. Вернуться в меню — Ctrl+C в этом окне.")
            run("tools/serve.py")

        elif choice == "2":
            part = choose_part()
            run("tools/demo_calc.py", *( [part] if part else [] ))

        elif choice == "3":
            part = choose_part()
            run("tools/demo_calc.py", "--json", *( [part] if part else [] ))

        elif choice == "4":
            part = choose_part()
            mode = choose_mode()
            args = ["tools/check_llm.py"]
            if part:
                args.append(part)
            args += ["--mode", mode]
            if yes("Показать сырой ответ модели?"):
                args.append("--raw")
            run(*args)

        elif choice == "5":
            check_ollama()

        elif choice == "6":
            all_tests()

        elif choice == "7":
            run("-m", "engine.tests.test_calc")

        elif choice == "8":
            run("-m", "engine.tests.test_llm")

        elif choice == "9":
            run("-m", "engine.tests.test_api")

        elif choice == "10":
            run("tools/import_gost7505.py", "--check")

        else:
            print("\n  Нет такого пункта.")

        input("\n  Enter — в меню... ")


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print()
        raise SystemExit(0)
