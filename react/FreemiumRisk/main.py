from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel
from typing import List, Optional
from fastapi.middleware.cors import CORSMiddleware
from uuid import uuid4
import os
import sqlite3
from langchain.chains import LLMChain
from langchain_openai import OpenAI
from langchain.prompts import PromptTemplate

# --- FastAPI setup ---
app = FastAPI()

# CORS setup for frontend interaction
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- Database setup ---

DB_PATH = os.path.join(os.path.dirname(__file__), "data", "freemium_tools.db")
os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)

conn = sqlite3.connect(DB_PATH, check_same_thread=False)
cursor = conn.cursor()

cursor.execute("""
CREATE TABLE IF NOT EXISTS apps (
    id TEXT PRIMARY KEY,
    name TEXT,
    url TEXT,
    description TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS ratings (
    id TEXT PRIMARY KEY,
    app_id TEXT,
    rating INTEGER,
    comment TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(app_id) REFERENCES apps(id)
);
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS risks (
    id TEXT PRIMARY KEY,
    app_id TEXT,
    score REAL,
    report TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(app_id) REFERENCES apps(id)
);
""")
conn.commit()

# --- Models ---
class AppSubmission(BaseModel):
    name: str
    url: str
    description: Optional[str] = ""

class RatingSubmission(BaseModel):
    app_id: str
    rating: int
    comment: Optional[str] = ""

# --- Langchain AI Risk Analysis ---
prompt_template = PromptTemplate(
    input_variables=["url"],
    template="""
    次のURLのソフトウェアについて、以下の4つの観点からリスク分析を行ってください：
    - セキュリティ
    - ライセンス
    - 更新頻度
    - 依存性

    各観点ごとに簡潔な評価と1.0～5.0（高いほど安全）のスコアを提示してください。
    最後に「総合評価」として簡単な総括と、以下の形式で必ず出力してください：

    【出力フォーマット】
    1. セキュリティ：<数値>
    <解説>
 
    2. ライセンス：<数値>
    <解説>

    3. 更新頻度：<数値>
    <解説>

    4. 依存性：<数値>
    <解説>

    総合評価：<総合スコア（1.0～5.0）>
    <総括>

    対象URL：{url}
    """
)
llm = OpenAI(temperature=0.3, max_tokens=3000)
chain = prompt_template | llm

def analyze_risk(url: str):
    response = chain.invoke({"url": url}).strip()
    import re
    match = re.search(r"総合評価[:：]\s*([1-5]\.[0-9])", response)
    #match = re.search(r"([1-5]\\.[0-9])", response)
    score = float(match.group(1)) if match else 3.0
    return score, response

def calculate_composite_score(ai_score: float, user_avg: float) -> float:
    return round((ai_score * 0.5) + (user_avg * 0.5), 2)

def get_safety_label(score: float) -> str:
    if score >= 4.5:
        return "安全（おすすめ）"
    elif score >= 3.5:
        return "注意して利用可"
    elif score >= 2.5:
        return "慎重に"
    else:
        return "推奨されない"

# --- API Endpoints ---
@app.post("/apps")
def submit_app(app: AppSubmission):
    app_id = str(uuid4())
    cursor.execute("INSERT INTO apps (id, name, url, description) VALUES (?, ?, ?, ?)",
                   (app_id, app.name, app.url, app.description))
    conn.commit()

    score, report = analyze_risk(app.url)
    cursor.execute("INSERT INTO risks (id, app_id, score, report) VALUES (?, ?, ?, ?)",
                   (str(uuid4()), app_id, score, report))
    conn.commit()

    return {"id": app_id, "score": score, "report": report}

@app.post("/ratings")
def submit_rating(rating: RatingSubmission):
    rating_id = str(uuid4())
    cursor.execute("INSERT INTO ratings (id, app_id, rating, comment) VALUES (?, ?, ?, ?)",
                   (rating_id, rating.app_id, rating.rating, rating.comment))
    conn.commit()
    return {"message": "Rating submitted", "id": rating_id}

@app.get("/apps")
def list_apps():
    cursor.execute("SELECT a.id, a.name, a.url, a.description, IFNULL(AVG(r.rating), 0), rk.score FROM apps a LEFT JOIN ratings r ON a.id = r.app_id LEFT JOIN risks rk ON a.id = rk.app_id GROUP BY a.id")
    apps = cursor.fetchall()
    return [
        {
            "id": row[0],
            "name": row[1],
            "url": row[2],
            "description": row[3],
            "average_rating": round(row[4], 2),
            "ai_risk_score": row[5],
            "composite_score": calculate_composite_score(row[5], row[4]),
            "safety_label": get_safety_label(calculate_composite_score(row[5], row[4]))
        }
        for row in apps
    ]

@app.get("/apps/{app_id}")
def get_app_detail(app_id: str):
    cursor.execute("SELECT * FROM apps WHERE id = ?", (app_id,))
    app = cursor.fetchone()
    if not app:
        raise HTTPException(status_code=404, detail="App not found")

    cursor.execute("SELECT score, report FROM risks WHERE app_id = ?", (app_id,))
    risk = cursor.fetchone()

    cursor.execute("SELECT rating, comment FROM ratings WHERE app_id = ?", (app_id,))
    ratings = cursor.fetchall()

    average_rating = round(sum([r[0] for r in ratings]) / len(ratings), 2) if ratings else 0.0
    ai_score = risk[0] if risk else 3.0
    composite = calculate_composite_score(ai_score, average_rating)

    return {
        "id": app[0],
        "name": app[1],
        "url": app[2],
        "description": app[3],
        "risk_score": ai_score,
        "risk_report": risk[1] if risk else None,
        "average_rating": average_rating,
        "composite_score": composite,
        "safety_label": get_safety_label(composite),
        "ratings": [
            {"rating": r[0], "comment": r[1]} for r in ratings
        ]
    }


@app.delete("/dev/clear_all")
def clear_all_data():
    cursor.execute("DELETE FROM ratings;")
    cursor.execute("DELETE FROM risks;")
    cursor.execute("DELETE FROM apps;")
    conn.commit()
    return {"message": "All data cleared."}
