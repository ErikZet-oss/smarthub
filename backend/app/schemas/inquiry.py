from __future__ import annotations

from typing import Optional

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator


class InquiryLineAIOutput(BaseModel):
    """Schéma pre Gemini structured output (bez raw_text)."""

    model_config = ConfigDict(populate_by_name=True)

    diameter: Optional[str] = None
    length: Optional[str] = None
    norm: Optional[str] = None
    class_: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("class", "class_"),
        serialization_alias="class",
    )
    leading_standard: Optional[str] = None
    material: Optional[str] = None
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
    """Parsovaný riadok dopytu — AI + manuálne opravy."""

    model_config = ConfigDict(populate_by_name=True)

    row_index: int = 0
    raw_text: str = ""
    diameter: Optional[str] = None
    length: Optional[str] = None
    norm: Optional[str] = None
    class_: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("class", "class_"),
        serialization_alias="class",
    )
    leading_standard: Optional[str] = None
    material: Optional[str] = None
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
            norm=_clean(ai.norm),
            class_=_clean(ai.class_),
            leading_standard=_clean(ai.leading_standard),
            material=_clean(ai.material),
            quantity=qty,
        )

    def missing_required_fields(self) -> list[str]:
        missing: list[str] = []
        if not (self.diameter or "").strip():
            missing.append("diameter")
        if not (self.length or "").strip():
            missing.append("length")
        if not (self.norm or "").strip():
            missing.append("norm")
        if not (self.class_ or "").strip():
            missing.append("class")
        if self.quantity is None or self.quantity <= 0:
            missing.append("quantity")
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
