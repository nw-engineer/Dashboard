import json
import os
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set


def _norm_text(s: str, *, nfkc: bool, lower: bool) -> str:
    out = s
    if nfkc:
        out = unicodedata.normalize("NFKC", out)
    if lower:
        out = out.lower()
    return out


def extract_values_by_path(obj: Any, path: str) -> List[Any]:
    """Extract values using a tiny subset of JSONPath-like syntax.

    Supported:
      - dot paths: a.b.c
      - list wildcard: a.b[].c  (equivalent to iterating list at b)

    Returns a list of 0..N values.
    """
    parts = path.split(".") if path else []
    curs: List[Any] = [obj]
    for part in parts:
        is_list = part.endswith("[]")
        key = part[:-2] if is_list else part
        nxt: List[Any] = []
        for cur in curs:
            if not isinstance(cur, dict):
                continue
            v = cur.get(key)
            if v is None:
                continue
            if is_list:
                if isinstance(v, list):
                    nxt.extend(v)
            else:
                nxt.append(v)
        curs = nxt
    return curs


@dataclass
class ImportantFieldsSpec:
    """Project-specific rules for important field extraction.

    This is intentionally simple and deterministic.
    """

    consistency_term_paths: List[str]
    must_mention_term_paths: List[str]
    must_mention_mode: str
    must_mention_min_count: int
    must_mention_mandatory: bool
    split_regex: str
    normalize_nfkc: bool
    normalize_lower: bool
    max_terms: int

    @staticmethod
    def empty() -> "ImportantFieldsSpec":
        return ImportantFieldsSpec(
            consistency_term_paths=[],
            must_mention_term_paths=[],
            must_mention_mode="any",
            must_mention_min_count=1,
            must_mention_mandatory=False,
            split_regex=r"[,、\s]+",
            normalize_nfkc=True,
            normalize_lower=False,
            max_terms=25,
        )

    @staticmethod
    def load_from_file(path: str | None) -> "ImportantFieldsSpec":
        if not path:
            return ImportantFieldsSpec.empty()
        p = Path(path)
        if not p.exists():
            return ImportantFieldsSpec.empty()
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return ImportantFieldsSpec.empty()

        top = data if isinstance(data, dict) else {}

        c_cfg = top.get("consistency_terms")
        if not isinstance(c_cfg, dict):
            c_cfg = {}
        c_paths = c_cfg.get("paths")
        if not isinstance(c_paths, list) or not all(isinstance(x, str) for x in c_paths):
            c_paths = []

        m_cfg = top.get("must_mention_terms")
        if not isinstance(m_cfg, dict):
            m_cfg = {}
        m_paths = m_cfg.get("paths")
        if not isinstance(m_paths, list) or not all(isinstance(x, str) for x in m_paths):
            m_paths = []
        m_mode = str(m_cfg.get("mode") or "any").lower().strip()
        if m_mode not in ("any", "all"):
            m_mode = "any"
        m_min = int(m_cfg.get("min_count", 1))
        m_mandatory = bool(m_cfg.get("mandatory", False))

        split_regex = None
        if isinstance(m_cfg.get("split_regex"), str):
            split_regex = m_cfg.get("split_regex")
        elif isinstance(c_cfg.get("split_regex"), str):
            split_regex = c_cfg.get("split_regex")
        split_regex = split_regex or r"[,、\s]+"

        nfkc = bool((m_cfg.get("normalize_nfkc") if "normalize_nfkc" in m_cfg else c_cfg.get("normalize_nfkc")) if ("normalize_nfkc" in m_cfg or "normalize_nfkc" in c_cfg) else True)
        lower = bool((m_cfg.get("normalize_lower") if "normalize_lower" in m_cfg else c_cfg.get("normalize_lower")) if ("normalize_lower" in m_cfg or "normalize_lower" in c_cfg) else False)
        max_terms = int((c_cfg.get("max_terms") if "max_terms" in c_cfg else top.get("max_terms", 25)) or 25)

        return ImportantFieldsSpec(
            consistency_term_paths=c_paths,
            must_mention_term_paths=m_paths,
            must_mention_mode=m_mode,
            must_mention_min_count=m_min,
            must_mention_mandatory=m_mandatory,
            split_regex=split_regex,
            normalize_nfkc=nfkc,
            normalize_lower=lower,
            max_terms=max_terms,
        )

    @staticmethod
    def load_from_env() -> "ImportantFieldsSpec":
        return ImportantFieldsSpec.load_from_file(os.getenv("IMPORTANT_FIELDS_SPEC"))

    def derive_consistency_terms(self, case: Dict[str, Any]) -> List[str]:
        if not self.consistency_term_paths:
            return []
        terms: List[str] = []
        for p in self.consistency_term_paths:
            values = extract_values_by_path(case, p)
            for v in values:
                if v is None:
                    continue
                if isinstance(v, str):
                    parts = [x.strip() for x in re.split(self.split_regex, v) if x.strip()]
                    terms.extend(parts)
                elif isinstance(v, (int, float)):
                    terms.append(str(v))
                else:
                    # allow dicts that contain name/keyword-like fields
                    if isinstance(v, dict):
                        for k in ("name", "keyword", "keywords", "skill_name"):
                            vv = v.get(k)
                            if isinstance(vv, str) and vv.strip():
                                parts = [x.strip() for x in re.split(self.split_regex, vv) if x.strip()]
                                terms.extend(parts)

        # normalize + de-dup preserve order
        seen: Set[str] = set()
        out: List[str] = []
        for t in terms:
            nt = _norm_text(t, nfkc=self.normalize_nfkc, lower=self.normalize_lower)
            if not nt:
                continue
            if nt in seen:
                continue
            seen.add(nt)
            out.append(t.strip())
            if len(out) >= self.max_terms:
                break
        return out

    def derive_must_mention_terms(self, case: Dict[str, Any]) -> List[str]:
        if not self.must_mention_term_paths:
            return []
        terms: List[str] = []
        for p in self.must_mention_term_paths:
            for v in extract_values_by_path(case, p):
                if v is None:
                    continue
                if isinstance(v, str):
                    parts = [x.strip() for x in re.split(self.split_regex, v) if x.strip()]
                    terms.extend(parts)
                elif isinstance(v, dict):
                    for k in ("name", "keyword", "keywords", "skill_name"):
                        vv = v.get(k)
                        if isinstance(vv, str) and vv.strip():
                            parts = [x.strip() for x in re.split(self.split_regex, vv) if x.strip()]
                            terms.extend(parts)
                else:
                    if isinstance(v, (int, float)):
                        terms.append(str(v))

        # normalize + de-dup preserve order
        seen: Set[str] = set()
        out: List[str] = []
        for t in terms:
            nt = _norm_text(t, nfkc=self.normalize_nfkc, lower=self.normalize_lower)
            if not nt:
                continue
            if nt in seen:
                continue
            seen.add(nt)
            out.append(t.strip())
            if len(out) >= self.max_terms:
                break
        return out
