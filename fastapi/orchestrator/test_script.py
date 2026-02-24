import sys
from pathlib import Path

from orchestrator.evaluator import Evaluator

# Dataset path (change if needed)
DATASET_PATH = "test.json"

# Use the v2 deepeval judge script (schema-flexible)
DEEPEVAL_SCRIPT = str(Path(__file__).resolve().parent / "test_judge_deepeval_v2.py")

def main():
    evaluator = Evaluator(dataset_path=str(DATASET_PATH), deepeval_script=DEEPEVAL_SCRIPT)
    exit_code = evaluator.run_all()
    sys.exit(exit_code)

if __name__ == "__main__":
    main()
