from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from datetime import datetime, timedelta
from pydantic import BaseModel
import json

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


DASHBOARD_STATE_FILE = "dashboard_state.json"


class DashboardState(BaseModel):
    widgets: list


@app.post("/api/save_dashboard")
async def save_dashboard(state: DashboardState):
    with open(DASHBOARD_STATE_FILE, "w") as f:
        json.dump(state.dict(), f)
    return {"message": "Dashboard state saved successfully"}


@app.get("/api/load_dashboard")
async def load_dashboard():
    try:
        with open(DASHBOARD_STATE_FILE, "r") as f:
            state = json.load(f)
        return state
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Dashboard state not found")

## Sample Data
@app.get("/api/timeseries")
async def get_timeseries():
    now = datetime.now()
    data = {
        "labels": [(now - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(5)],
        "datasets": [
            {
                "label": "Timeseries Data",
                "data": [65, 59, 80, 81, 56],
            },
        ],
    }
    return data
