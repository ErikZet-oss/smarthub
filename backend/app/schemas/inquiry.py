from __future__ import annotations

from typing import Optional

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator

from app.services.inquiry.norm_rules import inquiry_required_field_names


class InquiryLineAIOutput(BaseModel):
    """Schéma pre Gemini structured output — polia zladené s vyhľadávaním."""

    model_config = ConfigDict(populate_by_name=True)

    diameter: Optional[str] = None
    length: Optional[str] = None
    norma: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("norma", "norm", "leading_standard"),
    )
    v_class: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("v_class", "class", "class_", "product_class"),
        serialization_alias="v_class",
    )
    surface: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("surface", "material"),
        serialization_alias="surface",
    )
    quantity: Optional[int] = None

    @field_validator("quantity", mode="before")
    @classmethod
    def _quantity_int(cls, value: object) -> Optional[int]:
        if value is None or value == "":
            return None
        try:
            q = int(float(str(value).replace(",", ".").strip()))
            return q if q > 0 else None
        except (TypeError, ValueError):
            return None


class InquiryLineParsed(BaseModel):
    """Parsovaný riadok dopytu — rovnaké filtre ako vyhľadávanie produktov."""

    model_config = ConfigDict(populate_by_name=True)

    row_index: int = 0
    raw_text: str = ""
    diameter: Optional[str] = None
    length: Optional[str] = None
    norma: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("norma", "norm", "leading_standard"),
        serialization_alias="norma",
    )
    v_class: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("v_class", "class", "class_"),
        serialization_alias="v_class",
    )
    surface: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("surface", "material"),
        serialization_alias="surface",
    )
    quantity: Optional[int] = None
    parse_error: Optional[str] = None

    @classmethod
    def from_ai(cls, row_index: int, raw_text: str, ai: InquiryLineAIOutput) -> InquiryLineParsed:
        qty = ai.quantity if ai.quantity is not None else 1
        return cls(
            row_index=row_index,
            raw_text=raw_text,
            diameter=_clean(ai.diameter),
            length=_clean(ai.length),
            norma=_clean(ai.norma),
            v_class=_clean(ai.v_class),
            surface=_clean(ai.surface),
            quantity=qty,
        )

    def missing_required_fields(self) -> list[str]:
        missing: list[str] = []
        for field in inquiry_required_field_names(self.norma, self.raw_text):
            val = getattr(self, field, None)
            if field == "quantity":
                if val is None or val <= 0:
                    missing.append(field)
            elif not str(val or "").strip():
                missing.append(field)
        return missing

    @property
    def is_valid(self) -> bool:
        return not self.missing_required_fields() and not self.parse_error


def _clean(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    s = str(value).strip()
    return s or None


class InquiryInputRow(BaseModel):
    row_index: int
    raw_text: str
    quantity_hint: Optional[int] = None


class InquiryParseTaskResult(BaseModel):
    rows: list[InquiryLineParsed]
    source_filename: str
    total_rows: int
