import re
from enum import Enum
from typing import List, Optional
from pydantic import BaseModel, ConfigDict, Field, field_validator

from port_tariff.exceptions import InvalidTariffRuleError


def slugify_category(value: str) -> str:
    """Normalizes a charge name into an UPPER_SNAKE_CASE identifier."""
    return re.sub(r"[^A-Za-z0-9]+", "_", value).strip("_").upper()


class QuantityBasis(str, Enum):
    """Vessel quantity a formula component is measured against."""

    FLAT = "FLAT"  # fixed amount: `rate` is the amount itself
    GT = "GT"
    NT = "NT"
    DWT = "DWT"
    LOA = "LOA"
    BEAM = "BEAM"
    CARGO_MT = "CARGO_MT"


class Multiplier(str, Enum):
    ONCE = "ONCE"  # charged once per call
    PER_MOVE = "PER_MOVE"  # multiplied by the rule's `moves`
    PER_DAY = "PER_DAY"  # multiplied by the billable stay days (pro rata)
    PER_HOUR = "PER_HOUR"  # multiplied by the billable stay hours


class FormulaComponent(BaseModel):
    """amount = rate * units * multiplier, with
    units = (quantity - threshold) / unit_size, rounded up when `round_up`."""

    model_config = ConfigDict(extra="ignore")

    basis: QuantityBasis
    rate: float = Field(
        ...,
        description=(
            "Amount per unit_size of the basis; the fixed amount itself when "
            "basis is FLAT"
        ),
    )
    unit_size: float = Field(
        default=1.0,
        description="Size of one billable unit, e.g. 100 for 'per 100 tons'",
    )
    round_up: bool = Field(
        default=True, description="Round units up ('or part thereof')"
    )
    round_days_up: bool = Field(
        default=False,
        description=(
            "When multiplier is PER_DAY or PER_HOUR, round the time factor "
            "up to the next whole unit (e.g. 'per commenced 24h period')"
        ),
    )
    percent_of: str = Field(
        default="",
        description=(
            "If non-empty, this component's 'rate' is a percentage applied to "
            "the named category's computed amount (e.g. 'PORT_DUES'). "
            "The engine resolves this after all base components are evaluated."
        ),
    )
    threshold: float = Field(
        default=0.0,
        description="Only the quantity above this lower bound is charged (tier excess)",
    )
    multiplier: Multiplier = Field(default=Multiplier.ONCE)


class FormulaType(str, Enum):
    # Fixed flat fee
    FLAT = "FLAT"

    # rate * Gross Tonnage
    PER_GT = "PER_GT"

    # rate * ceil(GT / 100)
    PER_100_GT = "PER_100_GT"

    # base_fee + (rate * ceil(GT / 100))
    BASE_PLUS_PER_100_GT = "BASE_PLUS_PER_100_GT"

    # base_fee + (rate * ceil((GT - excess_threshold) / 100))
    TIERED_BASE_PLUS_EXCESS_100_GT = "TIERED_BASE_PLUS_EXCESS_100_GT"

    # (basic_rate * ceil(GT/100)) + (daily_rate * ceil(GT/100) * days)
    PORT_DUES_BASIC_AND_DURATION = "PORT_DUES_BASIC_AND_DURATION"

    # rate * LOA meters
    PER_LOA_METER = "PER_LOA_METER"
    # rate * stay hours (with optional rounding)
    PER_HOUR_CHARGE = "PER_HOUR_CHARGE"


