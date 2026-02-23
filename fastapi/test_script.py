import sys
from pathlib import Path

from orchestrator.evaluator import Evaluator

OUTPUT_PATH = "test.json"
DEEPEVAL_SCRIPT = str(Path(__file__).resolve().parent / "test_judge_deepeval.py")

def main() -> int:
    evaluator = Evaluator(dataset_path=str(OUTPUT_PATH), deepeval_script=DEEPEVAL_SCRIPT)
    return evaluator.run_all()

if __name__ == "__main__":
    raise SystemExit(main())
