from typing import List

from pydantic import BaseModel, ConfigDict, Field, field_validator

from port_tariff.models.tariff import slugify_category


class ChargeEntry(BaseModel):
    """One distinct charge discovered in a tariff document."""

    model_config = ConfigDict(extra="ignore")

    category: str = Field(
        ...,
        description=(
            "UPPER_SNAKE_CASE identifier derived from the charge's name in the "
            "document, e.g. SEAPORT_DUES"
        ),
    )
    display_name: str = Field(
        ..., description="The charge's name as written in the document"
    )
    aliases: List[str] = Field(
        default_factory=list,
        description="Other names the document uses for this charge",
    )
    description: str = Field(
        ..., description="One sentence on what is charged and for which service"
    )
    applies_when: str = Field(
        ...,
        description=(
            "Conditions under which it is levied: per call, per movement or per "
            "day; vessel types or activities concerned"
        ),
    )
    section_refs: List[str] = Field(
        ...,
        description=(
            "Clause numbers (e.g. 3.3) or exact heading texts of the sections "
            "that define this charge's rules and rates"
        ),
    )
    search_queries: List[str] = Field(
        ...,
        description=(
            "2-4 short retrieval queries, in the document's own wording, that "
            "would find this charge's rate table"
        ),
    )

    @field_validator("category")
    @classmethod
    def _normalize_category(cls, v: str) -> str:
        slug = slugify_category(v)
        if not slug:
            raise ValueError("category must not be empty")
        return slug


class ChargeCatalog(BaseModel):
    """Every vessel charge found in a tariff document, plus document-level context."""

    model_config = ConfigDict(extra="ignore")

    currency: str = Field(
        ..., description="ISO 4217 code of the currency the document states its rates in"
    )
    rate_selection_notes: str = Field(
        ...,
        description=(
            "How rates vary in this document: per port column with a fallback "
            "column, per vessel type, per tonnage band, or not at all"
        ),
    )
    charges: List[ChargeEntry]
