from __future__ import annotations

import json
import math
import re
import unittest
from pathlib import Path

from engine import names
from engine.api import server
from engine.calc import forging, geometry, pipeline
from engine.calc import free_forging as ff
from engine.data import loader
from engine.models import Part, Primitive

SAMPLES = Path(__file__).resolve().parents[2] / "knowledge" / "examples" / "parts.json"
PARTS = {p["name"]: Part.from_dict(p) for p in json.loads(SAMPLES.read_text(encoding="utf-8"))["parts"]}
SHAFT, GEAR, FLANGE, RING, LEVER = (PARTS[n] for n in (
    "Вал ступенчатый", "Шестерня (диск с отверстием)", "Фланец", "Кольцо", "Рычаг"))


def envelope_mass(part: Part) -> float:
    factor = loader.gost7505()["complexity_note"]["envelope_factor"]
    return geometry.envelope_volume(part.primitives) * factor ** 3 / geometry.MM3_IN_M3 * loader.density()


class Reference(unittest.TestCase):

    def test_integrity(self):
        self.assertEqual(loader.check_integrity(), [])

    def test_steel_groups(self):
        for grade, group in (("20", "M1"), ("45", "M2"), ("У8А", "M3"), ("ШХ15", "M3")):
            self.assertEqual(forging.steel_group(grade), group)
        for mat in loader.load("materials.json")["materials"]:
            self.assertEqual(forging.steel_group(mat["grade"]), mat["group_hint"], mat["grade"])

    def test_unknown_material(self):
        with self.assertRaises(loader.ReferenceError):
            loader.material("Сталь-которой-нет")

    def test_part_types_match_server(self):
        types = loader.kr_part_types()
        self.assertEqual({t["code"] for t in types}, server.PART_TYPES)
        for t in types:
            self.assertFalse(loader.kr_group(t["group"])["code"].startswith(("4", "5")), t["code"])

    def test_names(self):
        self.assertEqual(names.part_type_name("ring"), "кольцо")
        self.assertIn("КГШП", names.process_name("die_forging_press"))
        self.assertEqual([names.gost_code(c) for c in ("T4", "C1", "M2", "")], ["Т4", "С1", "М2", ""])
        self.assertTrue(names.equipment_name("press_crank").startswith("Кривошипный"))
        self.assertEqual(names.part_type_name("звездолёт"), "звездолёт")

    def test_7829_rows_never_decrease(self):
        t = loader.load("gost7829_tables.json")
        for row in t["table2"]["rows"]:
            vals = [v[0] for v in row["values"] if v]
            self.assertEqual(vals, sorted(vals), row["length"])
        for row in t["table7"]["rows"]:
            for key in ("H", "D", "d"):
                vals = [v[0] for v in row[key] if v]
                self.assertEqual(vals, sorted(vals), (row["size"], key))


