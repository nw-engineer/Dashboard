import argparse
from pathlib import Path

from orchestrator.evaluator import Evaluator

DEEPEVAL_SCRIPT = str(Path(__file__).resolve().parent / "test_judge_deepeval_v2.py")

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", default="test.json", help="Path to dataset JSON (object or array of objects)")
    ap.add_argument("--output_dir", default="evaluation_results", help="Directory for result logs")
    ap.add_argument("--config", default=None, help="Project config JSON (or set env ORCH_CONFIG)")
    ap.add_argument("--dotenv", default=None, help="Path to .env (or set env DOTENV_PATH)")
    args = ap.parse_args()

    evaluator = Evaluator(
        dataset_path=str(args.dataset),
        deepeval_script=DEEPEVAL_SCRIPT,
        output_dir=str(args.output_dir),
        project_config_path=args.config,
        dotenv_path=args.dotenv,
    )
    return evaluator.run_all()

if __name__ == "__main__":
    raise SystemExit(main())
