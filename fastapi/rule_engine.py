import os
import json
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

try:
    import jsonschema  # type: ignore
    _JSONSCHEMA_AVAILABLE = True
except Exception:
    _JSONSCHEMA_AVAILABLE = False


@dataclass
class RuleResult:
    """
    Deterministic evaluation result.

    Notes:
      - mandatory=True means "release gate" at test-case level.
      - name should be stable to support dashboards/aggregation.
    """
    name: str
    passed: bool
    reason: Optional[str] = None
    mandatory: bool = True

    def as_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "passed": self.passed,
            "reason": self.reason,
            "mandatory": self.mandatory,
        }


def _get_generated_text(case: Dict[str, Any]) -> str:
    gt = case.get("generated_text")
    if isinstance(gt, dict):
        t = gt.get("text")
        return t.strip() if isinstance(t, str) else ""
    if isinstance(gt, str):
        return gt.strip()
    return ""


def _get_by_path(d: Dict[str, Any], path: Sequence[str]) -> Any:
    cur: Any = d
    for p in path:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(p)
        if cur is None:
            return None
    return cur


def _normalize_number(s: Any) -> str:
    return re.sub(r"[^\d]", "", str(s))


def _month_age(now_utc: datetime, iso_dt: str) -> Optional[int]:
    # Accept ISO8601, allow trailing "Z"
    s = iso_dt.strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(s)
    except Exception:
        return None
    # naive handling: compare year/month
    return (now_utc.year - dt.year) * 12 + (now_utc.month - dt.month)