class Geometry(unittest.TestCase):

    def test_volumes(self):
        self.assertAlmostEqual(geometry.primitive_volume(Primitive(kind="cylinder", d=100, l=100)),
                               math.pi * 100 ** 2 / 4 * 100)
        disc_with_hole = [Primitive(kind="cylinder", d=100, l=20),
                          Primitive(kind="cylinder", d=50, l=20, subtract=True)]
        self.assertAlmostEqual(geometry.volume(disc_with_hole), math.pi * (100 ** 2 - 50 ** 2) / 4 * 20)

    def test_steel_cube_weighs_7_85_kg(self):
        self.assertAlmostEqual(geometry.mass([Primitive(kind="box", a=100, b=100, c=100)], loader.density()),
                               7.85, places=2)

    def test_envelope_is_the_smallest_figure(self):
        self.assertEqual(geometry.box_extents(LEVER.primitives), (220, 70, 40))
        self.assertAlmostEqual(geometry.envelope_volume(LEVER.primitives), 220 * 70 * 40)
        shaft = [Primitive(kind="cylinder", d=80, l=200), Primitive(kind="cylinder", d=60, l=120)]
        self.assertAlmostEqual(geometry.envelope_volume(shaft), math.pi * 80 ** 2 / 4 * 320)

    def test_plan(self):
        shaft = [Primitive(kind="cylinder", d=60, l=100), Primitive(kind="cylinder", d=80, l=200),
                 Primitive(kind="cylinder", d=50, l=100)]
        disc = [Primitive(kind="cylinder", d=200, l=30), Primitive(kind="cylinder", d=80, l=30, subtract=True)]
        short = [Primitive(kind="cylinder", d=100, l=60)]
        lever = [Primitive(kind="box", a=225, b=64, c=35), Primitive(kind="cylinder", d=74, l=45, along_axis=False)]
        for name, prims, upright, area, perimeter in (
            ("рычаг", lever, False, 225 * 64, 2 * (225 + 64)),
            ("вал вдоль разъёма", shaft, False, 60 * 100 + 80 * 200 + 50 * 100, 2 * 400 + 60 + 50 + 20 + 30),
            ("диск торцом", disc, True, math.pi * 200 ** 2 / 4, math.pi * 200),
            ("короткий вал торцом", short, False, math.pi * 100 ** 2 / 4, math.pi * 100),
        ):
            with self.subTest(name):
                got_area, got_perimeter = geometry.plan(prims, upright)
                self.assertAlmostEqual(got_area, area)
                self.assertAlmostEqual(got_perimeter, perimeter)


