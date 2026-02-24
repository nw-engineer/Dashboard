import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple, Union

from .rule_engine import RuleResult, _get_generated_text


def _nfkc(s: str) -> str:
    return unicodedata.normalize("NFKC", s)


def _normalize_text(s: str, *, nfkc: bool = True, lower: bool = False, strip: bool = True) -> str:
    if nfkc:
        s = _nfkc(s)
    if strip:
        s = s.strip()
    if lower:
        s = s.lower()
    return s


def _tokenize_path(path: str) -> List[str]:
    return [p for p in path.split(".") if p]


def _extract_values(obj: Any, path: str) -> List[Any]:
    """
    Extract values from dict/list using a small dot-path syntax.

    Supported segments:
      - "a.b.c"
      - "items[]" or "items[*]" : iterate a list
      - "items[0]" : index

    Returns flattened list of leaf values (can include non-str scalars).
    """
    tokens = _tokenize_path(path)

    def walk(cur: Any, i: int) -> Iterable[Any]:
        if i >= len(tokens):
            yield cur
            return
        tok = tokens[i]

        m_idx = re.match(r"^(.+)\[(\d+)\]$", tok)
        if tok.endswith("[]") or tok.endswith("[*]"):
            key = tok.split("[", 1)[0]
            nxt = cur.get(key) if isinstance(cur, dict) else None
            if isinstance(nxt, list):
                for it in nxt:
                    yield from walk(it, i + 1)
            return

        if m_idx:
            key = m_idx.group(1)
            idx = int(m_idx.group(2))
            nxt = cur.get(key) if isinstance(cur, dict) else None
            if isinstance(nxt, list) and 0 <= idx < len(nxt):
                yield from walk(nxt[idx], i + 1)
            return

        # plain dict key
        nxt = cur.get(tok) if isinstance(cur, dict) else None
        if nxt is None:
            return
        yield from walk(nxt, i + 1)

    return list(walk(obj, 0))


def _as_str_list(values: Sequence[Any]) -> List[str]:
    out: List[str] = []
    for v in values:
        if v is None:
            continue
        if isinstance(v, (str, int, float, bool)):
            out.append(str(v))
            continue
        # ignore dict/list/object
    return out


@dataclass
class ImportantFieldCheck:
    """
    One rule from an external spec.

    - source:
        "context" -> values are extracted from the test-case JSON via `path`
        "literal" -> terms are taken from `terms`
    - mode:
        "must_mention_all"  : every term must appear in output text
        "must_mention_any"  : at least one term must appear
        "must_not_mention"  : no term must appear
        "regex_must_match"  : regex must match output (pattern required)
        "regex_must_not_match": regex must NOT match output
    """
    name: str
    mandatory: bool = True
    source: str = "context"
    path: Optional[str] = None
    terms: Optional[List[str]] = None
    mode: str = "must_mention_all"
    min_count: int = 1
    pattern: Optional[str] = None
    normalize_nfkc: bool = True
    normalize_lower: bool = False


class ImportantFieldChecker:
    def __init__(self, checks: List[ImportantFieldCheck]):
        self.checks = checks

    @staticmethod
    def from_json_file(path: Union[str, Path]) -> "ImportantFieldChecker":
        p = Path(path).expanduser()
        import json
        data = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or "checks" not in data or not isinstance(data["checks"], list):
            raise RuntimeError("important_fields spec must be an object with 'checks': [ ... ]")

        checks: List[ImportantFieldCheck] = []
        for raw in data["checks"]:
            if not isinstance(raw, dict) or "name" not in raw:
                continue
            checks.append(ImportantFieldCheck(
                name=str(raw["name"]),
                mandatory=bool(raw.get("mandatory", True)),
                source=str(raw.get("source", "context")),
                path=raw.get("path"),
                terms=raw.get("terms"),
                mode=str(raw.get("mode", "must_mention_all")),
                min_count=int(raw.get("min_count", 1)),
                pattern=raw.get("pattern"),
                normalize_nfkc=bool(raw.get("normalize_nfkc", True)),
                normalize_lower=bool(raw.get("normalize_lower", False)),
            ))
        return ImportantFieldChecker(checks)

    def run(self, case: Dict[str, Any]) -> List[RuleResult]:
        """
        Compare output text against important fields (project-defined).
        Deterministic by design; no LLM usage.
        """
        out_text = _get_generated_text(case)
        results: List[RuleResult] = []

        for c in self.checks:
            norm_out = _normalize_text(out_text, nfkc=c.normalize_nfkc, lower=c.normalize_lower)

            terms: List[str] = []
            if c.source == "literal":
                terms = list(c.terms or [])
            else:
                if not c.path:
                    results.append(RuleResult(
                        name=f"important_fields_{c.name}",
                        passed=False,
                        reason="missing 'path' in spec",
                        mandatory=c.mandatory
                    ))
                    continue
                vals = _as_str_list(_extract_values(case, c.path))
                terms = vals

            # normalize terms
            norm_terms = [_normalize_text(t, nfkc=c.normalize_nfkc, lower=c.normalize_lower) for t in terms if t is not None]
            norm_terms = [t for t in norm_terms if t != ""]

            if c.mode in ("regex_must_match", "regex_must_not_match"):
                if not c.pattern:
                    results.append(RuleResult(
                        name=f"important_fields_{c.name}",
                        passed=False,
                        reason="missing 'pattern' for regex mode",
                        mandatory=c.mandatory
                    ))
                    continue
                ok = re.search(c.pattern, norm_out) is not None
                passed = ok if c.mode == "regex_must_match" else (not ok)
                results.append(RuleResult(
                    name=f"important_fields_{c.name}",
                    passed=passed,
                    reason=None if passed else f"regex check failed: mode={c.mode}",
                    mandatory=c.mandatory
                ))
                continue

            if not norm_terms:
                # If nothing is specified/extracted, treat as PASS unless min_count enforces it
                if c.mode.startswith("must_mention") and c.min_count > 0:
                    results.append(RuleResult(
                        name=f"important_fields_{c.name}",
                        passed=False,
                        reason=f"no terms extracted (path={c.path})",
                        mandatory=c.mandatory
                    ))
                else:
                    results.append(RuleResult(
                        name=f"important_fields_{c.name}",
                        passed=True,
                        reason="no terms to check",
                        mandatory=c.mandatory
                    ))
                continue

            hits = [t for t in norm_terms if t and (t in norm_out)]
            if c.mode == "must_mention_all":
                missing = [t for t in norm_terms if t not in norm_out]
                passed = (len(missing) == 0) and (len(hits) >= c.min_count)
                reason = None if passed else f"missing_terms={missing[:10]}"
            elif c.mode == "must_mention_any":
                passed = len(hits) >= max(1, c.min_count)
                reason = None if passed else f"no required term found (need>={c.min_count})"
            elif c.mode == "must_not_mention":
                passed = len(hits) == 0
                reason = None if passed else f"forbidden_terms_found={hits[:10]}"
            else:
                passed = False
                reason = f"unknown mode={c.mode}"

            results.append(RuleResult(
                name=f"important_fields_{c.name}",
                passed=passed,
                reason=reason,
                mandatory=c.mandatory
            ))

        return results
