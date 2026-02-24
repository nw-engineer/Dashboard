import json
import os
import subprocess
import sys
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .consistency2 import ConsistencyEngine
from .context_validator import ContextValidator
from .result_logger import ResultLogger
from .rule_engine import RuleEngine, RuleResult
from .settings import OrchestratorSettings
from .gen_api import build_payload_for_gen_api, extract_text_from_gen_response

import requests


DEEPEVAL_SCRIPT_DEFAULT = os.getenv("DEEPEVAL_SCRIPT", "test_judge_deepeval.py")


def _load_dataset(path: Path) -> List[Dict[str, Any]]:
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, dict):
        return [data]
    if isinstance(data, list) and all(isinstance(x, dict) for x in data):
        return data
    raise RuntimeError("Dataset JSON top-level must be an object or an array of objects")


def _now_utc_iso() -> str:
    return datetime.utcnow().isoformat() + "Z"


def _is_rule_result(x: Any) -> bool:
    return isinstance(x, RuleResult)


def _collect_mandatory_failures(results: List[RuleResult]) -> List[Dict[str, Any]]:
    fails = []
    for r in results:
        if r.mandatory and not r.passed:
            fails.append({"name": r.name, "reason": r.reason})
    return fails


def _dimension_summary(all_results: List[RuleResult]) -> Dict[str, str]:
    """
    Convert granular RuleResults into spec dimensions.
    Returns: {"accuracy": "PASS"/"FAIL", ...}
    """
    mapping = {
        "accuracy": lambda n: n.startswith("accuracy_"),
        "hallucination": lambda n: n.startswith("hallucination_"),
        "consistency": lambda n: n.startswith("consistency_") or n == "consistency_final",
        "format": lambda n: n.startswith("format_") or n.startswith("schema_"),
        "assertion_risk": lambda n: n.startswith("assertion_"),
        "freshness": lambda n: n.startswith("freshness_"),
        "fairness_bias": lambda n: n.startswith("fairness_"),
        "legal": lambda n: n.startswith("legal_"),
        "pii": lambda n: n.startswith("pii_"),
    }

    out: Dict[str, str] = {}
    for dim, pred in mapping.items():
        relevant = [r for r in all_results if pred(r.name)]
        if not relevant:
            continue
        out[dim] = "FAIL" if any(r.mandatory and not r.passed for r in relevant) else "PASS"
    return out


