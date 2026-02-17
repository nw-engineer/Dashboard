import json
import pandas as pd
from giskard.rag import evaluate, KnowledgeBase
from giskard.rag.testset import QuestionSample, QATestset
from giskard.rag.metrics import correctness_metric
from giskard.rag.metrics.ragas import ragas_faithfulness, ragas_answer_relevancy

def main():
    cases = json.load(open("cases.json", "r", encoding="utf-8"))

    # testset（手作り）: question / reference_answer / reference_context を入れる :contentReference[oaicite:19]{index=19}
    samples = []
    for c in cases:
        samples.append(
            QuestionSample(
                id=c["case_id"],
                question=c["prompt"],
                reference_answer=c["reference"],
                reference_context=c["context"],
                conversation_history=[],
                metadata={"kind": "offer_letter"},
            )
        )
    testset = QATestset(samples)

    # knowledge_base は形式上用意（参照用）
    df = pd.DataFrame({"samples": [c["context"] for c in cases]})
    kb = KnowledgeBase.from_pandas(df, columns=["samples"])

    # あなたの予測関数（ここでは prompt から case を引いて generated を返す）
    def predict_fn(question: str, history=None) -> str:
        c = next(x for x in cases if x["prompt"] == question)
        return c["generated"]

    report = evaluate(
        predict_fn,
        testset=testset,
        knowledge_base=kb,
        metrics=[correctness_metric, ragas_answer_relevancy, ragas_faithfulness],
    )

    # ざっくり表示＆保存
    display(report)
    report.save("giskard_report")

if __name__ == "__main__":
    main()
