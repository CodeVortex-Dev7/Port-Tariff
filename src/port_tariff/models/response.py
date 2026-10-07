from typing import List, Optional
from pydantic import BaseModel, ConfigDict, Field


class TariffItemBreakdown(BaseModel):
    model_config = ConfigDict(extra="ignore")

    canonical_category: str
    item_name: str
    source_tariff_item_name: Optional[str] = None
    calculated_value: float = Field(..., description="Rounded to 2 decimal places")
    currency: str
    clause_reference: str
    formula_breakdown: str = Field(
        ..., description="Human-readable arithmetic audit trail"
    )
    explanation: str


class TariffCalculationResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    port: str
    vessel_name: str
    total_dues: float = Field(..., description="Sum of all calculated dues")
    currency: str
    items: List[TariffItemBreakdown]
    warnings: List[str] = Field(
        default_factory=list,
        description="Charges found in the tariff that could not be priced for this call",
    )
