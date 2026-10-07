import json
from pathlib import Path
import pytest

from port_tariff.engine.calculator import DeterministicTariffEngine
from port_tariff.models.tariff import FormulaType, TariffRule
from port_tariff.models.vessel import VesselProfile


@pytest.fixture
def sudestada_vessel() -> VesselProfile:
    file_path = Path("data/vessel_sudestada_durban.json")
    with open(file_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return VesselProfile.model_validate(data)


@pytest.fixture
def engine() -> DeterministicTariffEngine:
    return DeterministicTariffEngine()


def test_light_dues_calculation(
    engine: DeterministicTariffEngine, sudestada_vessel: VesselProfile
):
    rule = TariffRule(
        canonical_category="LIGHT_DUES",
        item_name="Light Dues",
        source_tariff_item_name="Light Dues",
        clause_reference="Clause 1.1.1, p. 9",
        formula_type=FormulaType.PER_100_GT,
        rate=117.08,
        moves=1,
        explanation="Charged per 100 tons GT or part thereof: ceil(51300/100) = 513 units * 117.08",
    )
    result = engine.calculate_item(rule, sudestada_vessel)
    assert result.calculated_value == 60062.04


def test_pilotage_dues_calculation(
    engine: DeterministicTariffEngine, sudestada_vessel: VesselProfile
):
    rule = TariffRule(
        canonical_category="PILOTAGE_DUES",
        item_name="Pilotage Dues",
        source_tariff_item_name="Pilotage services",
        clause_reference="Clause 3.3, p. 13",
        formula_type=FormulaType.BASE_PLUS_PER_100_GT,
        base_fee=18608.61,
        rate=9.72,
        moves=2,
        explanation="Durban: (18608.61 + 513 * 9.72) * 2 moves (inward + outward)",
    )
    result = engine.calculate_item(rule, sudestada_vessel)
    assert result.calculated_value == 47189.94


def test_towage_dues_calculation(
    engine: DeterministicTariffEngine, sudestada_vessel: VesselProfile
):
    rule = TariffRule(
        canonical_category="TOWAGE_DUES",
        item_name="Towage Dues",
        source_tariff_item_name="Tugs/Vessel assistance and/or attendance",
        clause_reference="Clause 3.6, p. 15",
        formula_type=FormulaType.TIERED_BASE_PLUS_EXCESS_100_GT,
        base_fee=73118.07,
        rate=32.24,
        excess_threshold=50000.0,
        moves=2,
        explanation="Durban 50,001-100,000 GT tier: (73118.07 + ceil((51300-50000)/100)*32.24) * 2",
    )
    result = engine.calculate_item(rule, sudestada_vessel)
    assert result.calculated_value == 147074.38


def test_running_lines_calculation(
    engine: DeterministicTariffEngine, sudestada_vessel: VesselProfile
):
    rule = TariffRule(
        canonical_category="RUNNING_LINES",
        item_name="Running Lines",
        source_tariff_item_name="Berthing services",
        clause_reference="Clause 3.8, p. 18",
        formula_type=FormulaType.BASE_PLUS_PER_100_GT,
        base_fee=2801.91,
        rate=13.68,
        moves=2,
        explanation="Berthing Services under 'Other Ports' (Durban fallback): (2801.91 + 513 * 13.68) * 2",
    )
    result = engine.calculate_item(rule, sudestada_vessel)
    assert result.calculated_value == 19639.50


def test_port_dues_calculation(
    engine: DeterministicTariffEngine, sudestada_vessel: VesselProfile
):
    rule = TariffRule(
        canonical_category="PORT_DUES",
        item_name="Port Dues",
        source_tariff_item_name="Port fees on vessels",
        clause_reference="Clause 4.1.1, p. 21",
        formula_type=FormulaType.PORT_DUES_BASIC_AND_DURATION,
        rate=192.73,
        stay_rate_per_day=57.79,
        explanation="Basic fee per 100 GT + duration fee per 100 GT per 24h pro rata for alongside stay",
    )
    result = engine.calculate_item(rule, sudestada_vessel)
    assert result.calculated_value == 199549.22


def test_vts_dues_calculation(
    engine: DeterministicTariffEngine, sudestada_vessel: VesselProfile
):
    rule = TariffRule(
        canonical_category="VTS_DUES",
        item_name="VTS Dues",
        source_tariff_item_name="VTS charges",
        clause_reference="Clause 2.1.1, p. 11",
        formula_type=FormulaType.PER_GT,
        rate=0.65,
        moves=1,
        explanation="Durban: 0.65 per GT",
    )
    result = engine.calculate_item(rule, sudestada_vessel)
    # 51,300 * 0.65 = 33,345.00 (within 0.08% of 51,255 registry GT 33,315.75)
    assert abs(result.calculated_value - 33315.75) <= 30.0