class Gost7505(unittest.TestCase):

    def test_initial_index_examples(self):
        for case in loader.gost7505()["initial_index"]["cross_check"]:
            got, _ = forging.initial_index(case["mass_kg"], case["M"], case["C"], case["T"])
            self.assertEqual(got, case["index"], case["source"])
        self.assertEqual(forging.initial_index(0.620, "M1", "C1", "T3"), (6, True))

    def test_initial_index_max(self):
        table = loader.gost7505()["initial_index"]
        top = (table["base_by_mass_kg"][-1]["base"] + max(table["shift_steel_group"].values())
               + max(table["shift_complexity"].values()) + max(table["shift_accuracy"].values()))
        self.assertEqual(top, table["index_max"])

    def test_complexity(self):
        self.assertEqual(forging.complexity(0.325 * 13.06, 13.06), "C2")

    def test_allowances_example_1(self):
        for size, thickness, ra, expected in ((60, False, 6.3, 1.0), (34, False, 0.8, 1.0),
                                              (31, True, 6.3, 1.0), (31, True, 0.8, 1.1),
                                              (21, True, 6.3, 0.9)):
            q = forging.main_allowance(6, size, size, ra_um=ra, is_thickness=thickness)
            self.assertEqual(q.value, expected, (size, thickness, ra))
            self.assertTrue(q.standard)

    def test_tolerances_example_1(self):
        for size, thickness, expected in ((62, False, (0.9, 0.6, -0.3)), (36, False, (0.8, 0.5, -0.3)),
                                          (23, True, (0.8, 0.5, -0.3)), (33.5, True, (0.8, 0.5, -0.3))):
            t = forging.linear_tolerance(6, size, thickness, max_thickness_mm=33.5)
            self.assertEqual((t["tolerance"].value, t["upper"].value, t["lower"].value), expected, size)

    def test_series_endpoints(self):
        for index, size, overall, ra, expected in ((1, 30, 20, 50.0, 0.4), (1, 30, 20, 6.3, 0.6),
                                                   (1, 30, 20, 0.8, 0.7), (23, 2000, 300, 50.0, 7.1),
                                                   (23, 2000, 300, 0.8, 10.0)):
            self.assertEqual(forging.main_allowance(index, size, overall, ra_um=ra).value, expected)
        for index, size, expected in ((1, 30, (0.3, 0.2, -0.1)), (23, 2000, (16.0, 10.0, -6.0))):
            t = forging.linear_tolerance(index, size)
            self.assertEqual((t["tolerance"].value, t["upper"].value, t["lower"].value), expected)

    def test_accuracy_classes_table_19(self):
        for equipment, flame, expected in (("press_crank", False, "T4"), ("hammer_die", False, "T4"),
                                           ("automat", False, "T2"), ("press_crank_closed", False, "T2"),
                                           ("precision", False, "T1"), ("automat", True, "T3"),
                                           ("press_crank", True, "T5"), ("hammer_forge", True, "T5")):
            self.assertEqual(forging.accuracy_class(equipment, flame_heating=flame), expected, equipment)
        for equipment in ("press_crank", "hammer_die"):
            with self.assertRaises(loader.ReferenceError):
                forging.accuracy_class(equipment, "T1")

    def test_flatness_table_5(self):
        for accuracy, size, expected in (("T3", 62, 0.2), ("T1", 62, 0.1), ("T5", 2000, 2.0), ("T5", 340, 0.8)):
            self.assertEqual(forging.flatness_allowance(accuracy, size).value, expected)

    def test_roughness_column(self):
        for ra, column in ((50.0, "rz100"), (6.3, "ra10"), (3.2, "ra10"), (0.8, "ra125")):
            self.assertEqual(forging.roughness_column(ra), column)

    def test_kr_groups(self):
        group = loader.kr_group("5")
        self.assertEqual((group["kr_min"], group["kr_max"]), (1.8, 2.2))
        self.assertIn("полые валы", group["examples"].lower())
        pierced = forging.kr_coefficient("ring", hole_formed_in_forging=True)
        self.assertIn("группа 2.1", pierced.source)
        self.assertEqual(pierced.value, (1.5 + 1.8) / 2)
        solid = forging.kr_coefficient("ring", hole_formed_in_forging=False)
        self.assertIn("группа 5", solid.source)
        self.assertEqual(solid.value, (1.8 + 2.2) / 2)
        self.assertIn("группа 4", forging.kr_coefficient("shaft", machined_all_over=False).source)

    def test_thickness_axis(self):
        boss = Primitive(kind="cylinder", d=40, l=50, along_axis=False)
        body = Primitive(kind="box", a=245, b=60, c=35)
        step = Primitive(kind="cylinder", d=80, l=200)
        for part_type, prim, axis, expected in (("lever", boss, "l", True), ("lever", boss, "d", False),
                                                ("lever", body, "c", True), ("lever", body, "a", False),
                                                ("lever", body, "b", False), ("shaft", step, "d", True),
                                                ("shaft", step, "l", False), ("gear", step, "l", True),
                                                ("gear", step, "d", False)):
            self.assertEqual(forging.is_thickness(part_type, prim, axis), expected, (part_type, axis))
        self.assertEqual(forging.max_thickness("lever", [body, boss]), 50)

    def test_lever_example_5(self):
        self.assertEqual(forging.main_allowance(16, 50, 50, 2.2, True).value, 2.7)
        self.assertEqual(forging.main_allowance(16, 35, 35, 3.2, True).value, 2.5)
        f = pipeline.calculate(LEVER).forging
        self.assertEqual(f.complexity, "C1")
        rows = {d["label"]: d for d in f.dimensions}
        flat = forging.flatness_allowance(f.accuracy_class, max(geometry.overall(LEVER.primitives).values())).value
        ra = LEVER.roughness_ra_um
        for label, size, thickness in (("высота тела рычага", 30, True), ("высота бобышки", 40, True),
                                       ("длина тела рычага", 220, False), ("ширина тела рычага", 60, False),
                                       ("внешний диаметр бобышки", 70, False)):
            expected = forging.main_allowance(f.initial_index, size, size, ra, thickness).value
            self.assertAlmostEqual(rows[label]["allowance_mm"], expected + (flat if thickness else 0), places=2)

    def test_restack_per_face(self):
        def cyl(d, l):
            return Primitive(kind="cylinder", d=d, l=l)
        for name, steps, expected in (
            ("вал", [cyl(60, 100), cyl(80, 200), cyl(50, 100)], [102, 204, 102]),
            ("уступ вниз", [cyl(80, 100), cyl(60, 100)], [103, 101]),
            ("канавка", [cyl(80, 100), cyl(50, 2), cyl(80, 100)], [103.02, 0, 103.02]),
        ):
            with self.subTest(name):
                forged = geometry.scale_primitives(steps, lambda size, p, key: 0.0)
                forging.restack(steps, forged, lambda size, p, key: size / 100)
                self.assertEqual([round(q.l, 2) for q in forged], expected)

    def test_overall_length_allowance_by_ends(self):
        f = pipeline.calculate(SHAFT).forging
        rows = {d["label"]: d for d in f.dimensions}
        total = rows["общая длина"]
        self.assertEqual(total["part_mm"], 410)
        self.assertEqual(total["allowance_mm"], forging.main_allowance(f.initial_index, 410, 410, SHAFT.roughness_ra_um).value)
        steps = ("высота шейки левой", "высота средней ступени", "высота шейки правой")
        self.assertAlmostEqual(sum(rows[s]["forging_mm"] for s in steps), total["forging_mm"], delta=0.15)
        g = pipeline.calculate(GEAR).forging
        gear = {d["label"]: d for d in g.dimensions}
        self.assertAlmostEqual(gear["высота обода"]["forging_mm"] + gear["высота ступицы"]["forging_mm"],
                               gear["общая высота"]["forging_mm"], delta=0.15)
        self.assertEqual(round(g.primitives[-1].l, 1), gear["общая высота"]["forging_mm"])

    def test_through_hole_height_not_repeated(self):
        blind = Part.from_dict({**FLANGE.to_dict(), "primitives": [
            {"kind": "cylinder", "d": 200, "l": 25, "name": "диск"},
            {"kind": "cylinder", "d": 80, "l": 10, "subtract": True}]})
        for name, part, labels in (
            ("фланец", FLANGE, ["внешний диаметр диска", "высота диска", "внутренний диаметр"]),
            ("шестерня", GEAR, ["внешний диаметр обода", "высота обода", "внешний диаметр ступицы",
                                "высота ступицы", "внутренний диаметр", "общая высота"]),
            ("глухое отверстие", blind, ["внешний диаметр диска", "высота диска", "внутренний диаметр",
                                         "глубина отверстия"]),
        ):
            with self.subTest(name):
                self.assertEqual([d["label"] for d in pipeline.calculate(part).forging.dimensions], labels)


