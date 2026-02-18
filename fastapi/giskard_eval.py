import os
import json
import tempfile
from typing import List

import giskard
from giskard.rag import QATestset, AgentAnswer, evaluate
from giskard.rag.metrics.ragas_metrics import ragas_answer_relevancy, ragas_faithfulness
from giskard.rag.metrics.correctness import correctness_metric


def _require_env(name: str) -> str:
    v = os.getenv(name)
    if not v:
        raise RuntimeError(f"Missing env var: {name}")
    return v


def main() -> None:
    # ---- Azure(LiteLLM) 設定 ----
    chat_deployment = _require_env("AZURE_OPENAI_CHAT_DEPLOYMENT")
    emb_deployment = _require_env("AZURE_OPENAI_EMBEDDING_DEPLOYMENT")

    # giskard は LiteLLM provider 形式でモデルを指定します（例: "openai/gpt-4o"）。:contentReference[oaicite:3]{index=3}
    giskard.llm.set_llm_model(f"azure/{chat_deployment}")
    giskard.llm.set_embedding_model(f"azure/{emb_deployment}")

    # ---- 入力ロード ----
    with open("cases.json", "r", encoding="utf-8") as f:
        cases = json.load(f)

    # ---- QATestset を JSONL で作って load（スキーマ差異に強い） ----
    # testset には question / reference_answer / reference_context を入れておくのが基本:contentReference[oaicite:4]{index=4}
    fd, testset_path = tempfile.mkstemp(suffix=".jsonl", prefix="giskard_testset_")
    os.close(fd)

    with open(testset_path, "w", encoding="utf-8") as wf:
        for i, c in enumerate(cases):
            rec = {
                "id": c.get("case_id", str(i)),
                "question": c["prompt"],
                "reference_answer": c.get("reference", ""),
                "reference_context": c.get("context", ""),
                "conversation_history": c.get("history", []),
                "metadata": {"case_id": c.get("case_id", str(i))},
            }
            wf.write(json.dumps(rec, ensure_ascii=False) + "\n")

    testset = QATestset.load(testset_path)

    # ---- 既に生成済み output を AgentAnswer として渡す ----
    # ragas_faithfulness などは AgentAnswer.documents（根拠コンテキスト）が重要です:contentReference[oaicite:5]{index=5}
    answers: List[AgentAnswer] = []
    for c in cases:
        ctx = c.get("context", "")
        docs = [ctx] if ctx else []
        answers.append(AgentAnswer(message=c["generated"], documents=docs))

    # ---- 評価（関連性 / 正確さ / Faithfulness）----
    #  - ragas_answer_relevancy: 関連性
    #  - ragas_faithfulness    : 根拠コンテキストに忠実か（ハルシネーション寄りの観点）
    #  - correctness_metric    : 参照回答(reference_answer)に対して正しいか（LLM-as-a-judge）:contentReference[oaicite:6]{index=6}
    report = evaluate(
        answers,
        testset=testset,
        metrics=[ragas_answer_relevancy, ragas_faithfulness, correctness_metric],
    )

    # ---- 出力 ----
    out_dir = "giskard_report"
    report.save(out_dir)
    # Notebook なら display(report.to_html(embed=True)) のように表示できます:contentReference[oaicite:7]{index=7}
    print(f"Saved report to: {out_dir}")
    print("Tip: Notebookなら report.to_html(embed=True) で可視化できます。")


if __name__ == "__main__":
    main()
