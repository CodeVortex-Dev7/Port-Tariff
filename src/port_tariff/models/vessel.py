from datetime import datetime
from enum import Enum
import math
from typing import List, Optional
from pydantic import BaseModel, ConfigDict, Field, computed_field


class VesselMetadata(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str
    built_year: Optional[int] = None
    flag: Optional[str] = None
    classification_society: Optional[str] = None
    call_sign: Optional[str] = None


class TechnicalSpecs(BaseModel):
    model_config = ConfigDict(extra="ignore")

    imo_number: Optional[str] = None
    type: str
    dwt: float
    gross_tonnage: float
    net_tonnage: Optional[float] = None
    loa_meters: float
    beam_meters: float
    moulded_depth_meters: Optional[float] = None
    lbp_meters: Optional[float] = None
    draft_sw_s_w_t: Optional[List[float]] = None
    suez_gt: Optional[float] = None
    suez_nt: Optional[float] = None


class StayBasis(str, Enum):
    """How the tariff document defines the billable time period."""
    ALONGSIDE = "ALONGSIDE"
    ARRIVAL_TO_DEPARTURE = "ARRIVAL_TO_DEPARTURE"
    COMMENCED_24H = "COMMENCED_24H"


class OperationalData(BaseModel):
    model_config = ConfigDict(extra="ignore")

    cargo_quantity_mt: Optional[float] = None
    days_alongside: Optional[float] = None
    arrival_time: Optional[datetime] = None
    departure_time: Optional[datetime] = None
    activity: Optional[str] = None
    num_operations: int = Field(
        default=2, description="Number of moves (usually 2: inward + outward)"
    )
    num_holds: Optional[int] = None
    stay_basis: StayBasis = Field(
        default=StayBasis.ALONGSIDE,
        description=(
            "How the tariff defines billable time. ALONGSIDE uses days_alongside; "
            "ARRIVAL_TO_DEPARTURE uses the full arrival-to-departure span; "
            "COMMENCED_24H rounds up to whole 24h periods."
        ),
    )




class VesselProfile(BaseModel):
    model_config = ConfigDict(extra="ignore")

    port_name: str = Field(
        default="",
        description="Target port of call; may stay empty only for single-port tariff documents",
    )
    vessel_metadata: VesselMetadata
    technical_specs: TechnicalSpecs
    operational_data: OperationalData

    @computed_field
    @property
    def gt_100_units(self) -> int:
        """Units of 100 gross tons or part thereof: ceil(GT / 100)."""
        return math.ceil(self.technical_specs.gross_tonnage / 100.0)

    @computed_field
    @property
    def billable_stay_days(self) -> float:
        """Returns the billable stay duration in days, respecting stay_basis.

        - ALONGSIDE: uses days_alongside if provided, else arrival-to-departure.
        - ARRIVAL_TO_DEPARTURE: always uses arrival-to-departure span.
        - COMMENCED_24H: rounds the arrival-to-departure span up to whole days.
        """
        basis = self.operational_data.stay_basis
        if basis in (StayBasis.ARRIVAL_TO_DEPARTURE, StayBasis.COMMENCED_24H):
            if (
                self.operational_data.arrival_time
                and self.operational_data.departure_time
            ):
                delta = (
                    self.operational_data.departure_time
                    - self.operational_data.arrival_time
                )
                days = delta.total_seconds() / 86400.0
                if basis == StayBasis.COMMENCED_24H:
                    return float(math.ceil(days))
                return days
            if self.operational_data.days_alongside is not None:
                return float(self.operational_data.days_alongside)
            return 1.0
        # ALONGSIDE (default)
        if self.operational_data.days_alongside is not None:
            return float(self.operational_data.days_alongside)
        if (
            self.operational_data.arrival_time
            and self.operational_data.departure_time
        ):
            delta = (
                self.operational_data.departure_time
                - self.operational_data.arrival_time
            )
            return delta.total_seconds() / 86400.0
        return 1.0