class Evaluator:
    """
    Evaluation Orchestrator (spec-aligned):

      - test-case level evaluation (PASS/FAIL)
      - batch evaluation (pass rate; release only if 100%)

    Engines:
      - RuleEngine (deterministic)
      - ContextValidator (deterministic hallucination)
      - ConsistencyEngine (deterministic + embedding supplement)
      - Optional: DeepEval LLM-as-judge (fairness/legality/etc.) via subprocess
    """

    def __init__(
        self,
        dataset_path: str,
        deepeval_script: str = DEEPEVAL_SCRIPT_DEFAULT,
        output_dir: str = "evaluation_results",
    ):
        self.dataset_path = Path(dataset_path)
        self.deepeval_script = str(deepeval_script)
        self.run_id = str(uuid.uuid4())

        self.settings = OrchestratorSettings.from_env()

        self.rule_engine = RuleEngine(
            required_fields_override=self.settings.required_fields or None,
            important_spec=self.settings.important_spec,
        )
        self.context_validator = ContextValidator()
        self.consistency_engine = ConsistencyEngine(settings=self.settings)
        self.logger = ResultLogger(output_dir=Path(output_dir))

        self.model_version = os.getenv("MODEL_VERSION") or os.getenv("OPENAI_MODEL") or None
        self.arch_version = os.getenv("ARCH_VERSION") or None
        self.prompt_version = os.getenv("PROMPT_VERSION") or None

        self.run_llm_judge = os.getenv("RUN_LLM_JUDGE", "1") == "1"
        self.require_llm_judge = os.getenv("REQUIRE_LLM_JUDGE", "0") == "1"

    def run_all(self) -> int:
        cases = _load_dataset(self.dataset_path)

        batch_cases: List[Dict[str, Any]] = []
        passed = 0
        failed = 0

        # ---- deterministic per-case eval
        for idx, case in enumerate(cases):
            case_key = str(case.get("case_id") or case.get("id") or idx)
            case_res = self._run_one_case(case, case_key=case_key, idx=idx)
            batch_cases.append(case_res)
            if case_res["final_status"] == "PASS":
                passed += 1
            else:
                failed += 1

        pass_rate = passed / max(1, (passed + failed))
        release_ok = (failed == 0)

        batch_result: Dict[str, Any] = {
            "metadata": {
                "run_id": self.run_id,
                "dataset_path": str(self.dataset_path),
                "timestamp": _now_utc_iso(),
                "model_version": self.model_version,
                "arch_version": self.arch_version,
                "prompt_version": self.prompt_version,
            },
            "batch": {
                "total": passed + failed,
                "passed": passed,
                "failed": failed,
                "pass_rate": pass_rate,
                "release_ok": release_ok,  # release criterion: 100%
            },
            "cases": batch_cases,
            "deepeval": None,
        }

        # ---- optional LLM-as-judge (batch)
        if self.run_llm_judge:
            deepeval_json = Path(self.logger.output_dir) / f"deepeval_{self.run_id}.json"
            exit_code, out = self._run_deepeval_subprocess(self.dataset_path, self.deepeval_script, deepeval_json)
            deepeval_payload: Dict[str, Any] = {
                "exit_code": exit_code,
                "output": out,
                "json_path": str(deepeval_json) if deepeval_json.exists() else None,
                "results": None,
            }
            if deepeval_json.exists():
                try:
                    deepeval_payload["results"] = json.loads(deepeval_json.read_text(encoding="utf-8"))
                except Exception as e:
                    deepeval_payload["results"] = {"error": f"failed to read deepeval json: {e}"}
            batch_result["deepeval"] = deepeval_payload

            if exit_code != 0 and self.require_llm_judge:
                batch_result["batch"]["release_ok"] = False
                batch_result["batch"]["failed"] = max(batch_result["batch"]["failed"], 1)  # ensure non-zero
                batch_result["batch"]["pass_rate"] = passed / max(1, (passed + failed))

        # Save batch summary
        self.logger.save_batch_result(run_id=self.run_id, batch_result=batch_result)

        # Exit codes
        if batch_result["batch"]["release_ok"]:
            return 0

        # Differentiate failure classes lightly
        if failed > 0:
            return 10  # deterministic gate failure
        if self.require_llm_judge:
            return 11  # LLM judge gate failure
        return 12

    def _run_one_case(self, case: Dict[str, Any], *, case_key: str, idx: int) -> Dict[str, Any]:
        metadata = {
            "run_id": self.run_id,
            "case_key": case_key,
            "case_index": idx,
            "timestamp": _now_utc_iso(),
            "model_version": self.model_version or case.get("model_version"),
            "arch_version": self.arch_version or case.get("arch_version"),
            "prompt_version": self.prompt_version or case.get("prompt_version"),
        }

        # Optionally generate base output (generated_text) if not present.
        self._ensure_generated_text(case)

        rule_results = self.rule_engine.check_case(case)
        ctx_results = self.context_validator.validate(case)
        consistency_results = self.consistency_engine.check(case)

        all_results: List[RuleResult] = []
        all_results.extend(rule_results)
        all_results.extend(ctx_results)
        all_results.extend(consistency_results)

        mandatory_fails = _collect_mandatory_failures(all_results)

        final_status = "PASS" if not mandatory_fails else "FAIL"
        case_result: Dict[str, Any] = {
            "metadata": metadata,
            "dimensions": _dimension_summary(all_results),
            "rule_results": [r.as_dict() for r in rule_results],
            "context_results": [r.as_dict() for r in ctx_results],
            "consistency_results": [r.as_dict() for r in consistency_results],
            "final_status": final_status,
            "fail_reasons": mandatory_fails or None,
        }

        # Persist each case
        self.logger.save_case_result(run_id=self.run_id, case_key=case_key, case=case, result=case_result)

        return case_result

    def _ensure_generated_text(self, case: Dict[str, Any]) -> None:
        """Ensure case['generated_text'] exists.

        If AUTO_GENERATE_BASE=1 and GEN_API_URL is set, call the generation API once
        to populate generated_text. This supports endpoints whose output key differs.
        """
        if isinstance(case.get("generated_text"), str) and case["generated_text"].strip():
            return
        # accept alternative field already present
        alt = case.get("gen_lab_company_description")
        if isinstance(alt, str) and alt.strip():
            case["generated_text"] = alt.strip()
            return

        if not self.settings.auto_generate_base:
            return
        if not self.settings.gen_api_url:
            return

        payload = build_payload_for_gen_api(case, include_keys=self.settings.gen_api_include_keys)
        try:
            resp = requests.post(self.settings.gen_api_url, json=payload, timeout=self.settings.gen_api_timeout_s)
            resp.raise_for_status()
            text = extract_text_from_gen_response(resp, response_text_keys=self.settings.gen_api_response_text_keys)
            if text and text.strip():
                case["generated_text"] = text.strip()
        except Exception:
            # Do not raise here; downstream schema check will fail and log reason.
            return

    def _run_deepeval_subprocess(self, dataset_path: Path, deepeval_script: str, output_json: Path) -> Tuple[int, str]:
        env = os.environ.copy()
        env["DEEPEVAL_DATASET"] = str(dataset_path.resolve())
        env["DEEPEVAL_OUTPUT_JSON"] = str(output_json.resolve())
        script_path = Path(deepeval_script).resolve()

        cmd = [sys.executable, str(script_path)]
        try:
            proc = subprocess.run(cmd, env=env, capture_output=True, text=True, check=False, timeout=900)
            out = (proc.stdout or "") + "\n" + (proc.stderr or "")
            return proc.returncode, out
        except Exception as e:
            return 253, f"deepeval execution failed: {e}"