class Gost7829(unittest.TestCase):

    def test_example_1_shaft(self):
        for d, expected in ((300, (16, 5)), (200, (14, 4)), (263, (16, 5)), (284, (16, 5)),
                            (233, (15, 4)), (180, (14, 4))):
            self.assertEqual(ff.table2(1600, d)[:2], expected, d)
        self.assertEqual(ff.length_tolerance(5), 13)
        extra, main_i = ff.extra_allowances([316, 214, 279, 300, 248, 194], [80, 312, 60, 688, 200, 300])
        self.assertEqual(main_i, 3)
        self.assertEqual(extra, [3, 6, 3, 0, 4, 7])

    def test_example_2_disc_and_plate(self):
        for size, height, axis, expected in ((500, 170, "D", (19, 7)), (500, 170, "d", (25, 7)),
                                             (500, 170, "H", (16, 5)), (350, 120, "H", (12, 4)),
                                             (350, 120, "D", (15, 5)), (350, 120, "d", (21, 5))):
            self.assertEqual(ff.table7(size, height, axis)[:2], expected)

    def test_example_3_rolled_ring(self):
        self.assertEqual(ff.table8(520, 100, "D")[:2], (19, 7))
        self.assertEqual(ff.table8(520, 100, "d", wall=120)[:2], (23, 7))
        self.assertEqual(ff.table8(520, 100, "H")[:2], (14, 5))

    def test_example_4_bored_cylinder(self):
        row, _ = ff.table9(380, 280)
        self.assertEqual((row["H"], row["D"], row["d"]), ([22, 8], [20, 7], [25, 7]))

    def test_example_5_hollow_shaft(self):
        self.assertEqual(ff.table10(550, 270)[:2], (21, 8))
        self.assertEqual(ff.table11(165)[:2], (130, 10))

    def test_example_6_bushing_in_rings(self):
        for size, height, axis, expected in ((450, 220, "D1", (20, 8, -8)), (450, 220, "d", (26, 8, -8)),
                                             (450, 220, "H", (18, 6, -6)), (300, 220, "D2", (14, 6, -2))):
            self.assertEqual(ff.table12(size, height, axis)[:3], expected)
        self.assertTrue(ff.bushing_extra_on_hub(470, 118, 314, 238))
        self.assertEqual(314 + ff.table3(470 - 314), 322)

    def test_example_7_bushing_in_dies(self):
        for size, axis, expected in ((200, "D", (10, 4, -2)), (130, "D", (9, 4, -2)),
                                     (200, "d", (18, 4, -4)), (200, "H", (12, 3, -3))):
            self.assertEqual(ff.table12(size, 120, axis, "table13")[:3], expected)

    def test_free_forged_shaft(self):
        f = pipeline.calculate(SHAFT, process="free_forging").forging
        self.assertEqual([p.d for p in f.primitives], [70, 88, 59])
        total = next(d for d in f.dimensions if d["label"] == "общая длина")
        self.assertEqual((total["forging_mm"], total["upper"]["value"]), (430, 5))

    def test_free_forged_gear(self):
        f = pipeline.calculate(GEAR, process="free_forging").forging
        self.assertFalse(any(p.subtract for p in f.primitives))
        self.assertEqual([(p.name, p.d, p.l) for p in f.primitives], [("обод", 191, 49), ("ступица", 102, 30)])
        hub = next(d for d in f.dimensions if d["element"] == "ступица")
        self.assertEqual((hub["upper"]["value"], hub["lower"]["value"]), (3, -3))

    def test_tables_per_shape(self):
        for part, table in ((SHAFT, "табл. 2 и 3"), (FLANGE, "табл. 7"), (GEAR, "табл. 12"), (RING, "табл. 8")):
            f = pipeline.calculate(part, process="free_forging").forging
            self.assertEqual(f.norm, "ГОСТ 7829-70", part.name)
            self.assertIn(table, f.forging_kind)
            self.assertTrue(all("ГОСТ 7829-70" in d["tolerance"]["source"] for d in f.dimensions))
            self.assertGreater(f.mass_actual.value, geometry.mass(part.primitives, loader.density()))

    def test_lever_falls_back_to_7505(self):
        res = pipeline.calculate(LEVER, process="free_forging")
        self.assertIn("приближение", res.forging.norm)
        self.assertTrue(all(not d["tolerance"]["standard"] for d in res.forging.dimensions))
        self.assertTrue(res.non_standard_values)

    def test_small_hole_not_pierced(self):
        disc = Part(name="диск", part_type="gear", material_grade="45", primitives=[
            Primitive(kind="cylinder", d=200, l=60), Primitive(kind="cylinder", d=36, l=60, subtract=True)])
        self.assertEqual(ff.classify(disc)[0], "disc_hole")
        f = pipeline.calculate(disc, process="free_forging").forging
        self.assertFalse(any(p.subtract for p in f.primitives))

    def test_genitive(self):
        for word, expected in (("средняя ступень", "средней ступени"), ("шейка левая", "шейки левой"),
                               ("тело рычага", "тела рычага"), ("обод", "обода"), ("ступица", "ступицы")):
            self.assertEqual(forging.genitive(word), expected)


