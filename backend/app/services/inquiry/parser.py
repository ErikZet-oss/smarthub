from __future__ import annotations

import json
import logging
import os
import re
from typing import Callable

from app.schemas.inquiry import InquiryLineAIOutput, InquiryLineParsed
from app.services.inquiry.catalog_snap import (
    extract_explicit_v_class,
    infer_v_class_for_row,
    is_washer_text,
)
from app.services.inquiry.normalize import apply_normalization, infer_surface_from_text
from app.services.inquiry.norm_rules import (
    extract_pin_dimensions,
    extract_pin_tolerance_fit,
    extract_pin_catalog_norma,
    extract_snap_ring_diameter,
    is_pin_norm,
    is_snap_ring_norm,
    norm_requires_length,
)
from app.services.inquiry.product_norm_hints import (
    infer_norma_from_text,
    product_norm_hints_for_prompt,
)
from app.services.inquiry.stn_suffix import extract_stn_suffix, infer_material_from_stn_text
from app.services.inquiry.stn_to_din import extract_stn_base, stn_base_to_din, stn_to_din_prompt_section

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = f"""Si parser dopytov na spojovací materiál. Extrahuj polia zladené s katalógom SmartHub:

- norma: leading standard v tvare DIN pre katalóg SmartHub (napr. DIN933, DIN934, 934, DIN125)
  Ak je v texte STN/ČSN 02 xxxx alebo ISO, premapuj na ekvivalentný DIN (nie STN).
- surface: povrchová úprava / materiál (napr. Oceľ pozinkovaná, Nerez A2, Mosadz)
- diameter: priemer (M3, M10, …)
- length: dĺžka v mm — LEN pre skrutky, zvary a pod. s dĺžkou
- v_class: trieda pevnosti (8.8, 10.9, …) — typicky len pri skrutkách
- quantity: počet ks (predvolene 1)

Dôležité pravidlá:
- Matice (DIN 934, DIN 985, …), podložky (DIN 125, DIN 127, …) NEMAJÚ dĺžku → length = null
- Matice: v_class doplň LEN ak je v texte (8.8, 10.9) alebo z STN suffixu (.55, .05); bez toho → null
- Závitová tyč má normu DIN 976 (v katalógu aj „976“) a VŽDY má dĺžku (napr. M10x1000)
- Pri skrutke M10x50 je diameter M10 a length 50
- A2/A4 pri nerezi daj do surface (Nerez A2), nie do v_class
- Plast / Polyamid / Nylon (aj „6.6“ v Polyamid (nylon) 6.6): surface = Polyamid, v_class = 0 — „6.6“ NIE JE trieda pevnosti
- Ak hodnotu nevieš určiť, použi null

{product_norm_hints_for_prompt()}

{stn_to_din_prompt_section()}

Príklady:
- "skrutka M10x50 DIN933 8.8 pozinkovaná" → norma DIN933, diameter M10, length 50, v_class 8.8, surface Oceľ pozinkovaná
- "Šesťhranná matica DIN 934 Oceľ Pozinkované M3" → norma DIN934, diameter M3, surface Oceľ pozinkovaná, v_class null, length null
- "MATICA M 24 10.9 DIN 934 ZN" → norma DIN934, diameter M24, surface Oceľ pozinkovaná, v_class 10.9, length null
- "Šesťhranná matica DIN 934 Plast Polyamid (nylon) 6.6 M3" → norma DIN934, diameter M3, surface Polyamid, v_class 0, length null
- "6x matica M8 DIN934" → norma DIN934, diameter M8, quantity 6, length null
- "Závitová tyč M10x1000 pozinkovaná" → norma DIN976, diameter M10, length 1000, surface Oceľ pozinkovaná
- "SKRUTKA M10X100 STN 02 1103 A4" → norma DIN933, diameter M10, length 100, surface Nerez A4
- "MATICA M 12 STN 02 1401" → norma DIN934, diameter M12, length null, surface Oceľ
- "MATICA M 12 STN 02 1401.55" → norma DIN934, diameter M12, surface Oceľ pozinkovaná, v_class 8.8
- "PODLOZKA 10 A2 STN 02 1702" → norma DIN125, diameter 10, length null
"""

