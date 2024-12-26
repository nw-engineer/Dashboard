from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from sqlalchemy import Column, Integer, String, create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from fastapi.middleware.cors import CORSMiddleware

DATABASE_URL = "sqlite:///./test.db"
engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

class Prefecture(Base):
    __tablename__ = "prefectures"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, index=True)
    count = Column(Integer, default=0)

class PrefectureUpdate(BaseModel):
    name: str
    count: int

Base.metadata.create_all(bind=engine)

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/prefectures")
def get_prefectures():
    with SessionLocal() as session:
        data = session.query(Prefecture).all()
        return [{"name": p.name, "count": p.count} for p in data]

@app.post("/prefectures")
def update_prefecture(prefecture: PrefectureUpdate):
    with SessionLocal() as session:
        db_pref = session.query(Prefecture).filter(Prefecture.name == prefecture.name).first()
        if db_pref:
            db_pref.count = prefecture.count
        else:
            db_pref = Prefecture(name=prefecture.name, count=prefecture.count)
            session.add(db_pref)
        session.commit()
        return {"message": "Updated successfully"}

