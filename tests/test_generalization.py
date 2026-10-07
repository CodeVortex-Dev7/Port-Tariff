import json

import pytest

from port_tariff.agent.normalizer import CategoryNormalizer
from port_tariff.engine.calculator import DeterministicTariffEngine
from port_tariff.exceptions import InvalidTariffRuleError
from port_tariff.models.catalog import ChargeCatalog, ChargeEntry
from port_tariff.models.tariff import (
    FormulaComponent,
    Multiplier,
    QuantityBasis,
    TariffRule,
)
from port_tariff.models.vessel import VesselProfile
from port_tariff.rag.retriever import _ref_matches

VESSEL = VesselProfile.model_validate(
    {
        "port_name": "Testport",
        "vessel_metadata": {"name": "TEST"},
        "technical_specs": {
            "type": "Bulk Carrier",
            "dwt": 93274,
            "gross_tonnage": 51300,
            "loa_meters": 229.2,
            "beam_meters": 38.0,
        },
        "operational_data": {"days_alongside": 2.0, "num_operations": 2},
    }
)


def _rule(**kwargs) -> TariffRule:
    return TariffRule(
        canonical_category="custom charge",
        item_name="Custom",
        source_tariff_item_name="Custom",
        clause_reference="1.1",
        explanation="test",
        currency="EUR",
        **kwargs,
    )


def test_components_support_bases_the_presets_cannot_express():
    rule = _rule(
        components=[
            FormulaComponent(basis=QuantityBasis.DWT, rate=0.5, unit_size=1000)
        ]
    )
    result = DeterministicTariffEngine().calculate_item(rule, VESSEL)
    assert rule.canonical_category == "CUSTOM_CHARGE"
    assert result.calculated_value == 47.00  # ceil(93.274) = 94 units * 0.5


def test_components_combine_once_and_per_day_multipliers():
    rule = _rule(
        components=[
            FormulaComponent(basis=QuantityBasis.FLAT, rate=100.0),
            FormulaComponent(
                basis=QuantityBasis.GT,
                rate=2.0,
                unit_size=100,
                multiplier=Multiplier.PER_DAY,
            ),
        ]
    )
    result = DeterministicTariffEngine().calculate_item(rule, VESSEL)
    assert result.calculated_value == 2152.00  # 100 + 513 * 2 * 2.0 days


def test_missing_vessel_quantity_is_an_error_not_zero():
    rule = _rule(components=[FormulaComponent(basis=QuantityBasis.NT, rate=1.0)])
    with pytest.raises(InvalidTariffRuleError):
        DeterministicTariffEngine().calculate_item(rule, VESSEL)


def test_rule_without_formula_is_rejected():
    with pytest.raises(InvalidTariffRuleError):
        DeterministicTariffEngine().calculate_item(_rule(), VESSEL)


def _catalog() -> ChargeCatalog:
    def entry(category: str, name: str, aliases=None) -> ChargeEntry:
        return ChargeEntry(
            category=category,
            display_name=name,
            aliases=aliases or [],
            description="d",
            applies_when="per call",
            section_refs=["1.1"],
            search_queries=["q"],
        )

    return ChargeCatalog(
        currency="EUR",
        rate_selection_notes="none",
        charges=[
            entry("seaport_dues", "Seaport dues", ["Port fees on vessels"]),
            entry("ice_surcharge", "Ice surcharge"),
        ],
    )


def test_normalizer_maps_known_aliases_and_keeps_unknown_charges(tmp_path):
    aliases = tmp_path / "aliases.json"
    aliases.write_text(json.dumps({"PORT_DUES": ["port fees on vessels"]}))
    catalog = CategoryNormalizer(aliases).apply(_catalog())
    assert [c.category for c in catalog.charges] == ["PORT_DUES", "ICE_SURCHARGE"]


def test_normalizer_without_alias_file_is_identity(tmp_path):
    catalog = CategoryNormalizer(tmp_path / "missing.json").apply(_catalog())
    assert [c.category for c in catalog.charges] == ["SEAPORT_DUES", "ICE_SURCHARGE"]


@pytest.mark.parametrize(
    "ref, hint, expected",
    [
        ("3.3", "## 3.3 Pilotage services", True),
        ("3.3", "## 3.3.1 Pilotage rates", True),
        ("3.3", "## 13.3 Something else", False),
        ("3.3", "## 3.30 Something else", False),
        ("Clause 3.3, p. 13", "## 3.3 Pilotage services", True),
        ("Berthing services", "## 3.8 BERTHING SERVICES", True),
        ("", "## 3.3 Pilotage services", False),
    ],
)
def test_section_ref_matching(ref, hint, expected):
    assert _ref_matches(ref, hint) is expected