_JSON_KEYS_HINT = (
    "Vráť JSON s kľúčmi: norma, surface, diameter, length, v_class, quantity. "
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
            "norma": {"type": "string"},
            "surface": {"type": "string"},
            "diameter": {"type": "string"},
            "length": {"type": "string"},
            "v_class": {"type": "string"},
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
    """Mapovanie starších kľúčov z AI odpovede."""
    out = dict(payload)
    if "norm" in out and "norma" not in out:
        out["norma"] = out.pop("norm")
    if "leading_standard" in out and "norma" not in out:
        out["norma"] = out.pop("leading_standard")
    if "material" in out and "surface" not in out:
        out["surface"] = out.pop("material")
    if "product_class" in out and "v_class" not in out:
        out["v_class"] = out.pop("product_class")
    if "class" in out and "v_class" not in out:
        out["v_class"] = out.pop("class")
    if "class_" in out and "v_class" not in out:
        out["v_class"] = out.pop("class_")
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
    pin_dims = extract_pin_dimensions(t)
    if pin_dims:
        diameter, length = pin_dims
    if diameter is None and length is None:
        m = re.search(
            r"\bM\s*(\d+(?:[,.]\d+)?)\s*[x×]\s*(\d+(?:[,.]\d+)?)\b",
            t,
            re.IGNORECASE,
        )
        if m:
            diameter = m.group(1).replace(",", ".")
            length = m.group(2).replace(",", ".")
        else:
            m = re.search(
                r"\b(\d+(?:[,.]\d+)?)\s*[x×X]\s*(\d+(?:[,.]\d+)?)(?:\s*MM\b)?",
                t,
                re.IGNORECASE,
            )
            if m:
                diameter = m.group(1).replace(",", ".")
                length = m.group(2).replace(",", ".")
            else:
                m = re.search(r"\bM\s*(\d+(?:[,.]\d+)?)\b", t, re.IGNORECASE)
                if m:
                    prefix = t[max(0, m.start() - 14) : m.start()].casefold()
                    if "tolerancia" not in prefix:
                        diameter = m.group(1).replace(",", ".")

    if length is None:
        m = re.search(r"\b(\d+(?:[,.]\d+)?)\s*mm\b", t, re.IGNORECASE)
        if m:
            length = m.group(1).replace(",", ".")

    norma = None
    m = re.search(r"\bDIN\s*125\s*[-]?\s*1?\s*A\b", t, re.IGNORECASE)
    if m:
        norma = "DIN125"
    else:
        m = re.search(r"\bDIN\s*7979\s+D\b", t, re.IGNORECASE)
        if m:
            norma = "7979 D"
        else:
            m = re.search(r"\bDIN\s*[-]?\s*(\d+)\b", t, re.IGNORECASE)
            if m:
                norma = f"DIN{m.group(1)}"
    if norma is None:
        stn_base = extract_stn_base(t)
        if stn_base:
            norma = stn_base_to_din(stn_base)
    if norma is None:
        norma = infer_norma_from_text(t)

    if is_snap_ring_norm(norma, t):
        snap_d = extract_snap_ring_diameter(t)
        if snap_d:
            diameter = snap_d
    elif is_pin_norm(norma, t) or pin_dims:
        pin_d = extract_pin_dimensions(t)
        if pin_d:
            diameter, length = pin_d

    v_class = extract_explicit_v_class(t)
    if is_washer_text(t):
        surface_guess = infer_surface_from_text(t)
        if surface_guess is None:
            low = t.casefold()
            if "a4" in low and "nerez" in low:
                surface_guess = "Nerez A4"
            elif "a2" in low or "nerez" in low:
                surface_guess = "Nerez A2"
            elif "pozink" in low:
                surface_guess = "Oceľ pozinkovaná"
            elif "mosadz" in low:
                surface_guess = "Mosadz"
        v_class = infer_v_class_for_row(norma=norma, surface=surface_guess, raw_text=t)
    elif re.search(r"\bA[24]\b", t, re.IGNORECASE):
        v_class = None  # A2/A4 ide do surface
    elif infer_surface_from_text(t) == "Polyamid":
        v_class = "0"
    elif is_snap_ring_norm(norma, t):
        v_class = None
    elif is_pin_norm(norma, t):
        tol = extract_pin_tolerance_fit(t)
        v_class = tol
    elif not v_class:
        norm_digits = ""
        if norma:
            m_norm = re.search(r"\d+", norma)
            if m_norm:
                norm_digits = m_norm.group(0)
        stn_base = extract_stn_base(t)
        stn_match = extract_stn_suffix(t)
        for m in re.finditer(r"\b(\d+(?:[,.]\d+)?)\b", t):
            val = m.group(1).replace(",", ".")
            if norm_digits and val in norm_digits:
                continue
            if stn_base and val in {stn_base, stn_base.lstrip("0"), f"02{stn_base}"}:
                continue
            if stn_match:
                stn_full = f"{stn_match.base}.{stn_match.suffix}"
                if val in {stn_match.base, stn_full, stn_match.suffix}:
                    continue
            if re.search(r"\bSTN\s*0?\s*2\s", t, re.IGNORECASE) and val in {"02", "2"}:
                continue
            if diameter and val == diameter:
                continue
            if re.search(rf"\bM\s*{re.escape(val)}\b", t, re.IGNORECASE):
                continue
            if re.search(rf"\b{re.escape(val)}\s*±?\s*\d*\s*HRC\b", t, re.IGNORECASE):
                continue
            if re.search(rf"\b{re.escape(val)}\s*HV\b", t, re.IGNORECASE):
                continue
            if re.fullmatch(r"\d+(?:\.\d+)?", val):
                v_class = val
                break

    surface = infer_surface_from_text(t)
    stn_hint = infer_material_from_stn_text(t, norma=norma, is_washer=is_washer_text(t))
    if stn_hint:
        if stn_hint.surface:
            surface = stn_hint.surface
        if stn_hint.v_class:
            v_class = stn_hint.v_class
    elif not surface:
        low = t.casefold()
        if "a4" in low and "nerez" in low:
            surface = "Nerez A4"
        elif "a2" in low or "nerez" in low:
            surface = "Nerez A2"
        elif "pozink" in low:
            surface = "Oceľ pozinkovaná"
        elif "mosadz" in low:
            surface = "Mosadz"
        elif "ocel" in low or "oceľ" in low:
            surface = "Oceľ"

    if not v_class and surface and not is_pin_norm(norma, t):
        v_class = infer_v_class_for_row(norma=norma, surface=surface, raw_text=t)

    if not norm_requires_length(norma, t):
        length = None

    qty = 1
    m = re.match(r"^\s*(\d+)\s*[x×]\s*", t, re.IGNORECASE)
    if m:
        qty = int(m.group(1))

    if not any([diameter, norma, length, v_class, surface]):
        return None

    return InquiryLineAIOutput(
        diameter=diameter,
        length=length,
        norma=norma,
        v_class=v_class,
        surface=surface,
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
