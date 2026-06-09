from __future__ import annotations

import json
import logging
import os
import re
from typing import Callable

from app.schemas.inquiry import InquiryLineAIOutput, InquiryLineParsed
from app.services.inquiry.normalize import apply_normalization

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """Si parser dopytov na spojovací materiál (skrutky, matice, podložky).
Z voľného textu extrahuj štruktúrované polia. Ak hodnotu nevieš určiť, použi null.
quantity: ak nie je uvedené, daj 1.
Pre matice bez dĺžky nech length je null.

Príklady:
- "skrutka M10x50 DIN933 8.8 pozinkovaná" → diameter M10, length 50, norm DIN933, product_class 8.8, material pozinkovaná
- "Šesťhranná matica DIN 934 Oceľ Pozinkované M3" → diameter M3, norm DIN934, material pozinkované, length null
- "6x matica M8 DIN934 8" → diameter M8, norm DIN934, product_class 8, quantity 6
"""

_JSON_KEYS_HINT = (
    "Vráť JSON s kľúčmi: diameter, length, norm, product_class, leading_standard, material, quantity. "
    "Použi null pre neznáme hodnoty."
)

_cached_model = None
_cached_model_key: str | None = None


def _build_user_prompt(raw_text: str) -> str:
    return f"Text položky dopytu:\n{raw_text.strip()}\n\n{_JSON_KEYS_HINT}"


def _gemini_inquiry_response_schema() -> dict[str, object]:
    """Schéma bez polí, ktoré Gemini API nepodporuje (title, class, …)."""
    return {
        "type": "object",
        "properties": {
            "diameter": {"type": "string"},
            "length": {"type": "string"},
            "norm": {"type": "string"},
            "product_class": {"type": "string"},
            "leading_standard": {"type": "string"},
            "material": {"type": "string"},
            "quantity": {"type": "integer"},
        },
    }


def _model_cache_key() -> str:
    api_key = (os.environ.get("GEMINI_API_KEY") or "").strip()
    model_name = (os.environ.get("GEMINI_MODEL") or "gemini-2.0-flash").strip()
    return f"{model_name}:{api_key[:8] if api_key else ''}"


def _gemini_model(*, use_schema: bool = True):
    global _cached_model, _cached_model_key

    api_key = (os.environ.get("GEMINI_API_KEY") or "").strip()
    if not api_key:
        return None

    cache_suffix = f":schema={use_schema}"
    key = _model_cache_key() + cache_suffix
    if _cached_model is not None and _cached_model_key == key:
        return _cached_model

    try:
        import google.generativeai as genai
    except ImportError:
        logger.warning("google-generativeai nie je nainštalované")
        return None

    genai.configure(api_key=api_key)
    model_name = (os.environ.get("GEMINI_MODEL") or "gemini-2.0-flash").strip()
    generation_config: dict[str, object] = {
        "temperature": 0.1,
        "response_mime_type": "application/json",
    }
    if use_schema:
        generation_config["response_schema"] = _gemini_inquiry_response_schema()

    try:
        model = genai.GenerativeModel(
            model_name,
            generation_config=generation_config,
            system_instruction=_SYSTEM_PROMPT,
        )
    except Exception as exc:
        logger.warning("Gemini model init zlyhal (schema=%s): %s", use_schema, exc)
        if use_schema:
            return _gemini_model(use_schema=False)
        return None

    _cached_model = model
    _cached_model_key = key
    return model


def _response_text(response) -> str:
    text = (getattr(response, "text", None) or "").strip()
    if text:
        return text
    candidates = getattr(response, "candidates", None) or []
    if not candidates:
        feedback = getattr(response, "prompt_feedback", None)
        if feedback and getattr(feedback, "block_reason", None):
            raise ValueError(f"Gemini zablokovalo odpoveď: {feedback.block_reason}")
        raise ValueError("Gemini nevrátilo kandidátov odpovede.")
    parts = candidates[0].content.parts
    return "".join(getattr(p, "text", "") or "" for p in parts).strip()


def _normalize_ai_payload(payload: dict[str, object]) -> dict[str, object]:
    """Mapovanie kľúčov z API na Pydantic (class → product_class)."""
    out = dict(payload)
    if "class" in out and "product_class" not in out:
        out["product_class"] = out.pop("class")
    if "class_" in out and "product_class" not in out:
        out["product_class"] = out.pop("class_")
    return out


