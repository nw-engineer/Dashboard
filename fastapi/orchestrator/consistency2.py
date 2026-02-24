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
from .settings import OrchestratorSettings
from .gen_api import build_payload_for_gen_api, extract_text_from_gen_response


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
        settings: Optional[OrchestratorSettings] = None,
    ):
        self.settings = settings or OrchestratorSettings.from_env()

        env_url = self.settings.gen_api_url
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
            payload = build_payload_for_gen_api(case, include_keys=self.settings.gen_api_include_keys)
            timeout_s = self.settings.gen_api_timeout_s
            for _ in range(need):
                try:
                    resp = requests.post(self.regen_endpoint, json=payload, timeout=timeout_s)
                    resp.raise_for_status()

                    # try JSON first; fall back to raw text
                    txt = extract_text_from_gen_response(resp, response_text_keys=self.settings.gen_api_response_text_keys)
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

    def _derive_terms(self, case: Dict[str, Any]) -> List[str]:
        """
        Terms for 'important field' presence consistency.

        Priority:
          1) case["consistency_terms"] (explicit)
          2) derived from injected structured fields (company name + key skills keywords)
        """
        # 1) explicit per-case terms
        explicit = case.get("consistency_terms")
        if isinstance(explicit, list) and all(isinstance(x, str) for x in explicit):
            return _unique_keep_order([t.strip() for t in explicit if t.strip()])[:25]

        # 2) external spec-driven terms (project specific)
        terms = self.settings.important_spec.derive_consistency_terms(case)
        if terms:
            return terms[:25]

        # 3) legacy heuristic (kept for backward compatibility)
        legacy_terms: List[str] = []
        company_name = (((case.get("company") or {}).get("basic") or {}).get("name") or "")
        if isinstance(company_name, str) and company_name.strip():
            legacy_terms.append(company_name.strip())
        tk = ((case.get("talent") or {}).get("technical_keywords") or "")
        if isinstance(tk, str):
            for part in re.split(r"[,\s]+", tk):
                p = part.strip()
                if p:
                    legacy_terms.append(p)
        software_other = (((case.get("talent") or {}).get("skills_experiences") or {}).get("software_skill_other") or "")
        if isinstance(software_other, str) and software_other.strip():
            legacy_terms.append(software_other.strip())
        it_skills = (((case.get("talent") or {}).get("skills_experiences") or {}).get("it_skills") or [])
        if isinstance(it_skills, list):
            for sk in it_skills:
                if isinstance(sk, dict):
                    nm = sk.get("skill_name")
                    if isinstance(nm, str) and nm.strip():
                        legacy_terms.append(nm.strip())
        return _unique_keep_order(legacy_terms)[:25]