class TariffRule(BaseModel):
    model_config = ConfigDict(extra="ignore")

    canonical_category: str = Field(
        default="",
        description="Identifier of the catalog charge this rule prices (assigned by the pipeline; may be left empty)",
    )
    item_name: str = Field(
        default="",
        description="Name of the charge (assigned by the pipeline; may be left empty)",
    )

    @field_validator("canonical_category")
    @classmethod
    def _normalize_category(cls, v: str) -> str:
        return slugify_category(v)

    source_tariff_item_name: str = Field(
        ...,
        description="Verbatim title used in the document (e.g. 'Berthing Services', 'Zeehavengeld', 'Mooring')",
    )
    clause_reference: str = Field(
        ...,
        description="Exact clause/page in the tariff book, e.g. 'Clause 1.1.1, p. 9'",
    )
    formula_type: Optional[FormulaType] = Field(
        default=None,
        description="Preset formula shape when one fits exactly; otherwise null and describe the charge with `components`",
    )
    components: List[FormulaComponent] = Field(
        default_factory=list,
        description="Explicit formula components, summed. When present they take precedence over formula_type",
    )
    base_fee: float = Field(
        default=0.0, description="Fixed base charge per service/call"
    )
    rate: float = Field(
        default=0.0, description="Applicable rate per unit (e.g. per 100 GT)"
    )
    excess_threshold: float = Field(
        default=0.0,
        description="Lower bound threshold for tiered tonnage brackets (e.g. 50000 for 50,001-100,000 GT)",
    )
    stay_rate_per_day: float = Field(
        default=0.0,
        description="Daily incremental rate per 100 GT for port dues",
    )
    minimum_fee: Optional[float] = Field(
        default=None,
        description=(
            "Optional floor applied to the computed amount. Populated when the "
            "tariff specifies a minimum charge for this item; otherwise null."
        ),
    )
    maximum_fee: Optional[float] = Field(
        default=None,
        description=(
            "Optional ceiling applied to the computed amount. Populated when "
            "the tariff specifies a maximum charge for this item; otherwise null."
        ),
    )

    @field_validator("minimum_fee", "maximum_fee")
    @classmethod
    def _non_positive_bound_means_unset(cls, v: Optional[float]) -> Optional[float]:
        """LLMs often emit 0 for 'no minimum/maximum'; treat that as unset."""
        return v if v is not None and v > 0 else None
    moves: int = Field(
        default=1,
        description="Multiplier for movements/operations (e.g. 2 for inward + outward)",
    )
    currency: str = Field(
        default="",
        description="ISO currency code of the rates; the document currency is used when empty",
    )
    explanation: str = Field(
        ...,
        description="Step-by-step reasoning explaining how the port and bracket were matched",
    )

    def resolved_components(self) -> List[FormulaComponent]:
        """Expands the rule into evaluable components (explicit ones win over presets)."""
        if self.components:
            return list(self.components)

        move = Multiplier.PER_MOVE
        per_100_gt = {"basis": QuantityBasis.GT, "unit_size": 100.0}
        base = FormulaComponent(
            basis=QuantityBasis.FLAT, rate=self.base_fee, multiplier=move
        )
        match self.formula_type:
            case FormulaType.FLAT:
                return [base]
            case FormulaType.PER_GT:
                return [
                    FormulaComponent(
                        basis=QuantityBasis.GT,
                        rate=self.rate,
                        round_up=False,
                        multiplier=move,
                    )
                ]
            case FormulaType.PER_100_GT:
                return [FormulaComponent(**per_100_gt, rate=self.rate, multiplier=move)]
            case FormulaType.BASE_PLUS_PER_100_GT:
                return [
                    base,
                    FormulaComponent(**per_100_gt, rate=self.rate, multiplier=move),
                ]
            case FormulaType.TIERED_BASE_PLUS_EXCESS_100_GT:
                return [
                    base,
                    FormulaComponent(
                        **per_100_gt,
                        rate=self.rate,
                        threshold=self.excess_threshold,
                        multiplier=move,
                    ),
                ]
            case FormulaType.PORT_DUES_BASIC_AND_DURATION:
                return [
                    FormulaComponent(**per_100_gt, rate=self.rate),
                    FormulaComponent(
                        **per_100_gt,
                        rate=self.stay_rate_per_day,
                        multiplier=Multiplier.PER_DAY,
                    ),
                ]
            case FormulaType.PER_LOA_METER:
                return [
                    FormulaComponent(
                        basis=QuantityBasis.LOA,
                        rate=self.rate,
                        round_up=False,
                        multiplier=move,
                    )
                ]
            case FormulaType.PER_HOUR_CHARGE:
                return [
                    FormulaComponent(
                        basis=QuantityBasis.FLAT,
                        rate=self.rate,
                        multiplier=Multiplier.PER_HOUR,
                    )
                ]
        raise InvalidTariffRuleError(
            f"Tariff rule '{self.item_name}' defines neither a formula_type nor components."
        )


class ChargeExtractionResult(BaseModel):
    """LLM output of the per-charge pricing step."""

    model_config = ConfigDict(extra="ignore")

    applicable: bool = Field(
        ..., description="Whether this charge applies to the vessel call"
    )
    reason: str = Field(
        ..., description="Short justification of the applicability decision"
    )
    rules: List[TariffRule] = Field(
        default_factory=list,
        description="Pricing rule(s) for the charge; empty when not applicable",
    )


class TariffExtractionResult(BaseModel):
    model_config = ConfigDict(extra="ignore")

    port_name: str
    rules: List[TariffRule]