def _parse_ai_response_text(text: str) -> InquiryLineAIOutput:
    payload = json.loads(text)
    if not isinstance(payload, dict):
        raise ValueError("AI odpoveď nie je JSON objekt.")
    return InquiryLineAIOutput.model_validate(_normalize_ai_payload(payload))


def _heuristic_parse(raw_text: str) -> InquiryLineAIOutput | None:
    """Záložné parsovanie regexom, ak Gemini zlyhá."""
    t = raw_text.strip()
    if not t:
        return None

    diameter = None
    length = None
    m = re.search(
        r"\bM\s*(\d+(?:[,.]\d+)?)\s*[x×]\s*(\d+(?:[,.]\d+)?)\b",
        t,
        re.IGNORECASE,
    )
    if m:
        diameter = f"M{m.group(1).replace(',', '.')}"
        length = m.group(2).replace(",", ".")
    else:
        m = re.search(r"\bM\s*(\d+(?:[,.]\d+)?)\b", t, re.IGNORECASE)
        if m:
            diameter = f"M{m.group(1).replace(',', '.')}"

    if length is None:
        m = re.search(r"\b(\d+(?:[,.]\d+)?)\s*mm\b", t, re.IGNORECASE)
        if m:
            length = m.group(1).replace(",", ".")

    norm = None
    m = re.search(r"\bDIN\s*[-]?\s*(\d+)\b", t, re.IGNORECASE)
    if m:
        norm = f"DIN{m.group(1)}"

    product_class = None
    m = re.search(r"\bA[24]\b", t, re.IGNORECASE)
    if m:
        product_class = m.group(0).upper()
    else:
        norm_digits = ""
        if norm:
            m_norm = re.search(r"\d+", norm)
            if m_norm:
                norm_digits = m_norm.group(0)
        for m in re.finditer(r"\b(\d+(?:[,.]\d+)?)\b", t):
            val = m.group(1).replace(",", ".")
            if norm_digits and val in norm_digits:
                continue
            product_class = val
            break

    material = None
    low = t.casefold()
    for key, label in (
        ("pozink", "pozinkované"),
        ("nerez", "nerez"),
        ("ocel", "oceľ"),
        ("mosadz", "mosadz"),
        ("a2", "A2"),
        ("a4", "A4"),
    ):
        if key in low:
            material = label
            break

    qty = 1
    m = re.match(r"^\s*(\d+)\s*[x×]\s*", t, re.IGNORECASE)
    if m:
        qty = int(m.group(1))

    if not any([diameter, norm, length, product_class, material]):
        return None

    return InquiryLineAIOutput(
        diameter=diameter,
        length=length,
        norm=norm,
        class_=product_class,
        material=material,
        quantity=qty,
    )


def _call_gemini(model, text: str) -> InquiryLineAIOutput:
    response = model.generate_content(_build_user_prompt(text))
    raw_json = _response_text(response)
    return _parse_ai_response_text(raw_json)


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
    last_error: str | None = None

    if gemini is not None:
        for attempt in range(2):
            try:
                ai = _call_gemini(gemini, text)
                parsed = InquiryLineParsed.from_ai(row_index, text, ai)
                if quantity_hint is not None and quantity_hint > 0:
                    parsed.quantity = quantity_hint
                return apply_normalization(parsed)
            except Exception as exc:
                last_error = str(exc)
                logger.warning("Gemini parse attempt %s zlyhal: %s", attempt + 1, exc)
    else:
        last_error = "GEMINI_API_KEY nie je nastavený na serveri."

    heuristic = _heuristic_parse(text)
    if heuristic is not None:
        parsed = InquiryLineParsed.from_ai(row_index, text, heuristic)
        if quantity_hint is not None and quantity_hint > 0:
            parsed.quantity = quantity_hint
        parsed.parse_error = None
        return apply_normalization(parsed)

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
    shared_model = model if model is not None else _gemini_model()
    total = len(rows)
    out: list[InquiryLineParsed] = []
    for i, (row_index, raw_text, qty_hint) in enumerate(rows, start=1):
        out.append(
            parse_inquiry_line(
                raw_text,
                row_index=row_index,
                quantity_hint=qty_hint,
                model=shared_model,
            )
        )
        if progress_cb is not None:
            progress_cb(i, total)
    return out