class RuleEngine:
    """
    Deterministic rule checks (no LLM).

    Covers (spec-aligned):
      - schema/key presence
      - format compliance (length / bullets / JSON)
      - accuracy safeguards (company-name mismatch, important-field mismatch hooks)
      - assertion risk (strong definitive phrases)
      - data freshness (last_updated + avoid strong "now/latest" claims when stale)
      - fairness/bias (rule-based patterns)
      - legal compliance (hiring-law risk phrases)
    """

    COMPANY_NAME_PATTERNS = [
        r"[一-龥ぁ-んァ-ンA-Za-z0-9ー・]+株式会社",
        r"[一-龥ぁ-んァ-ンA-Za-z0-9ー・]+有限会社",
        r"[一-龥ぁ-んァ-ンA-Za-z0-9ー・]+合同会社",
    ]

    DEFAULT_DEFINITIVE_WORDS = [
        "現在",
        "最新",
        "必ず",
        "必須",
        "確実に",
        "必然的",
        "間違いなく",
        "絶対",
        "確定",
        "保証",
        "100%",
    ]

    DEFAULT_BIAS_PATTERNS = [
        r"男性.*(向いている|有利|優れている|採用|歓迎)",
        r"女性.*(向いている|有利|優れている|採用|歓迎)",
        r"(有名大学|名門大学).*優秀",
        r"地方大学.*(不利|劣る)",
        r"(高卒|大卒|院卒).*だけ(採用|可)",
        r"(外国人|国籍).*不可",
        r"(障害者|障がい者).*不可",
    ]

    # Hiring-domain legal risk phrases (heuristic)
    DEFAULT_LEGAL_RISK_PATTERNS = [
        r"年齢制限",
        r"\b\d{1,2}歳(以下|未満|以上|超)\b",
        r"既婚(者)?(歓迎|不可|限定)",
        r"未婚(者)?(歓迎|不可|限定)",
        r"妊娠(中)?(歓迎|不可|限定)",
        r"出身地.*(不利|不可|限定)",
        r"国籍.*(不問|限定|不可)",
        r"性別.*(不問|限定|不可)",
    ]

    def __init__(
        self,
        *,
        max_output_len: Optional[int] = None,
        require_last_updated: Optional[bool] = None,
        freshness_months: Optional[int] = None,
        require_user_input: Optional[bool] = None,
        allow_definitive_words: Optional[bool] = None,
        important_field_checker: Optional[Any] = None,
    ):
        # Test-case schema requirements
        self.required_fields = ["talent", "company", "generated_text"]

        self.max_output_len = max_output_len if max_output_len is not None else int(os.getenv("MAX_OUTPUT_LEN", "200"))
        # Spec: context should include last_updated. Default to required.
        if require_last_updated is None:
            require_last_updated = os.getenv("REQUIRE_LAST_UPDATED", "1") == "1"
        self.require_last_updated = require_last_updated

        if freshness_months is None:
            freshness_months = int(os.getenv("FRESHNESS_MONTHS", "12"))
        self.freshness_months = freshness_months

        if require_user_input is None:
            require_user_input = os.getenv("REQUIRE_USER_INPUT", "0") == "1"
        self.require_user_input = require_user_input

        if allow_definitive_words is None:
            allow_definitive_words = os.getenv("ALLOW_DEFINITIVE_WORDS", "0") == "1"
        self.important_field_checker = important_field_checker

        self.allow_definitive_words = allow_definitive_words

        # "Accuracy" important numeric keys (configurable per case)
        self.default_important_numeric_paths: List[Tuple[str, ...]] = [
            ("company", "basic", "employee_count"),
            ("company", "basic", "annual_revenue"),
        ]

        self.definitive_words = self.DEFAULT_DEFINITIVE_WORDS
        self.bias_patterns = self.DEFAULT_BIAS_PATTERNS
        self.legal_risk_patterns = self.DEFAULT_LEGAL_RISK_PATTERNS

    def check_case(self, case: Dict[str, Any]) -> List[RuleResult]:
        results: List[RuleResult] = []

        # ---- Schema / required keys
        if not isinstance(case, dict):
            return [RuleResult(name="schema_top_level_object", passed=False, reason="top-level not object", mandatory=True)]

        for f in self.required_fields:
            ok = f in case
            results.append(RuleResult(name=f"schema_required_field_{f}", passed=ok, reason=None if ok else f"{f} missing", mandatory=True))

        # user_input is part of the "ideal" testcase, but may be omitted in some datasets
        if self.require_user_input:
            ok = "user_input" in case and isinstance(case.get("user_input"), str) and case.get("user_input", "").strip()
            results.append(RuleResult(name="schema_required_field_user_input", passed=ok, reason=None if ok else "user_input missing/empty", mandatory=True))
        else:
            ok = "user_input" in case
            results.append(RuleResult(name="schema_user_input_present", passed=True, reason=None if ok else "user_input missing (warning)", mandatory=False))

        gen_text = _get_generated_text(case)
        if not gen_text:
            results.append(RuleResult(name="schema_generated_text_text", passed=False, reason="generated_text.text missing/empty", mandatory=True))
            return results  # cannot proceed meaningfully

        results.append(RuleResult(name="schema_basic", passed=True, mandatory=True))

        # ---- Format compliance (mandatory)
        if self.max_output_len and len(gen_text) > self.max_output_len:
            results.append(
                RuleResult(
                    name="format_max_length",
                    passed=False,
                    reason=f"length={len(gen_text)} > {self.max_output_len}",
                    mandatory=True,
                )
            )
        else:
            results.append(
                RuleResult(
                    name="format_max_length",
                    passed=True,
                    reason=f"length={len(gen_text)}",
                    mandatory=True,
                )
            )

        fmt = (case.get("output_format") or "").lower().strip()
        if fmt:
            results.extend(self._check_output_format(gen_text, case, fmt))

        # ---- Assertion risk (mandatory by default)
        if not self.allow_definitive_words and not case.get("allow_definitive_words", False):
            found = [w for w in self.definitive_words if w in gen_text]
            if found:
                results.append(
                    RuleResult(
                        name="assertion_definitive_words",
                        passed=False,
                        reason=f"definitive words present: {found}",
                        mandatory=True,
                    )
                )
            else:
                results.append(RuleResult(name="assertion_definitive_words", passed=True, mandatory=True))
        else:
            results.append(RuleResult(name="assertion_definitive_words", passed=True, reason="definitive words allowed", mandatory=False))

        # ---- Data freshness (△ but can be mandatory by config)
        freshness_required = bool(case.get("freshness_required", self.require_last_updated))
        last_updated = (
            _get_by_path(case, ("company", "basic", "last_updated"))
            or _get_by_path(case, ("talent", "last_updated"))
            or _get_by_path(case, ("last_updated",))
        )

        if freshness_required and not last_updated:
            results.append(RuleResult(name="freshness_last_updated_present", passed=False, reason="last_updated missing", mandatory=True))
        else:
            results.append(
                RuleResult(
                    name="freshness_last_updated_present",
                    passed=True,
                    reason=None if last_updated else "last_updated missing (not required)",
                    mandatory=bool(freshness_required),
                )
            )

        # If last_updated exists, apply stale-check
        if last_updated:
            age_m = _month_age(datetime.utcnow(), str(last_updated))
            if age_m is None:
                results.append(RuleResult(name="freshness_last_updated_parse", passed=False, reason=f"invalid last_updated: {last_updated}", mandatory=bool(freshness_required)))
            else:
                results.append(RuleResult(name="freshness_age_months", passed=True, reason=f"age_months={age_m}", mandatory=False))
                if age_m > self.freshness_months:
                    # Stale data: strong "now/latest" claims are disallowed
                    stale_claim_words = ["現在", "最新", "今", "必ず", "間違いなく", "成長中", "急成長"]
                    found = [w for w in stale_claim_words if w in gen_text]
                    if found:
                        results.append(
                            RuleResult(
                                name="freshness_stale_definitive_claim",
                                passed=False,
                                reason=f"data stale ({age_m} months) but strong-now claim present: {found}",
                                mandatory=True if freshness_required else False,
                            )
                        )
                    else:
                        results.append(
                            RuleResult(
                                name="freshness_stale_definitive_claim",
                                passed=True,
                                reason=f"data stale ({age_m} months) and no strong-now claims",
                                mandatory=False,
                            )
                        )
                else:
                    results.append(
                        RuleResult(
                            name="freshness_stale_definitive_claim",
                            passed=True,
                            reason=f"fresh (age_months={age_m})",
                            mandatory=bool(freshness_required),
                        )
                    )

        # ---- Accuracy hooks
        results.extend(self._check_company_name_mismatch(gen_text, case))
        results.extend(self._check_important_numeric_values(gen_text, case))

        # ---- Fairness/Bias (mandatory)
        for rx in self.bias_patterns:
            if re.search(rx, gen_text):
                results.append(
                    RuleResult(
                        name="fairness_bias_pattern",
                        passed=False,
                        reason=f"matched bias pattern: {rx}",
                        mandatory=True,
                    )
                )
        if not any(r.name == "fairness_bias_pattern" and not r.passed for r in results):
            results.append(RuleResult(name="fairness_bias_pattern", passed=True, mandatory=True))

        # ---- Legal compliance (mandatory)
        for rx in self.legal_risk_patterns:
            if re.search(rx, gen_text):
                results.append(
                    RuleResult(
                        name="legal_risk_phrase",
                        passed=False,
                        reason=f"matched legal risk: {rx}",
                        mandatory=True,
                    )
                )
        if not any(r.name == "legal_risk_phrase" and not r.passed for r in results):
            results.append(RuleResult(name="legal_risk_phrase", passed=True, mandatory=True))

        return results

    def _check_output_format(self, gen_text: str, case: Dict[str, Any], fmt: str) -> List[RuleResult]:
        results: List[RuleResult] = []
        if fmt == "json":
            try:
                parsed = json.loads(gen_text)
                results.append(RuleResult(name="format_json_parse", passed=True, mandatory=True))
                schema = case.get("output_json_schema")
                if schema is not None:
                    if not _JSONSCHEMA_AVAILABLE:
                        results.append(
                            RuleResult(
                                name="format_json_schema",
                                passed=False,
                                reason="jsonschema not installed but output_json_schema provided",
                                mandatory=True,
                            )
                        )
                    else:
                        try:
                            jsonschema.validate(parsed, schema)  # type: ignore
                            results.append(RuleResult(name="format_json_schema", passed=True, mandatory=True))
                        except Exception as e:
                            results.append(RuleResult(name="format_json_schema", passed=False, reason=str(e), mandatory=True))
            except Exception as e:
                results.append(RuleResult(name="format_json_parse", passed=False, reason=str(e), mandatory=True))
        elif fmt in ("bullets", "bullet", "list"):
            lines = [ln.strip() for ln in gen_text.splitlines() if ln.strip()]
            if not lines:
                results.append(RuleResult(name="format_bullets", passed=False, reason="empty output", mandatory=True))
                return results
            bullet_prefixes = ("-", "・", "*", "•")
            ok = all(any(ln.startswith(bp) for bp in bullet_prefixes) for ln in lines)
            results.append(RuleResult(name="format_bullets", passed=ok, reason=None if ok else "non-bullet line detected", mandatory=True))
        else:
            # Unknown format tag: treat as warning, do not gate release
            results.append(RuleResult(name="format_unknown_spec", passed=True, reason=f"unknown output_format={fmt}", mandatory=False))
        # Project-specific important field checks (external spec)
        if self.important_field_checker is not None:
            try:
                results.extend(self.important_field_checker.run(case))
            except Exception as e:
                results.append(RuleResult(
                    name="important_fields_runtime_error",
                    passed=False,
                    reason=str(e),
                    mandatory=True,
                ))

        return results

    def _check_company_name_mismatch(self, gen_text: str, case: Dict[str, Any]) -> List[RuleResult]:
        results: List[RuleResult] = []
        expected = _get_by_path(case, ("company", "basic", "name"))
        expected = str(expected).strip() if expected else ""
        if not expected:
            return [RuleResult(name="accuracy_company_name_mismatch", passed=True, reason="company.basic.name missing (skip)", mandatory=False)]

        found: List[str] = []
        for rx in self.COMPANY_NAME_PATTERNS:
            found += re.findall(rx, gen_text)

        # If output mentions a company-like name and it's not the expected one, fail.
        mismatches = [nm for nm in found if nm != expected]
        if mismatches:
            results.append(
                RuleResult(
                    name="accuracy_company_name_mismatch",
                    passed=False,
                    reason=f"company name(s) in output not matching context: {mismatches} (expected={expected})",
                    mandatory=True,
                )
            )
        else:
            results.append(RuleResult(name="accuracy_company_name_mismatch", passed=True, mandatory=True))
        return results

    def _check_important_numeric_values(self, gen_text: str, case: Dict[str, Any]) -> List[RuleResult]:
        """
        Accuracy: important numeric fields must not be contradicted.

        This rule is conservative:
          - If the value is mentioned, it must match (exact digits match after normalization).
          - If not mentioned, we do not fail (presence is not required unless case asks).
        """
        results: List[RuleResult] = []
        important_paths: List[Tuple[str, ...]] = []
        raw_paths = case.get("important_fields")
        if isinstance(raw_paths, list) and all(isinstance(x, str) for x in raw_paths):
            for p in raw_paths:
                important_paths.append(tuple(p.split(".")))
        else:
            important_paths = list(self.default_important_numeric_paths)

        out_numbers = {_normalize_number(x) for x in re.findall(r"\d[\d,\.]*", gen_text)}
        out_numbers.discard("")

        for path in important_paths:
            ctx_v = _get_by_path(case, path)
            if ctx_v is None:
                continue
            ctx_num = _normalize_number(ctx_v)
            if not ctx_num:
                continue

            must_mention = False
            must_map = case.get("must_mention_fields") or {}
            if isinstance(must_map, dict):
                must_mention = bool(must_map.get(".".join(path), False))

            if ctx_num in out_numbers:
                results.append(RuleResult(name=f"accuracy_numeric_match_{'.'.join(path)}", passed=True, mandatory=True))
            else:
                # Not mentioned
                if must_mention:
                    results.append(
                        RuleResult(
                            name=f"accuracy_numeric_match_{'.'.join(path)}",
                            passed=False,
                            reason=f"important field must be mentioned but not found: {'.'.join(path)}={ctx_v}",
                            mandatory=True,
                        )
                    )
                else:
                    results.append(
                        RuleResult(
                            name=f"accuracy_numeric_match_{'.'.join(path)}",
                            passed=True,
                            reason="not mentioned (allowed)",
                            mandatory=False,
                        )
                    )

        return results
