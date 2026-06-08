from __future__ import annotations

import json
import logging
import os
from typing import Callable

from app.schemas.inquiry import InquiryLineAIOutput, InquiryLineParsed
from app.services.inquiry.normalize import apply_normalization

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """Si parser dopytov na spojovací materiál (skrutky, matice, podložky).
Z voľného textu extrahuj štruktúrované polia. Ak hodnotu nevieš určiť, nastav null.
quantity: ak nie je uvedené, použij 1.

Príklady:
- "skrutka M10x50 DIN933 8.8 pozinkovaná" → diameter M10, length 50, norm DIN933, class 8.8, material pozinkovaná, quantity 1
- "6x matica M8 DIN934 8" → diameter M8, norm DIN934, class 8, quantity 6
- "M12 x 120 DIN 931 A2" → diameter M12, length 120, norm DIN931, class A2, material nerez
"""


def _build_user_prompt(raw_text: str) -> str:
    return f"Text položky dopytu:\n{raw_text.strip()}"


def _gemini_model():
    api_key = (os.environ.get("GEMINI_API_KEY") or "").strip()
    if not api_key:
        return None
    try:
        import google.generativeai as genai
    except ImportError:
        logger.warning("google-generativeai nie je nainštalované")
        return None

    genai.configure(api_key=api_key)
    model_name = (os.environ.get("GEMINI_MODEL") or "gemini-2.0-flash").strip()
    schema = InquiryLineAIOutput.model_json_schema()
    return genai.GenerativeModel(
        model_name,
        generation_config={
            "temperature": 0.1,
            "response_mime_type": "application/json",
            "response_schema": schema,
        },
        system_instruction=_SYSTEM_PROMPT,
    )


def _parse_ai_response_text(text: str) -> InquiryLineAIOutput:
    payload = json.loads(text)
    return InquiryLineAIOutput.model_validate(payload)


def parse_inquiry_line(
    raw_text: str,
    *,
    row_index: int = 0,
    quantity_hint: int | None = None,
    model=None,
) -> InquiryLineParsed:
    text = (raw_text or "").strip()
    if not text:
        return InquiryLineParsed(
            row_index=row_index,
            raw_text=raw_text or "",
            parse_error="Prázdna bunka",
        )

    gemini = model if model is not None else _gemini_model()
    if gemini is None:
        return InquiryLineParsed(
            row_index=row_index,
            raw_text=text,
            parse_error="GEMINI_API_KEY nie je nastavený na serveri.",
        )

    last_error: str | None = None
    for attempt in range(2):
        try:
            response = gemini.generate_content(_build_user_prompt(text))
            raw_json = (response.text or "").strip()
            if not raw_json and response.candidates:
                parts = response.candidates[0].content.parts
                raw_json = "".join(getattr(p, "text", "") or "" for p in parts).strip()
            ai = _parse_ai_response_text(raw_json)
            parsed = InquiryLineParsed.from_ai(row_index, text, ai)
            if quantity_hint is not None and quantity_hint > 0:
                parsed.quantity = quantity_hint
            return apply_normalization(parsed)
        except Exception as exc:
            last_error = str(exc)
            logger.warning("Gemini parse attempt %s zlyhal: %s", attempt + 1, exc)

    return InquiryLineParsed(
        row_index=row_index,
        raw_text=text,
        parse_error=last_error or "Parsovanie zlyhalo",
    )


def parse_inquiry_batch(
    rows: list[tuple[int, str, int | None]],
    *,
    progress_cb: Callable[[int, int], None] | None = None,
    model=None,
) -> list[InquiryLineParsed]:
    total = len(rows)
    out: list[InquiryLineParsed] = []
    for i, (row_index, raw_text, qty_hint) in enumerate(rows, start=1):
        out.append(
            parse_inquiry_line(
                raw_text,
                row_index=row_index,
                quantity_hint=qty_hint,
                model=model,
            )
        )
        if progress_cb is not None:
            progress_cb(i, total)
    return out
