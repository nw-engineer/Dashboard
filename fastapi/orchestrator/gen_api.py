from __future__ import annotations

import json
from typing import Any, Dict, Iterable, List, Optional

import requests


_DEFAULT_DROP_KEYS = {
    "generated_text",
    "regen_texts",
    "dimensions",
    "rule_results",
    "context_results",
    "consistency_results",
    "final_status",
    "fail_reasons",
    "metadata",
}


def build_payload_for_gen_api(case: Dict[str, Any], *, include_keys: Optional[List[str]] = None) -> Dict[str, Any]:
    """Build request payload for a generation endpoint.

    Precedence:
      1) case["gen_api_payload"] if provided
      2) If include_keys is provided and non-empty, copy only those keys (if present)
      3) Otherwise, copy the whole case except output/eval artifacts

    This keeps the orchestrator flexible for multiple endpoints.
    """
    explicit = case.get("gen_api_payload")
    if isinstance(explicit, dict):
        return explicit

    if include_keys:
        payload: Dict[str, Any] = {}
        for k in include_keys:
            if k in case:
                payload[k] = case[k]
        # if include_keys yields nothing, fall back to full payload
        if payload:
            for drop in _DEFAULT_DROP_KEYS:
                payload.pop(drop, None)
            return payload

    payload = dict(case)
    for drop in _DEFAULT_DROP_KEYS:
        payload.pop(drop, None)
    return payload


def extract_text_from_gen_response(resp: requests.Response, *, response_text_keys: Optional[List[str]] = None) -> str:
    """Extract generated text from a response.

    Supports:
      - JSON dict with a string at one of response_text_keys
      - JSON dict with keys: offer_recommendation, gen_lab_company_description, generated_text, text, output, result
      - JSON dict where generated_text is {"text": "..."}
      - plain text
    """
    try:
        data = resp.json()
    except Exception:
        return (resp.text or "").strip()

    if isinstance(data, dict):
        # 1) user-configured keys first
        if response_text_keys:
            for k in response_text_keys:
                v = data.get(k)
                if isinstance(v, str) and v.strip():
                    return v.strip()
                if isinstance(v, dict):
                    t = v.get("text")
                    if isinstance(t, str) and t.strip():
                        return t.strip()

        # 2) common defaults
        for key in (
            "offer_recommendation",
            "gen_lab_company_description",
            "generated_text",
            "text",
            "output",
            "result",
        ):
            v = data.get(key)
            if isinstance(v, str) and v.strip():
                return v.strip()
            if isinstance(v, dict):
                t = v.get("text")
                if isinstance(t, str) and t.strip():
                    return t.strip()

    return (resp.text or "").strip()
