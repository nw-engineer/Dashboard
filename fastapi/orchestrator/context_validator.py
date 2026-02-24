import json
import re
from typing import Any, Dict, List, Optional, Set, Tuple

from .rule_engine import RuleResult, _get_generated_text


class ContextValidator:
    """
    Context-based validation for hallucination & leakage.

    Deterministic checks (spec-aligned):
      - Number hallucination: any number in output MUST exist in injected context (strict).
      - Risky keyword hallucination: keywords that imply extra conditions/benefits must exist in context.
      - "General knowledge" + definitive claim: disallow "一般的に/通常" combined with strong definitive words.
      - PII: block email/URL leakage (optional but treated as mandatory by default).
    """

    ENTITY_PATTERNS = {
        "EMAIL": r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+",
        "URL": r"https?://[^\s]+",
        "NUMBER": r"\b\d[\d,\.]*\b",
    }

    GENERAL_KNOWLEDGE_WORDS = ["一般的に", "通常", "たいてい", "多くの場合"]
    DEFINITIVE_WORDS = ["必ず", "絶対", "間違いなく", "確実に", "保証", "100%"]

    # Hiring-domain: keywords that tend to introduce extra facts if not injected
    DEFAULT_RISK_KEYWORDS = [
        "年収", "給与", "月給", "時給", "賞与", "ボーナス", "昇給",
        "福利厚生", "住宅手当", "交通費", "残業", "残業代", "休日", "有給",
        "リモート", "在宅", "フルリモート", "副業", "転勤なし", "勤務地確約",
        "キャンペーン", "割引", "特典", "入社祝い", "紹介料",
        "内定", "確約", "合格",
    ]

    def validate(self, case: Dict[str, Any]) -> List[RuleResult]:
        results: List[RuleResult] = []
        gen_text = _get_generated_text(case)

        if not gen_text:
            return [RuleResult(name="hallucination_no_generated_text", passed=False, reason="generated_text missing/empty", mandatory=True)]

        ctx_numbers, ctx_blob = self._collect_context_numbers_and_blob(case)

        # ---- PII (email/url)
        emails = re.findall(self.ENTITY_PATTERNS["EMAIL"], gen_text)
        if emails:
            results.append(RuleResult(name="pii_email_in_output", passed=False, reason=f"emails found: {emails}", mandatory=True))
        else:
            results.append(RuleResult(name="pii_email_in_output", passed=True, mandatory=True))

        urls = re.findall(self.ENTITY_PATTERNS["URL"], gen_text)
        if urls:
            results.append(RuleResult(name="pii_url_in_output", passed=False, reason=f"urls found: {urls}", mandatory=True))
        else:
            results.append(RuleResult(name="pii_url_in_output", passed=True, mandatory=True))

        # ---- Number hallucination (strict)
        out_numbers = re.findall(self.ENTITY_PATTERNS["NUMBER"], gen_text)
        hallucinated: List[str] = []
        for n in out_numbers:
            n_norm = self._norm_num(n)
            if n_norm and n_norm not in ctx_numbers:
                hallucinated.append(n)
                results.append(
                    RuleResult(
                        name="hallucination_number",
                        passed=False,
                        reason=f"number {n} not found in injected context",
                        mandatory=True,
                    )
                )

        if not hallucinated:
            results.append(RuleResult(name="hallucination_number", passed=True, mandatory=True))

        # ---- Risk keyword hallucination
        risk_keywords = case.get("risk_keywords")
        if not isinstance(risk_keywords, list) or not all(isinstance(x, str) for x in risk_keywords):
            risk_keywords = self.DEFAULT_RISK_KEYWORDS

        allowed_extra = set(case.get("allowed_output_keywords") or [])
        if not all(isinstance(x, str) for x in allowed_extra):
            allowed_extra = set()

        risky_hits: List[str] = []
        for kw in risk_keywords:
            if kw in allowed_extra:
                continue
            if kw in gen_text and kw not in ctx_blob:
                risky_hits.append(kw)
                results.append(
                    RuleResult(
                        name="hallucination_risky_keyword",
                        passed=False,
                        reason=f"keyword '{kw}' appears in output but not in injected context",
                        mandatory=True,
                    )
                )
        if not risky_hits:
            results.append(RuleResult(name="hallucination_risky_keyword", passed=True, mandatory=True))

        # ---- "general knowledge" + definitive claim
        if not case.get("allow_general_knowledge", False):
            has_general = any(w in gen_text for w in self.GENERAL_KNOWLEDGE_WORDS)
            has_def = any(w in gen_text for w in self.DEFINITIVE_WORDS)
            if has_general and has_def:
                results.append(
                    RuleResult(
                        name="hallucination_general_knowledge_definitive",
                        passed=False,
                        reason="contains general-knowledge filler + definitive claim",
                        mandatory=True,
                    )
                )
            else:
                results.append(RuleResult(name="hallucination_general_knowledge_definitive", passed=True, mandatory=True))
        else:
            results.append(RuleResult(name="hallucination_general_knowledge_definitive", passed=True, reason="general knowledge allowed", mandatory=False))

        return results

    def _collect_context_numbers_and_blob(self, case: Dict[str, Any]) -> Tuple[Set[str], str]:
        """
        Collect all numbers from injected context ONLY (exclude generated outputs),
        and a searchable blob string for simple membership tests.
        """
        ctx = dict(case)
        ctx.pop("generated_text", None)
        ctx.pop("regen_texts", None)

        blob = json.dumps(ctx, ensure_ascii=False, sort_keys=True)

        numbers = set()
        for m in re.findall(r"\d[\d,\.]*", blob):
            n = self._norm_num(m)
            if n:
                numbers.add(n)
        return numbers, blob

    def _norm_num(self, s: str) -> Optional[str]:
        s2 = re.sub(r"[^\d]", "", s)
        return s2 if s2 else None
