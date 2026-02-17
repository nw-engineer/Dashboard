import json
import numpy as np
from trulens.core import TruSession, Feedback
from trulens.core.otel.instrument import instrument
from trulens.apps.custom import TruCustomApp
from trulens.providers.openai import OpenAI as fOpenAI
from trulens.feedback import GroundTruthAgreement

class OfferApp:
    def __init__(self, cases):
        self.cases = {c["case_id"]: c for c in cases}

    @instrument()
    def retrieve(self, case_id: str):
        return [self.cases[case_id]["context"]]

    @instrument()
    def generate(self, case_id: str, contexts):
        # ここでは生成済みテキストを返す（あなたのAPI呼び出しに置換可能）
        return self.cases[case_id]["generated"]

    @instrument()
    def query(self, case_id: str):
        ctx = self.retrieve(case_id)
        return self.generate(case_id, ctx)

def main():
    cases = json.load(open("cases.json", "r", encoding="utf-8"))
    session = TruSession()

    provider = fOpenAI(model_engine="gpt-4o-mini")

    # TruLens Quickstartの定義に寄せた3つ（Answer Relevance / Groundedness） :contentReference[oaicite:16]{index=16}
    f_groundedness = (
        Feedback(
            provider.groundedness_measure_with_cot_reasons_consider_answerability,
            name="Groundedness",
        )
        .on_context(collect_list=True)
        .on_output()
        .on_input()
    )

    f_answer_relevance = (
        Feedback(provider.relevance_with_cot_reasons, name="Answer Relevance")
        .on_input()
        .on_output()
    )

    # “正確さ” を golden（reference）との一致で評価（GroundTruthAgreement） :contentReference[oaicite:17]{index=17}
    golden_set = [{"query": c["case_id"], "expected_response": c["reference"]} for c in cases]
    f_gt = Feedback(
        GroundTruthAgreement(golden_set, provider=provider).agreement_measure,
        name="Ground Truth Semantic Agreement",
    ).on_input_output()

    app = OfferApp(cases)
    tru_app = TruCustomApp(app, app_id="offer_app", feedbacks=[f_answer_relevance, f_groundedness, f_gt])

    with tru_app as recording:
        for c in cases:
            app.query(c["case_id"])

    df = session.get_records_and_feedback(app_ids=["offer_app"])[0]
    print(df[["input", "output", "Answer Relevance", "Groundedness", "Ground Truth Semantic Agreement"]])

if __name__ == "__main__":
    main()
