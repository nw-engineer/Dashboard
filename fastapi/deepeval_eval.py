import json
from deepeval.test_case import LLMTestCase
from deepeval.metrics import AnswerRelevancyMetric, FaithfulnessMetric, HallucinationMetric

def main():
    cases = json.load(open("cases.json", "r", encoding="utf-8"))

    metrics = [
        AnswerRelevancyMetric(include_reason=True),
        FaithfulnessMetric(include_reason=True),
        HallucinationMetric(include_reason=True),
    ]

    for c in cases:
        # DeepEvalのバージョン差異に備え、context / retrieval_context を両方試す方針
        try:
            tc = LLMTestCase(
                input=c["prompt"],
                actual_output=c["generated"],
                retrieval_context=[c["context"]],
                expected_output=c["reference"],
            )
        except TypeError:
            tc = LLMTestCase(
                input=c["prompt"],
                actual_output=c["generated"],
                context=[c["context"]],
                expected_output=c["reference"],
            )

        print(f"\n=== {c['case_id']} ===")
        for m in metrics:
            m.measure(tc)
            print(f"- {m.__class__.__name__}: {m.score:.4f}")
            if getattr(m, "reason", None):
                print(f"  reason: {m.reason}")

if __name__ == "__main__":
    main()
