import os
import re
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

import requests

try:
    from sentence_transformers import SentenceTransformer, util  # type: ignore
    S_EMBED_AVAILABLE = True
except Exception:
    S_EMBED_AVAILABLE = False

from .rule_engine import RuleResult, _get_generated_text


def _unique_keep_order(items: List[str]) -> List[str]:
    out: List[str] = []
    seen: Set[str] = set()
    for x in items:
        if x not in seen:
            out.append(x)
            seen.add(x)
    return out


def _norm_number_list(text: str) -> Set[str]:
    nums = re.findall(r"\d[\d,\.]*", text)
    out = {re.sub(r"[^\d]", "", n) for n in nums}
    out.discard("")
    return out


class ConsistencyEngine:
    """
    Consistency checks (same input/context regenerated multiple times).

    Spec intent:
      1) Prefer "important field" consistency before embeddings.
      2) Embedding similarity is supplementary (default threshold 0.92).
      3) Expression variance is allowed, but fact changes are not.

    Implementation:
      - Build variants: [generated_text] + regen_texts (and optionally HTTP regen if GEN_API_URL set)
      - Deterministic: important_numbers set + important_term presence pattern must match across variants
      - Supplement: embedding similarity >= threshold (when available)
      - Output includes a mandatory "consistency_final" RuleResult
    """

    def __init__(
        self,
        regen_endpoint: Optional[str] = None,
        n_samples: int = 3,
        embedding_model_name: Optional[str] = None,
        sim_threshold: float = 0.92,
        require_embedding: Optional[bool] = None,
    ):
        env_url = os.getenv("GEN_API_URL")
        self.regen_endpoint = (regen_endpoint or env_url or "").strip() or None
        self.n = n_samples
        self.sim_threshold = sim_threshold

        if embedding_model_name is None:
            embedding_model_name = os.getenv("EMBEDDING_MODEL", "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2")
        self.embedding_model_name = embedding_model_name

        if require_embedding is None:
            require_embedding = os.getenv("REQUIRE_EMBEDDING", "0") == "1"
        self.require_embedding = require_embedding

        self.embed_model = None
        if S_EMBED_AVAILABLE:
            try:
                self.embed_model = SentenceTransformer(self.embedding_model_name)
            except Exception:
                self.embed_model = None

    def check(self, case: Dict[str, Any]) -> List[RuleResult]:
        results: List[RuleResult] = []
        variants = self._collect_variants(case, results)

        if not variants:
            return [RuleResult(name="consistency_no_texts", passed=False, reason="no generated_text/regen_texts available", mandatory=True)]

        # Use first text as base
        base = variants[0]

        # ---- Deterministic important fields
        base_nums = _norm_number_list(base)
        num_ok = True
        for i, t in enumerate(variants[1:], start=1):
            other_nums = _norm_number_list(t)
            if other_nums != base_nums:
                num_ok = False
                results.append(
                    RuleResult(
                        name="consistency_numbers_mismatch",
                        passed=False,
                        reason=f"variant#{i} nums {sorted(other_nums)} != base {sorted(base_nums)}",
                        mandatory=True,
                    )
                )
        if num_ok:
            results.append(RuleResult(name="consistency_numbers_mismatch", passed=True, mandatory=True))

        # ---- Deterministic important term presence pattern
        terms = self._derive_terms(case)
        term_ok = True
        base_presence = {term: (term in base) for term in terms}

        for i, t in enumerate(variants[1:], start=1):
            for term, present in base_presence.items():
                other_present = term in t
                if other_present != present:
                    term_ok = False
                    results.append(
                        RuleResult(
                            name="consistency_term_presence_mismatch",
                            passed=False,
                            reason=f"variant#{i} term='{term}' present={other_present} != base_present={present}",
                            mandatory=True,
                        )
                    )
        if term_ok:
            results.append(RuleResult(name="consistency_term_presence_mismatch", passed=True, mandatory=True))

        # ---- Embedding similarity (supplement)
        emb_ok = True
        emb_reason = None
        if self.embed_model and len(variants) >= 2:
            try:
                emb = self.embed_model.encode(variants, convert_to_tensor=True)
                sims: List[float] = []
                for i in range(len(variants)):
                    for j in range(i + 1, len(variants)):
                        sims.append(float(util.cos_sim(emb[i], emb[j]).item()))
                avg_sim = sum(sims) / len(sims) if sims else 1.0
                emb_ok = avg_sim >= self.sim_threshold
                results.append(
                    RuleResult(
                        name="embedding_sim_avg",
                        passed=emb_ok,
                        reason=f"avg={avg_sim}",
                        mandatory=bool(self.require_embedding),
                    )
                )
            except Exception as e:
                emb_ok = False
                emb_reason = str(e)
                results.append(RuleResult(name="embedding_sim_error", passed=False, reason=str(e), mandatory=bool(self.require_embedding)))
        else:
            # Embedding not available
            results.append(
                RuleResult(
                    name="embedding_sim_skipped",
                    passed=not self.require_embedding,
                    reason="sentence-transformers unavailable or not enough samples",
                    mandatory=bool(self.require_embedding),
                )
            )
            emb_ok = not self.require_embedding

        final_ok = num_ok and term_ok and emb_ok
        results.append(
            RuleResult(
                name="consistency_final",
                passed=final_ok,
                reason=None if final_ok else "see consistency_numbers/term_presence/embedding checks",
                mandatory=True,
            )
        )

        return results

    def _collect_variants(self, case: Dict[str, Any], results: List[RuleResult]) -> List[str]:
        """
        Build texts to compare for consistency.

        Preferred behavior (when GEN_API_URL is available):
          - Generate variants by calling the generation API (same input/context), up to n_samples.
          - Ignore offline `case["regen_texts"]` by default to avoid mixing stale/pre-baked variants.

        Backward compatible behavior:
          - If GEN_API_URL is NOT set, fall back to `case["regen_texts"]` (offline).
          - If you really want to also use offline regen_texts even when GEN_API_URL exists,
            set env ALLOW_OFFLINE_REGEN_TEXTS=1.
        """
        texts: List[str] = []

        # base text (often created by a prior generation step)
        base = _get_generated_text(case)
        if base:
            texts.append(base)

        allow_offline = os.getenv("ALLOW_OFFLINE_REGEN_TEXTS", "0") == "1"

        # offline regen texts (only when API is not available, unless explicitly allowed)
        if allow_offline or (not self.regen_endpoint):
            regen_texts = case.get("regen_texts", [])
            if isinstance(regen_texts, list):
                for t in regen_texts:
                    if isinstance(t, str) and t.strip():
                        texts.append(t.strip())

        texts = _unique_keep_order(texts)

        # If API is available, generate remaining samples via HTTP
        need = max(0, self.n - len(texts))
        if need > 0 and self.regen_endpoint:
            payload = self._build_regen_payload(case)
            timeout_s = int(os.getenv("GEN_API_TIMEOUT", "30"))
            for _ in range(need):
                try:
                    resp = requests.post(self.regen_endpoint, json=payload, timeout=timeout_s)
                    resp.raise_for_status()

                    # try JSON first; fall back to raw text
                    txt = self._extract_text_from_response(resp)
                    if isinstance(txt, str) and txt.strip():
                        texts.append(txt.strip())
                    else:
                        results.append(
                            RuleResult(
                                name="regen_empty",
                                passed=False,
                                reason="regen endpoint returned empty text",
                                mandatory=False,
                            )
                        )
                except Exception as e:
                    results.append(RuleResult(name="regen_failed", passed=False, reason=str(e), mandatory=False))

            texts = _unique_keep_order(texts)
            results.append(
                RuleResult(
                    name="regen_generated_count",
                    passed=True,
                    reason=f"generated={max(0, len(texts)- (1 if base else 0))} total={len(texts)}",
                    mandatory=False,
                )
            )
        elif need > 0 and not self.regen_endpoint:
            results.append(
                RuleResult(
                    name="regen_skipped",
                    passed=True,
                    reason="GEN_API_URL not set; using regen_texts from JSON only",
                    mandatory=False,
                )
            )

        return _unique_keep_order(texts)

    def _build_regen_payload(self, case: Dict[str, Any]) -> Dict[str, Any]:
        """
        Build a payload for generation API calls.

        We intentionally remove output artifacts (generated_text/regen_texts/etc.)
        so the API always regenerates from the same input/context.
        """
        # If the test case provides an explicit payload, trust it.
        explicit = case.get("gen_api_payload")
        if isinstance(explicit, dict):
            return explicit

        payload: Dict[str, Any] = {}

        # Prefer known/expected keys (safe default for structured-data injection style)
        for k in (
            "user_input",
            "talent",
            "company",
            "system_prompt",
            "prompt",
            "messages",
            "output_format",
            "output_json_schema",
            "max_length",
            "temperature",
            "model",
            "arch_version",
            "prompt_version",
        ):
            if k in case:
                payload[k] = case[k]

        # Fallback: include everything except output/eval artifacts
        if not payload:
            payload = dict(case)

        for drop in (
            "generated_text",
            "regen_texts",
            "dimensions",
            "rule_results",
            "context_results",
            "consistency_results",
            "final_status",
            "fail_reasons",
            "metadata",
        ):
            payload.pop(drop, None)

        return payload

    def _extract_text_from_response(self, resp: requests.Response) -> str:
        """
        Extract generated text from API response.

        Supported response shapes:
          - JSON dict with keys: offer_recommendation / generated_text / text / output
          - JSON dict where generated_text is { "text": "..." }
          - Plain text body
        """
        try:
            data = resp.json()
        except Exception:
            return (resp.text or "").strip()

        if isinstance(data, dict):
            for key in ("offer_recommendation", "generated_text", "text", "output"):
                v = data.get(key)
                if isinstance(v, str) and v.strip():
                    return v.strip()
                if isinstance(v, dict):
                    t = v.get("text")
                    if isinstance(t, str) and t.strip():
                        return t.strip()

        # some APIs may return {"result": "..."}
        if isinstance(data, dict):
            v = data.get("result")
            if isinstance(v, str) and v.strip():
                return v.strip()

        return (resp.text or "").strip()

    def _derive_terms(self, case: Dict[str, Any]) -> List[str]:
        """
        Terms for 'important field' presence consistency.

        Priority:
          1) case["consistency_terms"] (explicit)
          2) derived from injected structured fields (company name + key skills keywords)
        """
        explicit = case.get("consistency_terms")
        terms: List[str] = []
        if isinstance(explicit, list) and all(isinstance(x, str) for x in explicit):
            terms = [t.strip() for t in explicit if t.strip()]
            return _unique_keep_order(terms)

        # Derive from context
        company_name = (((case.get("company") or {}).get("basic") or {}).get("name") or "")
        if isinstance(company_name, str) and company_name.strip():
            terms.append(company_name.strip())

        # Talent technical keywords (comma separated)
        tk = ((case.get("talent") or {}).get("technical_keywords") or "")
        if isinstance(tk, str):
            for part in re.split(r"[,\s]+", tk):
                p = part.strip()
                if p:
                    terms.append(p)

        # Common skills tokens (software / it skills)
        software_other = (((case.get("talent") or {}).get("skills_experiences") or {}).get("software_skill_other") or "")
        if isinstance(software_other, str) and software_other.strip():
            terms.append(software_other.strip())

        it_skills = (((case.get("talent") or {}).get("skills_experiences") or {}).get("it_skills") or [])
        if isinstance(it_skills, list):
            for sk in it_skills:
                if isinstance(sk, dict):
                    nm = sk.get("skill_name")
                    if isinstance(nm, str) and nm.strip():
                        terms.append(nm.strip())

        # De-duplicate and cap
        terms = _unique_keep_order(terms)
        return terms[:25]
