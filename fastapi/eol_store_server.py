# eol_store_server.py
import asyncio
from datetime import datetime
from mcp.server.fastmcp import FastMCP, Context

from sqlalchemy import create_engine, Column, Integer, String, Date, Boolean, Text, TIMESTAMP, UniqueConstraint
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

# ========== MCP サーバ初期化 ==========
app = FastMCP("eol-store-server")

# ========== DB設定 ==========
DATABASE_URL = "postgresql://user:password@localhost:5432/mydb"  # 環境に合わせて変更
engine = create_engine(DATABASE_URL)
Session = sessionmaker(bind=engine)
Base = declarative_base()

# ========== テーブル定義 ==========
class ProductEOL(Base):
    __tablename__ = "product_eol"
    id = Column(Integer, primary_key=True, autoincrement=True)
    product = Column(String(50), nullable=False)
    cycle = Column(String(50))
    release_date = Column(Date)
    eol = Column(Date)
    latest = Column(String(50))
    latest_release_date = Column(Date)
    lts = Column(Boolean)
    link = Column(Text)
    created_at = Column(TIMESTAMP, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("product", "cycle", name="uq_product_cycle"),
    )

# 初回だけテーブル作成
Base.metadata.create_all(engine)


# ========== ツール: フラット化済みデータを保存 ==========
@app.tool()
def store_eol_records(ctx: Context, records: list[dict]):
    """
    Store flattened EOL records into RDB (PostgreSQL/MySQL).
    
    Args:
        records: List of flat EOL dicts (from Dify)
    Returns:
        Dict with summary of inserted/updated records
    """
    session = Session()
    inserted, updated, errors = 0, 0, 0

    for rec in records:
        try:
            product = rec.get("product")
            cycle = rec.get("cycle")

            # 既存レコード確認（UPSERT風）
            existing = session.query(ProductEOL).filter_by(product=product, cycle=cycle).first()
            if existing:
                # 更新
                existing.release_date = _parse_date(rec.get("release_date"))
                existing.eol = _parse_date(rec.get("eol"))
                existing.latest = rec.get("latest")
                existing.latest_release_date = _parse_date(rec.get("latest_release_date"))
                existing.lts = rec.get("lts")
                existing.link = rec.get("link")
                updated += 1
            else:
                # 新規追加
                new_record = ProductEOL(
                    product=product,
                    cycle=cycle,
                    release_date=_parse_date(rec.get("release_date")),
                    eol=_parse_date(rec.get("eol")),
                    latest=rec.get("latest"),
                    latest_release_date=_parse_date(rec.get("latest_release_date")),
                    lts=rec.get("lts"),
                    link=rec.get("link"),
                )
                session.add(new_record)
                inserted += 1

        except Exception as e:
            errors += 1
            print(f"Error processing record {rec}: {e}")

    session.commit()
    session.close()

    return {
        "inserted": inserted,
        "updated": updated,
        "errors": errors
    }


# ========== 日付パース補助関数 ==========
def _parse_date(date_str):
    if not date_str:
        return None
    try:
        return datetime.strptime(date_str, "%Y-%m-%d").date()
    except Exception:
        return None


# ========== エントリーポイント ==========
if __name__ == "__main__":
    asyncio.run(app.run())
