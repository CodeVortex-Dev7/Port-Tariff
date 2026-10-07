from decimal import ROUND_CEILING, ROUND_HALF_UP, Decimal
from typing import List, Tuple, Union

from port_tariff.exceptions import InvalidTariffRuleError
from port_tariff.logging_config import get_logger
from port_tariff.models.response import (
    TariffCalculationResponse,
    TariffItemBreakdown,
)
from port_tariff.models.tariff import (
    FormulaComponent,
    Multiplier,
    QuantityBasis,
    TariffRule,
)
from port_tariff.models.vessel import VesselProfile

logger = get_logger(__name__)


class DeterministicTariffEngine:
    """Pure, deterministic execution engine for port tariff rules.

    Eliminates LLM arithmetic errors by calculating dues using exact
    mathematical definitions and standard decimal precision.
    """

    @staticmethod
    def round_currency(val: Union[float, Decimal]) -> float:
        """Rounds to 2 decimal places using bankers' HALF_UP rounding."""
        d = Decimal(str(val))
        return float(d.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))

    @staticmethod
    def _time_factor(comp: FormulaComponent, vessel: VesselProfile) -> Decimal:
        """Computes the time multiplier (days or hours), respecting round_days_up."""
        days = Decimal(str(vessel.billable_stay_days))
        if comp.multiplier is Multiplier.PER_HOUR:
            hours = days * 24
            if comp.round_days_up:
                hours = hours.to_integral_value(rounding=ROUND_CEILING)
            return hours
        # PER_DAY
        if comp.round_days_up:
            days = days.to_integral_value(rounding=ROUND_CEILING)
        return days

    @staticmethod
    def _quantity(basis: QuantityBasis, vessel: VesselProfile) -> Decimal:
        """Looks up the vessel quantity a formula component is measured against."""
        specs, ops = vessel.technical_specs, vessel.operational_data
        value = {
            QuantityBasis.GT: specs.gross_tonnage,
            QuantityBasis.NT: specs.net_tonnage,
            QuantityBasis.DWT: specs.dwt,
            QuantityBasis.LOA: specs.loa_meters,
            QuantityBasis.BEAM: specs.beam_meters,
            QuantityBasis.CARGO_MT: ops.cargo_quantity_mt,
        }.get(basis)
        if value is None:
            raise InvalidTariffRuleError(
                f"The vessel profile has no value for {basis.value}, "
                "which the tariff rule requires."
            )
        return Decimal(str(value))

    def _evaluate_component(
        self,
        rule: TariffRule,
        comp: FormulaComponent,
        vessel: VesselProfile,
        moves: int,
        days: Decimal,
    ) -> Tuple[Decimal, str]:
        rate = Decimal(str(comp.rate))
        unit_size = Decimal(str(comp.unit_size))
        # Values default to 0.0, so one the LLM failed to extract would otherwise
        # yield a silent 0.00.
        if rate <= 0 or unit_size <= 0:
            raise InvalidTariffRuleError(
                f"Tariff rule '{rule.item_name}' has a {comp.basis.value} component "
                "with a missing or non-positive rate/unit size. "
                "Refusing to report a charge of 0."
            )

        if comp.basis is QuantityBasis.FLAT:
            units, text = Decimal(1), f"{rate:f}"
        else:
            quantity = self._quantity(comp.basis, vessel)
            billable = max(Decimal(0), quantity - Decimal(str(comp.threshold)))
            units = billable / unit_size
            if comp.round_up:
                units = units.to_integral_value(rounding=ROUND_CEILING)
            text = f"{units:f} {comp.basis.value} units \u00d7 {rate:f}"

        if comp.multiplier in (Multiplier.PER_DAY, Multiplier.PER_HOUR):
            factor = self._time_factor(comp, vessel)
        else:
            factor = {
                Multiplier.ONCE: Decimal(1),
                Multiplier.PER_MOVE: Decimal(moves),
            }[comp.multiplier]
        if comp.multiplier is not Multiplier.ONCE:
            unit = {
                Multiplier.PER_MOVE: "moves",
                Multiplier.PER_DAY: "days",
                Multiplier.PER_HOUR: "hours",
            }[comp.multiplier]
            text += f" \u00d7 {factor:f} {unit}"
        return units * rate * factor, f"({text})"

    def calculate_item(
        self, rule: TariffRule, vessel: VesselProfile
    ) -> TariffItemBreakdown:
        # All money math is done in Decimal; floats only exist at the
        # LLM/JSON boundary.
        moves = max(1, rule.moves)
        days = Decimal(str(vessel.billable_stay_days))

        calc_value = Decimal("0")
        parts: List[str] = []
        for component in rule.resolved_components():
            amount, text = self._evaluate_component(
                rule, component, vessel, moves, days
            )
            calc_value += amount
            parts.append(text)
        formula_str = f"{' + '.join(parts)} = {calc_value:.2f} {rule.currency}"

        # Enforce tariff-specified floor/ceiling (only when explicitly set).
        # Applies AFTER all formula math so it correctly caps e.g. a per-GT
        # item whose computed value falls below the documented minimum.
        if rule.minimum_fee is not None:
            minimum_fee = Decimal(str(rule.minimum_fee))
            if calc_value < minimum_fee:
                formula_str += (
                    f"  [minimum fee {minimum_fee:.2f} {rule.currency} applied]"
                )
                calc_value = minimum_fee
        if rule.maximum_fee is not None:
            maximum_fee = Decimal(str(rule.maximum_fee))
            if calc_value > maximum_fee:
                formula_str += (
                    f"  [maximum fee {maximum_fee:.2f} {rule.currency} applied]"
                )
                calc_value = maximum_fee

        rounded_amount = self.round_currency(calc_value)

        logger.info(
            "engine.item.computed",
            category=rule.canonical_category,
            item_name=rule.item_name,
            amount=rounded_amount,
            currency=rule.currency,
        )

        return TariffItemBreakdown(
            canonical_category=rule.canonical_category,
            item_name=rule.item_name,
            source_tariff_item_name=rule.source_tariff_item_name,
            calculated_value=rounded_amount,
            currency=rule.currency,
            clause_reference=rule.clause_reference,
            formula_breakdown=formula_str,
            explanation=rule.explanation,
        )

    def calculate_all(
        self, rules: List[TariffRule], vessel: VesselProfile
    ) -> TariffCalculationResponse:
        """Executes all rules for a given vessel call and generates an itemized summary."""
        logger.info("engine.calculate_all.start", rule_count=len(rules))
        if not rules:
            raise InvalidTariffRuleError("No tariff rules were provided to calculate.")
        currencies = {rule.currency.strip().upper() for rule in rules}
        if len(currencies) > 1:
            raise InvalidTariffRuleError(
                f"Cannot total dues across mixed currencies: {sorted(currencies)}"
            )
        items: List[TariffItemBreakdown] = [
            self.calculate_item(rule, vessel) for rule in rules
        ]
        # Resolve percent_of surcharges: if a rule's component references
        # another category's amount, apply the percentage.
        base_amounts = {
            item.canonical_category: Decimal(str(item.calculated_value))
            for item in items
        }
        for idx, rule in enumerate(rules):
            for comp in rule.resolved_components():
                if comp.percent_of and comp.percent_of.upper() in base_amounts:
                    pct = Decimal(str(comp.rate)) / Decimal("100")
                    base_val = base_amounts[comp.percent_of.upper()]
                    surcharge = (base_val * pct).quantize(
                        Decimal("0.01"), rounding=ROUND_HALF_UP
                    )
                    old_val = Decimal(str(items[idx].calculated_value))
                    new_val = self.round_currency(old_val + surcharge)
                    items[idx] = items[idx].model_copy(
                        update={
                            "calculated_value": new_val,
                            "formula_breakdown": (
                                items[idx].formula_breakdown
                                + f"  [+{comp.rate:.2f}% of {comp.percent_of}: {surcharge}]"
                            ),
                        }
                    )

        total = self.round_currency(
            sum(Decimal(str(item.calculated_value)) for item in items)
        )
        doc_currency = next(iter(currencies))

        logger.info(
            "engine.total.computed",
            total=total,
            currency=doc_currency,
            item_count=len(items),
        )

        return TariffCalculationResponse(
            port=vessel.port_name,
            vessel_name=vessel.vessel_metadata.name,
            total_dues=total,
            currency=doc_currency,
            items=items,
        )
