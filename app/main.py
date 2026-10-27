from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from app.database import AgriTwinDatabase
from app.rl_agent import AgriTwinRLAdvisor

app = FastAPI(
    title="AgriTwin Cloud Service Provider & RL Advisor API",
    description=(
        "Synchronizes physical Greenhouse IoT sensors, Amazon DynamoDB state tables, "
        "Reinforcement Learning irrigation policies, and the Meta Quest 3S Unity Digital Twin."
    ),
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

db = AgriTwinDatabase()
rl_advisor = AgriTwinRLAdvisor()
active_websockets: List[WebSocket] = []


class ZoneTelemetryPayload(BaseModel):
    farmId: str = Field(default="MaeFahLuang_Greenhouse_01")
    zoneId: str = Field(..., examples=["A1"])
    crop: Optional[str] = None
    moisture: float = Field(..., ge=0.0, le=100.0)
    temp: float = Field(..., ge=-10.0, le=60.0)
    ndvi: float = Field(default=0.75, ge=0.0, le=1.0)
    waterUsage: float = Field(default=150.0, ge=0.0)
    airTemp: Optional[float] = None
    soilTemp: Optional[float] = None
    humidity: Optional[float] = None
    windSpeedMs: Optional[float] = None
    pressureHpa: Optional[float] = None


class IrrigationCommandPayload(BaseModel):
    zoneId: str = Field(..., examples=["A2"])
    action: str = Field(default="OPEN_VALVE")
    durationSec: int = Field(default=900, ge=10, le=7200)
    moistureBoost: float = Field(default=22.0)
    source: str = Field(default="META_QUEST_3S_VR")


class PlacedBuildingPayload(BaseModel):
    buildingId: str
    buildingType: str
    gridX: int
    gridZ: int
    rotationSteps: int = 0


async def _broadcast_event(event: Dict[str, Any]) -> None:
    stale: List[WebSocket] = []
    for ws in active_websockets:
        try:
            await ws.send_json(event)
        except Exception:
            stale.append(ws)
    for ws in stale:
        active_websockets.remove(ws)


@app.get("/health")
def health_check() -> Dict[str, Any]:
    return {
        "status": "ok",
        "service": "AgriTwin Cloud Data Server",
        "storageBackend": "In-Memory Fallback" if db.use_memory_fallback else "Amazon DynamoDB",
        "rlPolicyIterations": rl_advisor.training_steps,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/api/v1/farms/{farm_id}/state")
def get_farm_twin_state(farm_id: str) -> Dict[str, Any]:
    state = db.get_farm_state(farm_id)
    rl_summary = rl_advisor.evaluate_farm(state.get("zones", []))
    state["rlAdvisor"] = rl_summary
    return state


@app.post("/api/v1/telemetry")
async def ingest_iot_telemetry(payload: ZoneTelemetryPayload) -> Dict[str, Any]:
    now_iso = datetime.now(timezone.utc).isoformat()
    moisture_err = abs(payload.moisture - 52.0) / 52.0
    stress = round(min(1.0, moisture_err * 0.75 + max(0.0, (payload.temp - 27.0) * 0.08)), 3)

    zone_state = {
        "id": payload.zoneId,
        "crop": payload.crop or payload.zoneId,
        "moisture": round(payload.moisture, 2),
        "temp": round(payload.temp, 2),
        "ndvi": round(payload.ndvi, 3),
        "waterUsage": round(payload.waterUsage, 1),
        "stress": stress,
        "updatedAt": now_iso,
    }

    db.record_telemetry(payload.zoneId, {**zone_state, "farmId": payload.farmId, "timestamp": now_iso})
    db.save_entity_state(payload.farmId, f"ZONE#{payload.zoneId}", zone_state)

    if payload.airTemp is not None or payload.humidity is not None:
        weather = {
            "airTemp": round(payload.airTemp or payload.temp, 1),
            "soilTemp": round(payload.soilTemp or (payload.temp - 6.0), 1),
            "humidity": round(payload.humidity or 68.0, 1),
            "windSpeedMs": round(payload.windSpeedMs or 14.0, 1),
            "pressureHpa": round(payload.pressureHpa or 1018.0, 1),
            "updatedAt": now_iso,
        }
        db.save_entity_state(payload.farmId, "WEATHER#CURRENT", weather)

    rl_rec = rl_advisor.observe_and_update(zone_state)
    await _broadcast_event({"type": "TELEMETRY_UPDATE", "zone": zone_state, "rlRecommendation": rl_rec})

    return {"status": "recorded", "zone": zone_state, "rlRecommendation": rl_rec}


@app.post("/api/v1/farms/{farm_id}/commands")
async def create_irrigation_command(farm_id: str, cmd: IrrigationCommandPayload) -> Dict[str, Any]:
    queued = db.enqueue_command(farm_id, cmd.model_dump())

    # Immediately update Digital Twin zone state so Unity VR reflects the valve action without lag
    state = db.get_farm_state(farm_id)
    for z in state.get("zones", []):
        if z.get("id") == cmd.zoneId:
            z["moisture"] = round(min(92.0, float(z.get("moisture", 40.0)) + cmd.moistureBoost), 2)
            z["temp"] = round(max(18.0, float(z.get("temp", 25.0)) - 1.4), 2)
            z["ndvi"] = round(min(0.95, float(z.get("ndvi", 0.70)) + 0.05), 3)
            z["stress"] = round(max(0.04, float(z.get("stress", 0.3)) * 0.45), 3)
            z["updatedAt"] = datetime.now(timezone.utc).isoformat()
            db.save_entity_state(farm_id, f"ZONE#{cmd.zoneId}", z)
            break

    await _broadcast_event({"type": "COMMAND_QUEUED", "command": queued})
    return {"status": "queued", "command": queued}


@app.get("/api/v1/farms/{farm_id}/commands/pending")
def poll_pending_commands_for_iot(farm_id: str) -> Dict[str, Any]:
    pending = db.pop_pending_commands(farm_id)
    return {"farmId": farm_id, "count": len(pending), "commands": pending}


@app.post("/api/v1/farms/{farm_id}/buildings")
async def sync_placed_building(farm_id: str, building: PlacedBuildingPayload) -> Dict[str, Any]:
    item = {
        **building.model_dump(),
        "updatedAt": datetime.now(timezone.utc).isoformat(),
    }
    db.save_entity_state(farm_id, f"BUILDING#{building.buildingId}", item)
    await _broadcast_event({"type": "BUILDING_PLACED", "building": item})
    return {"status": "synced", "building": item}


@app.websocket("/ws/farms/{farm_id}")
async def farm_realtime_socket(websocket: WebSocket, farm_id: str) -> None:
    await websocket.accept()
    active_websockets.append(websocket)
    try:
        await websocket.send_json({"type": "INITIAL_STATE", "data": get_farm_twin_state(farm_id)})
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        if websocket in active_websockets:
            active_websockets.remove(websocket)
