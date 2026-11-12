from __future__ import annotations

from ..data import loader
from ..models import HeatingResult, Part, Quantity

INDUCTION_PROGRAM_THRESHOLD = 20_000


def choose_heater(part: Part, mass_blank_kg: float) -> tuple[str, str]:
    if part.heating == "induction":
        return "induction", "Задано пользователем"
    if part.annual_program >= INDUCTION_PROGRAM_THRESHOLD and mass_blank_kg <= 50:
        return (
            "induction",
            f"Годовая программа {part.annual_program} шт. — индукционный нагрев "
            f"даёт меньший угар и стабильную температуру",
        )
    return "flame", "Камерная газовая печь — универсальный вариант для серийного производства"


def hold_time(heater: str, size_mm: float) -> Quantity | None:
    per_mm = loader.heating_hold_min_per_mm(heater)
    if per_mm <= 0 or size_mm <= 0:
        return None
    block = loader.load("losses.json")["heating_hold"]
    return Quantity(
        round(size_mm * per_mm), "мин", block["source"], False,  # время выдержки t = норма * размер, мин
        f"t = {per_mm:g} мин/мм * {size_mm:g} мм",
    )


def calculate(part: Part, mass_blank_kg: float) -> HeatingResult:
    res = HeatingResult()
    mat = loader.material(part.material_grade)
    src = loader.source_of("materials.json")
    verified = loader.is_verified("materials.json")

    res.temp_start = Quantity(
        mat["forging_temp_start_c"], "°C", f"{src}; марка {mat['grade']}", verified,
        "Температура начала ковки",
    )
    res.temp_end = Quantity(
        mat["forging_temp_end_c"], "°C", f"{src}; марка {mat['grade']}", verified,
        "Температура конца ковки",
    )

    treat = loader.heat_treatment(part.material_grade)
    res.treat_kind = treat["kind"]
    res.treat_cooling = treat["cooling"]
    res.treat_source = treat["source"]
    if treat["temp_c"]:
        low, high = treat["temp_c"]
        res.treat_temp_min = Quantity(low, "°C", treat["source"], bool(treat["standard"]),
                                      f"{treat['kind']}, нижняя граница")
        res.treat_temp_max = Quantity(high, "°C", treat["source"], bool(treat["standard"]),
                                      f"{treat['kind']}, верхняя граница")

    code, reason = choose_heater(part, mass_blank_kg)
    res.heater = code
    if code == "induction":
        eq = loader.equipment("furnace_induction")
        res.scale_percent = Quantity(
            loader.scale_percent("induction"), "%", loader.source_of("losses.json"), False, "Угар"
        )
    else:
        eq = loader.equipment("furnace_flame")
        res.scale_percent = Quantity(
            loader.scale_percent("flame_first"), "%", loader.source_of("losses.json"), False, "Угар"
        )
    res.heater_name = f"{eq['name_ru']} ({reason})"

    interval = res.temp_start.value - res.temp_end.value  # интервал ковочных температур, °C
    if interval < 300:
        res.warnings.append(
            f"Узкий температурный интервал ковки ({interval:.0f} °C) — "
            f"вероятен подогрев между операциями"
        )
        res.heats_count = 2
    else:
        res.heats_count = 1

    return res
