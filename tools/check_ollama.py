from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from engine.calc import pipeline
from engine.llm import generate, prompt, stepwise, validate
from engine.llm.client import DEFAULT_HOST, DEFAULT_MODEL, LLMError, OllamaClient
from engine.models import Part

SAMPLES = Path(__file__).resolve().parents[1] / "engine" / "tests" / "parts_sample.json"
ANSWER_FILE = Path(__file__).resolve().parent / "last_answer.json"

OK = "  [ v ]"
NO = "  [ X ]"
WARN = "  [ ! ]"


def head(text: str) -> None:
    print()
    print(text)
    print("-" * 78)


def get_json(url: str, timeout: int = 10):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def main() -> int:
    ap = argparse.ArgumentParser(description="Почему маршрут строит не нейросеть")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--host", default=DEFAULT_HOST)
    ap.add_argument("--part", type=int, default=3, help="номер типовой детали 1..5")
    ap.add_argument("--keep-alive", default=None,
                    help="сколько держать модель в памяти, напр. 30m — лечит перезагрузку на каждый запрос")
    args = ap.parse_args()

    print("=" * 78)
    print("ДИАГНОСТИКА ЛОКАЛЬНОЙ МОДЕЛИ")
    print(f"адрес: {args.host}    ожидаемая модель: {args.model}")
    print("=" * 78)

    head("1. Служба Ollama")
    try:
        tags = get_json(f"{args.host}/api/tags")
    except (OSError, ValueError) as exc:
        print(f"{NO} Ollama не отвечает: {exc}")
        print("\nПРИЧИНА НАЙДЕНА: служба не запущена или слушает другой адрес.")
        print("  ollama serve")
        print("  если Ollama стоит на другом порту — python tools/check_ollama.py --host http://localhost:ПОРТ")
        return 1
    print(f"{OK} служба отвечает")
    try:
        print(f"      версия: {get_json(f'{args.host}/api/version').get('version')}")
    except (OSError, ValueError) as exc:
        print(f"{WARN} версия не получена: {exc}")

    head("2. Имя модели")
    names = [m.get("name", "") for m in tags.get("models", [])]
    if not names:
        print(f"{NO} в Ollama не загружено ни одной модели")
        print(f"\nПРИЧИНА НАЙДЕНА:  ollama pull {args.model}")
        return 1

    print("      загружено:")
    for m in tags.get("models", []):
        size = m.get("size", 0) / 1024 ** 3
        print(f"        {m.get('name')}   {size:.1f} ГБ")

    exact = args.model in names
    prefix = [n for n in names if n.startswith(args.model.split(":")[0])]

    if exact:
        print(f"{OK} имя совпадает точно")
    elif prefix:
        print(f"{NO} ТОЧНОГО совпадения нет, есть похожие: {', '.join(prefix)}")
        print()
        print("      Это и есть самая частая причина. Проверка доступности в client.available()")
        print("      считает совпадением любое имя с тем же началом, поэтому в интерфейсе")
        print("      горит зелёный кружок «Ollama доступна». Но chat() шлёт ТОЧНОЕ имя,")
        print("      Ollama отвечает 404, движок молча откатывается на правила.")
        print()
        print("      Лечится любым из двух способов:")
        print(f"        ollama pull {args.model}")
        print(f"        либо в engine/llm/client.py: DEFAULT_MODEL = \"{prefix[0]}\"")
        print()
        print(f"      Дальше проверяю с именем '{prefix[0]}' — чтобы увидеть остальные шаги.")
        args.model = prefix[0]
    else:
        print(f"{NO} модели '{args.model}' нет и похожих нет")
        print(f"\nПРИЧИНА НАЙДЕНА:  ollama pull {args.model}")
        return 1

    client = OllamaClient(model=args.model, host=args.host)

    if args.keep_alive:
        body = json.dumps({"model": args.model, "messages": [{"role": "user", "content": "ok"}],
                           "stream": False, "keep_alive": args.keep_alive}).encode()
        req = urllib.request.Request(f"{args.host}/api/chat", data=body,
                                     headers={"Content-Type": "application/json"})
        try:
            urllib.request.urlopen(req, timeout=600).read()
            print(f"      модель закреплена в памяти на {args.keep_alive}")
        except (OSError, ValueError) as exc:
            print(f"{WARN} не удалось задать keep_alive: {exc}")

    head("3. Простой запрос (проверка, что модель вообще отвечает)")
    started = time.monotonic()
    try:
        answer = client.chat([
            {"role": "user", "content": 'Ответь ровно так: {"ok": true}'}
        ])
    except LLMError as exc:
        print(f"{NO} {exc}")
        print("\nПРИЧИНА НАЙДЕНА: до модели запрос не доходит. Текст ошибки выше —")
        print("      если там 404, имя модели всё-таки не то; если таймаут —")
        print("      модель не помещается в память и грузится слишком долго.")
        return 1
    first_call = time.monotonic() - started
    print(f"{OK} ответ получен за {first_call:.1f} с")
    print(f"      {answer.strip()[:120]}")
    if first_call > 60:
        print(f"{WARN} первый ответ дольше минуты — модель грузится с диска.")
        print("      Если так на каждом запросе, добавьте --keep-alive 30m")

    started = time.monotonic()
    client.chat([{"role": "user", "content": 'Ответь ровно так: {"ok": true}'}])
    second_call = time.monotonic() - started
    print(f"      повторный запрос: {second_call:.1f} с", end="")
    if second_call > first_call * 0.6 and first_call > 20:
        print("  <- модель выгружается между запросами, нужен keep_alive")
    else:
        print("  (модель осталась в памяти)")

    head("4. Полный запрос маршрута")
    parts = json.loads(SAMPLES.read_text(encoding="utf-8"))["parts"]
    part = Part.from_dict(parts[max(1, min(len(parts), args.part)) - 1])
    calc = pipeline.calculate(part)
    print(f"      деталь: {part.name}, процесс {calc.process}")

    messages = prompt.build_route_messages(calc)
    chars = sum(len(m["content"]) for m in messages)
    print(f"      объём промпта: {chars} символов, примерно {chars // 3} токенов "
          f"(контекст {client.num_ctx})")
    if chars // 3 > client.num_ctx * 0.8:
        print(f"{WARN} промпт занимает почти весь контекст — модели негде отвечать,")
        print("      она обрежет ответ. Уменьшите число примеров в prompt.load_examples")

    started = time.monotonic()
    try:
        raw = client.chat(messages)
    except LLMError as exc:
        print(f"{NO} {exc}")
        return 1
    elapsed = time.monotonic() - started
    print(f"{OK} ответ за {elapsed:.1f} с, длина {len(raw)} символов")
    ANSWER_FILE.write_text(raw, encoding="utf-8")
    print(f"      сохранён в {ANSWER_FILE}")

    head("5. Проверка ответа")
    data, parse_error = generate.parse_response(raw)
    if parse_error:
        print(f"{NO} {parse_error}")
        print("      первые 300 символов ответа:")
        print("      " + raw[:300].replace("\n", "\n      "))
        return 1
    print(f"{OK} JSON разобран, операций: {len(data.get('operations') or [])}")

    errors = validate.validate(data, calc)
    if not errors:
        print(f"{OK} ответ прошёл проверку целиком")
        print("\nМодель работает и её маршрут принимается. Если в интерфейсе всё равно")
        print("стоит бейдж «правила» — смотрите, какой режим выбран в форме.")
        return 0

    print(f"{NO} проверку не прошёл, ошибок: {len(errors)}")
    for e in errors:
        print(f"        - {e}")

    naming = [e for e in errors if "наименование" in e]
    equip = [e for e in errors if "оборудование" in e]
    print()
    if naming or equip:
        print("      Обратите внимание: из этих ошибок")
        print(f"        {len(naming)} — расхождение в тексте наименования операции,")
        print(f"        {len(equip)} — оборудование написано словами вместо кода.")
        print("      Это придирки к форме, а не к технологии: маршрут по составу может")
        print("      быть верным, но check_semantics пункты 4 и 5 требуют совпадения")
        print("      строка-в-строку. 7B-модель так почти никогда не попадает,")
        print("      три попытки уходят впустую и движок откатывается на правила.")
        print("      Варианты: сверять наименование без учёта регистра и скобок,")
        print("      либо подставлять его из справочника по коду, а у модели брать")
        print("      только код, оборудование и обоснование.")

    head("6. Режим «по частям» — тот же вопрос, но мелкими запросами")
    started = time.monotonic()
    result = stepwise.build(calc, client)
    from_llm = sum(1 for d in result.decisions if d.get("source") == "llm")
    print(f"      решений всего: {len(result.decisions)}, из них от модели: {from_llm}, "
          f"время {time.monotonic() - started:.1f} с")
    if from_llm:
        print(f"{OK} в этом режиме модель участвует — им и пользуйтесь, пока не смягчена проверка")
    else:
        print(f"{NO} и здесь модель не ответила ни на один вопрос")
    print()
    print("      Важно: в режиме «по частям» бейдж в интерфейсе показывает «нейросеть»")
    print("      даже когда все решения приняты правилами. Смотрите столбец «кто решил»")
    print("      в таблице решений, а не бейдж.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
