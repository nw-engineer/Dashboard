import json
from pathlib import Path
from datetime import datetime
from typing import Any, Dict, Optional


class ResultLogger:
    def __init__(self, output_dir: Path = Path("evaluation_results")):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def save_case_result(self, run_id: str, case_key: str, case: Dict[str, Any], result: Dict[str, Any]) -> Path:
        ts = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
        filename = f"case_result_{run_id}_{case_key}_{ts}.json"
        p = self.output_dir / filename
        with p.open("w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)

        casefile = self.output_dir / f"case_input_{run_id}_{case_key}_{ts}.json"
        with casefile.open("w", encoding="utf-8") as f:
            json.dump(case, f, ensure_ascii=False, indent=2)

        print(f"Saved case result: {p}")
        return p

    def save_batch_result(self, run_id: str, batch_result: Dict[str, Any]) -> Path:
        ts = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
        filename = f"batch_result_{run_id}_{ts}.json"
        p = self.output_dir / filename
        with p.open("w", encoding="utf-8") as f:
            json.dump(batch_result, f, ensure_ascii=False, indent=2)
        print(f"Saved batch result: {p}")
        return p
