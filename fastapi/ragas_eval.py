import asyncio
import json
from openai import AsyncOpenAI

# Ragasはバージョンで import パスや引数名が変わりやすいので try で吸収します
try:
    from ragas.llms import llm_factory
    from ragas.embeddings.base import embedding_factory
    from ragas.metrics.collections import AnswerRelevancy, Faithfulness, AnswerCorrectness
except Exception as e:
    raise RuntimeError("ragas の import に失敗しました。ragas のバージョンをご確認ください。") from e

async def ascore_robust(metric, **kwargs):
    """
    ragas の ascore は引数名が変わることがあるので、
    よくある候補を順番に試します。
    """
    candidates = [kwargs]

    # contexts系の別名候補
    if "retrieved_contexts" in kwargs:
        k2 = dict(kwargs)
        k2["contexts"] = k2.pop("retrieved_contexts")
        candidates.append(k2)

    for cand in candidates:
        try:
            return await metric.ascore(**cand)
        except TypeError:
            continue
    raise

async def main():
    cases = json.load(open("cases.json", "r", encoding="utf-8"))

    client = AsyncOpenAI()
    llm = llm_factory("gpt-4o-mini", client=client)
    emb = embedding_factory("openai", model="text-embedding-3-small", client=client)

    m_rel = AnswerRelevancy(llm=llm, embeddings=emb)
    m_fai = Faithfulness(llm=llm)
    m_cor = AnswerCorrectness(llm=llm, embeddings=emb)

    for c in cases:
        prompt = c["prompt"]
        out = c["generated"]
        ctxs = [c["context"]]
        ref = c["reference"]

        rel = await ascore_robust(m_rel, user_input=prompt, response=out, retrieved_contexts=ctxs)
        fai = await ascore_robust(m_fai, user_input=prompt, response=out, retrieved_contexts=ctxs)
        cor = await ascore_robust(m_cor, user_input=prompt, response=out, reference=ref)

        print(f"\n=== {c['case_id']} ===")
        print(f"- answer_relevancy : {rel:.4f}")
        print(f"- faithfulness     : {fai:.4f}")
        print(f"- answer_correctness(reference): {cor:.4f}")

if __name__ == "__main__":
    asyncio.run(main())