class SampleParts(unittest.TestCase):

    def test_process_choice(self):
        self.assertEqual(pipeline.calculate(SHAFT).process, "die_forging_hammer")
        self.assertEqual(pipeline.calculate(GEAR).process, "die_forging_press")
        single = Part.from_dict({**SHAFT.to_dict(), "annual_program": 50})
        self.assertEqual(pipeline.calculate(single).process, "free_forging")

    def test_mass_chain_and_ranges(self):
        for part in PARTS.values():
            with self.subTest(part.name):
                res = pipeline.calculate(part)
                f, b = res.forging, res.blank
                self.assertGreater(f.mass_part.value, 0)
                self.assertGreater(f.mass_actual.value, f.mass_part.value)
                self.assertGreater(b.mass_blank.value, f.mass_actual.value)
                self.assertIn(f.complexity, ("C1", "C2", "C3", "C4"))
                self.assertTrue(10 <= b.material_utilization.value <= 100)
                self.assertIn(b.diameter.value, loader.stock_diameters())
                self.assertIsNotNone(res.force.main_equipment)
                self.assertTrue(1100 <= res.heating.temp_start.value <= 1300)
                self.assertTrue(750 <= res.heating.temp_end.value <= 900)

    def test_index_from_forging_mass(self):
        for part in PARTS.values():
            with self.subTest(part.name):
                f = pipeline.calculate(part).forging
                m = f.mass_actual.value
                again, _ = forging.initial_index(m, f.steel_group, forging.complexity(m, envelope_mass(part)),
                                                 f.accuracy_class)
                self.assertEqual(again, f.initial_index)
                self.assertTrue(1 <= f.index_passes <= 3)

    def test_material_items_sum_to_blank(self):
        for part in PARTS.values():
            with self.subTest(part.name):
                b = pipeline.calculate(part).blank
                self.assertEqual(b.material_items[0].name, "Поковка")
                self.assertAlmostEqual(sum(i.mass.value for i in b.material_items), b.mass_blank.value, delta=1e-3 * b.mass_blank.value)
                self.assertAlmostEqual(sum(i.percent.value for i in b.material_items), 100.0, delta=0.3)
                self.assertTrue(all(i.mass.source for i in b.material_items))

    def test_kp_kip_and_consumption(self):
        for part in PARTS.values():
            with self.subTest(part.name):
                res = pipeline.calculate(part)
                b, f = res.blank, res.forging
                self.assertEqual(b.forgings_per_blank.value, 1)
                self.assertAlmostEqual(b.forging_utilization.value, f.mass_part.value / f.mass_actual.value, places=3)
                self.assertAlmostEqual(b.consumption_ratio.value, b.mass_blank.value / f.mass_part.value, places=3)
                self.assertFalse(b.consumption_ratio.standard)

    def test_ld_only_for_upsetting(self):
        for part in PARTS.values():
            with self.subTest(part.name):
                upset = part.part_type in ("gear", "flange", "ring", "cross")
                self.assertEqual(pipeline.calculate(part).blank.upset_ratio is not None, upset)

    def test_hold_time(self):
        flange = pipeline.calculate(FLANGE)
        self.assertAlmostEqual(flange.heating.hold_time.value, flange.blank.diameter.value)
        self.assertFalse(flange.heating.hold_time.standard)
        self.assertAlmostEqual(flange.heating.treat_hold_time.value, 60, delta=1)
        gear = pipeline.calculate(GEAR)
        self.assertEqual(gear.heating.heater, "induction")
        self.assertIsNone(gear.heating.hold_time)

    def test_ring_kr_estimate_close_to_actual(self):
        res = pipeline.calculate(RING)
        f = res.forging
        self.assertLess(abs(f.mass_calc.value - f.mass_actual.value) / f.mass_actual.value, 0.4)
        self.assertFalse([w for w in res.warnings if "Кр" in w])

    def test_kr_warning_names_part_type(self):
        part = Part(name="Проба", part_type="gear", material_grade="45",
                    primitives=[Primitive(kind="cylinder", d=600, l=600)], annual_program=5000)
        warning = next(w for w in pipeline.calculate(part).warnings if "группу Кр" in w)
        self.assertIn("«шестерня»", warning)

    def test_shaft_lies_along_parting(self):
        res = pipeline.calculate(SHAFT)
        body = sum(p.d * p.l for p in res.forging.primitives)
        self.assertGreater(res.force.projection_area.value, body)
        self.assertLess(res.force.projection_area.value, 1.3 * body)
        self.assertEqual(res.force.main_equipment.value, 2500)

    def test_preform_passes(self):
        for part, expected in (
            (SHAFT, [("протяжка", "шейка левая", 67.4), ("подкатка", "средняя ступень", 88.0),
                     ("протяжка", "шейка правая", 57.4)]),
            (LEVER, [("протяжка", "тело рычага", 53.6), ("протяжка", "бобышка", 65.3)]),
            (GEAR, []), (FLANGE, []), (RING, []),
        ):
            with self.subTest(part.name):
                b = pipeline.calculate(part).blank
                self.assertEqual([(x["pass"], x["element"], x["to_mm"]) for x in b.preform], expected)
                self.assertTrue(all(x["from_mm"] == b.diameter.value for x in b.preform))

    def test_heat_treatment_by_grade(self):
        for grade, kind, span, standard in (
            ("45", "нормализация", (845, 875), True),
            ("20", "нормализация", (885, 915), True),
            ("40Х", "нормализация", (840, 860), False),
            ("У8А", "отжиг", (730, 750), True),
            ("ШХ15", "отжиг", (780, 800), False),
            ("09Г2С", "нормализация", None, None),
        ):
            with self.subTest(grade):
                h = pipeline.calculate(Part.from_dict({**SHAFT.to_dict(), "material_grade": grade})).heating
                self.assertEqual(h.treat_kind, kind)
                if span is None:
                    self.assertIsNone(h.treat_temp_min)
                    self.assertIn("ГОСТ 8479-70", h.treat_source)
                    continue
                self.assertEqual((h.treat_temp_min.value, h.treat_temp_max.value), span)
                self.assertEqual(h.treat_temp_min.standard, standard)

    def test_treatment_above_critical_point(self):
        steels = loader.load("heat_treatment.json")["steels"]
        checked = {g: s for g, s in steels.items() if s.get("critical_c")}
        self.assertEqual(set(checked), {"40Х", "40ХН", "30ХГСА", "12ХН3А", "ШХ15"})
        for grade, s in checked.items():
            with self.subTest(grade):
                point = s["critical_c"]["Ac1" if s["kind"] == "отжиг" else "Ac3"]
                self.assertGreater(s["temp_c"][0], point)

    def test_hammer_reason(self):
        fr = pipeline.calculate(SHAFT).force
        self.assertEqual(fr.main_equipment.reason,
                         f"Выбран молот с массой падающих частей {fr.main_equipment.value:g} кг — "
                         f"ближайший больший к расчётной {fr.falling_mass_required.value:.0f} кг")

    def test_texts_use_cyrillic_gost_codes(self):
        latin = re.compile(r"\b[MCT]\d\b")
        for part in PARTS.values():
            calc = pipeline.calculate(part)
            texts = calc.warnings + calc.forging.warnings + [
                q.formula for q in (calc.blank.mass_flash, calc.blank.mass_web, calc.forging.kr) if q]
            for text in texts:
                self.assertIsNone(latin.search(text), (part.name, text))


