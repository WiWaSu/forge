from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Literal, Optional


@dataclass
class Quantity:

    value: float
    unit: str = ""
    source: str = ""
    standard: bool = True
    formula: str = ""

    def __post_init__(self) -> None:
        if isinstance(self.value, (int, float)):
            self.value = round(float(self.value), 4)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def __float__(self) -> float:
        return float(self.value)

    def __repr__(self) -> str:
        flag = "" if self.standard else " [НЕ ГОСТ]"
        return f"{self.value} {self.unit}{flag}"


PrimitiveKind = Literal["cylinder", "tube", "box"]


@dataclass
class Primitive:

    kind: PrimitiveKind
    d: float = 0.0
    d_inner: float = 0.0
    l: float = 0.0
    a: float = 0.0
    b: float = 0.0
    c: float = 0.0
    subtract: bool = False
    along_axis: bool = True
    name: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


PartType = Literal["shaft", "gear", "flange", "ring", "lever", "cross", "combined"]
ProcessType = Literal[
    "die_forging_hammer", "die_forging_press", "die_forging_hcm", "free_forging"
]


@dataclass
class Part:

    name: str
    part_type: PartType
    material_grade: str
    primitives: list[Primitive] = field(default_factory=list)
    designation: str = ""
    annual_program: int = 1000
    envelope_kind: Literal["cylinder", "box"] = "cylinder"
    mass_kg: Optional[float] = None
    roughness_ra_um: float = 6.3
    machined_all_over: bool = True
    heating: Literal["flame", "induction"] = "flame"
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        return d

    @staticmethod
    def from_dict(data: dict[str, Any]) -> "Part":
        prims = [Primitive(**p) for p in data.get("primitives", [])]
        payload = {k: v for k, v in data.items() if k != "primitives"}
        return Part(primitives=prims, **payload)


@dataclass
class ForgingResult:

    steel_group: str = ""
    complexity: str = ""
    accuracy_class: str = ""
    initial_index: int = 0
    kr: Optional[Quantity] = None
    mass_part: Optional[Quantity] = None
    mass_calc: Optional[Quantity] = None
    mass_actual: Optional[Quantity] = None
    index_passes: int = 1
    index_basis: str = ""
    norm: str = "ГОСТ 7505-89"
    forging_kind: str = ""
    dimensions: list[dict[str, Any]] = field(default_factory=list)
    primitives: list[Primitive] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass
class MaterialItem:

    name: str = ""
    mass: Optional[Quantity] = None
    percent: Optional[Quantity] = None


@dataclass
class BlankResult:

    mass_flash: Optional[Quantity] = None
    mass_web: Optional[Quantity] = None
    mass_scale: Optional[Quantity] = None
    mass_cut_loss: Optional[Quantity] = None
    mass_blank: Optional[Quantity] = None
    volume_blank: Optional[Quantity] = None
    diameter: Optional[Quantity] = None
    length: Optional[Quantity] = None
    upset_ratio: Optional[Quantity] = None
    forge_ratio: Optional[Quantity] = None
    material_utilization: Optional[Quantity] = None
    consumption_ratio: Optional[Quantity] = None
    forgings_per_blank: Optional[Quantity] = None
    forging_utilization: Optional[Quantity] = None
    preform: list[dict[str, Any]] = field(default_factory=list)
    material_items: list[MaterialItem] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass
class HeatingResult:
    temp_start: Optional[Quantity] = None
    temp_end: Optional[Quantity] = None
    heater: str = ""
    heater_name: str = ""
    heats_count: int = 1
    scale_percent: Optional[Quantity] = None
    hold_time: Optional[Quantity] = None
    treat_hold_time: Optional[Quantity] = None
    treat_kind: str = ""
    treat_cooling: str = ""
    treat_temp_min: Optional[Quantity] = None
    treat_temp_max: Optional[Quantity] = None
    treat_source: str = ""
    warnings: list[str] = field(default_factory=list)


@dataclass
class EquipmentChoice:
    type: str = ""
    name_ru: str = ""
    param: str = ""
    value: float = 0.0
    unit: str = ""
    reason: str = ""


@dataclass
class ForceResult:
    projection_area: Optional[Quantity] = None
    force_required: Optional[Quantity] = None
    falling_mass_required: Optional[Quantity] = None
    trim_force_required: Optional[Quantity] = None
    main_equipment: Optional[EquipmentChoice] = None
    trim_equipment: Optional[EquipmentChoice] = None
    warnings: list[str] = field(default_factory=list)


@dataclass
class CalcResult:

    part: Optional[Part] = None
    process: ProcessType = "die_forging_hammer"
    process_reason: str = ""
    material: dict[str, Any] = field(default_factory=dict)
    stock: dict[str, Any] = field(default_factory=dict)
    forging: ForgingResult = field(default_factory=ForgingResult)
    blank: BlankResult = field(default_factory=BlankResult)
    heating: HeatingResult = field(default_factory=HeatingResult)
    force: ForceResult = field(default_factory=ForceResult)
    non_standard_values: list[str] = field(default_factory=list)
    non_standard: list[dict[str, Any]] = field(default_factory=list)
    normative: list[dict[str, str]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        def convert(obj: Any) -> Any:
            if isinstance(obj, Quantity):
                return obj.to_dict()
            if isinstance(obj, (Primitive, Part, EquipmentChoice)):
                return convert(asdict(obj))
            if isinstance(obj, dict):
                return {k: convert(v) for k, v in obj.items()}
            if isinstance(obj, list):
                return [convert(v) for v in obj]
            if hasattr(obj, "__dataclass_fields__"):
                return {k: convert(getattr(obj, k)) for k in obj.__dataclass_fields__}
            return obj

        return convert(self)
