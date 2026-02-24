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
from .config import load_dotenv, load_project_config
from .context_validator import ContextValidator
from .result_logger import ResultLogger
from .rule_engine import RuleEngine, RuleResult
from .important_fields import ImportantFieldChecker


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
        *,
        project_config_path: Optional[str] = None,
        dotenv_path: Optional[str] = None,
    ):
        self.dataset_path = Path(dataset_path)
        self.deepeval_script = str(deepeval_script)
        # Load environment + project config (optional)
        load_dotenv(dotenv_path)
        self.project_config = load_project_config(project_config_path)

        self.run_id = str(uuid.uuid4())

        # Optional important-fields spec (project-specific)
        self.important_field_checker = None
        spec_path = self.project_config.get("important_fields_spec") if isinstance(self.project_config, dict) else None
        if spec_path:
            try:
                self.important_field_checker = ImportantFieldChecker.from_json_file(spec_path)
            except Exception as e:
                # Fail-fast: misconfigured spec should surface early
                raise RuntimeError(f"Failed to load important_fields_spec: {e}") from e

        # Build engines with config overrides (env is still supported inside engines)
        rule_conf = (self.project_config.get("rule_engine") or {}) if isinstance(self.project_config, dict) else {}
        cons_conf = (self.project_config.get("consistency_engine") or {}) if isinstance(self.project_config, dict) else {}

        self.rule_engine = RuleEngine(
            max_output_len=rule_conf.get("max_output_len"),
            require_last_updated=rule_conf.get("require_last_updated"),
            freshness_months=rule_conf.get("freshness_months"),
            require_user_input=rule_conf.get("require_user_input"),
            allow_definitive_words=rule_conf.get("allow_definitive_words"),
            important_field_checker=self.important_field_checker,
        )
        self.context_validator = ContextValidator()
        self.consistency_engine = ConsistencyEngine(
            regen_endpoint=cons_conf.get("regen_endpoint"),
            n_samples=int(cons_conf.get("n_samples", 3)),
            embedding_model_name=cons_conf.get("embedding_model_name"),
            sim_threshold=float(cons_conf.get("sim_threshold", 0.92)),
            require_embedding=cons_conf.get("require_embedding"),
        )
        self.context_validator = ContextValidator()
        self.consistency_engine = ConsistencyEngine()
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