class CardForms(unittest.TestCase):

    def setUp(self):
        self.forms = loader.route_card_forms()

    def all_forms(self):
        return [*self.forms["route_card"]["forms"].values(), *self.forms["forging_card"]["forms"].values()]

    def test_rows_fill_the_field(self):
        sheet = self.forms["sheet"]
        self.assertAlmostEqual(sheet["width_mm"] - 2 * sheet["side_margin_mm"], sheet["field_width_mm"])
        for form in self.all_forms():
            for row, cells in form["rows"].items():
                self.assertAlmostEqual(sum(w for _, w in cells), sheet["field_width_mm"], msg=row)

    def test_widths_on_print_step(self):
        step = self.forms["print_step_mm"]
        for form in self.all_forms():
            for cells in form["rows"].values():
                for _, w in cells:
                    self.assertAlmostEqual(w / step, round(w / step))

    def test_body_heights(self):
        h = self.forms["row_height_mm"]
        for form in self.all_forms():
            self.assertAlmostEqual(form["body_rows"] * h, form["body_height_mm"])
        card = self.forms["forging_card"]["forms"]
        self.assertEqual((card["1"]["body_rows"], card["1"]["first_body_row"], card["1а"]["body_rows"]), (13, 4, 17))

    def test_every_graph_described(self):
        known = {g["no"] for g in self.forms["route_card"]["graphs"]} | {
            g["no"] for g in self.forms["forging_card"]["graphs"]}
        for form in self.all_forms():
            for key, cells in form["rows"].items():
                for no, _ in cells:
                    if no == 0:
                        self.assertTrue(form["labels"][key]["0"])
                    else:
                        self.assertIn(no, known)

    def test_title_block(self):
        tb, step = self.forms["title_block"], self.forms["print_step_mm"]
        blocks = tb["blocks"]
        rows = {name: b.get("rows") or [[1, b["columns"]]] for name, b in blocks.items()}
        width = {name: sum(w for _, w in r[0][1]) for name, r in rows.items()}
        for name, block_rows in rows.items():
            for _, cells in block_rows:
                self.assertAlmostEqual(sum(w for _, w in cells), width[name], msg=name)
                for _, w in cells:
                    self.assertAlmostEqual(w / step, round(w / step), msg=name)
        for names, total in ((["Б1ф1"], 189.8), (["Б1ф1а"], 106.6), (["Б2ф1"], 96.2), (["Б3ф1а"], 80.6),
                             (["Б4ф1"], 75.4), (["Б2ф1", "Б1ф1"], 286), (["Б5ф1"], 286), (["Б5ф1а"], 286),
                             (["Б6ф1"], 286)):
            with self.subTest(names):
                self.assertAlmostEqual(sum(width[n] for n in names), total)
        self.assertAlmostEqual(tb["field_height_mm"], 42 * tb["line_mm"])
        self.assertAlmostEqual(tb["head_mm"] + tb["top_lines"] * tb["line_mm"], 23, delta=0.25)
        self.assertLessEqual(tb["head_mm"] + tb["top_lines"] * tb["line_mm"] + tb["field_height_mm"]
                             + blocks["Б6ф1"]["height_mm"], 209.9)

    def test_forging_card_layout(self):
        card = self.forms["forging_card"]
        own = {g["no"]: g["name"] for g in card["graphs"]}
        self.assertEqual((own[13], own[31], own[32]), ("Уковка", "КП", "КИП"))
        rows = card["forms"]["1"]["rows"]
        self.assertEqual([no for no, _ in rows["М01"]], [1, 2, 0, 13])
        self.assertEqual([no for no, _ in rows["М02"]][-2:], [31, 32])
        self.assertEqual([no for no, _ in rows["М03"]], [1] + [33, 34, 35] * 4)
        route = self.forms["route_card"]["forms"]["1"]["rows"]
        self.assertEqual((rows["А"], rows["Б"]), (route["А"], route["Б"]))


if __name__ == "__main__":
    unittest.main()
