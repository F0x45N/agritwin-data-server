import math
import os
import random
import time
from typing import Any, Dict, List

import requests

API_BASE_URL = os.getenv("API_BASE_URL", "http://localhost:8000")
FARM_ID = os.getenv("FARM_ID", "MaeFahLuang_Greenhouse_01")
TICK_INTERVAL = float(os.getenv("TICK_INTERVAL_SECONDS", "3.0"))

ZONES: List[Dict[str, Any]] = [
    {"id": "A1", "crop": "Arabica Coffee", "moisture": 38.0, "temp": 24.5, "ndvi": 0.72, "waterUsage": 140.0},
    {"id": "A2", "crop": "Highland Tea", "moisture": 24.0, "temp": 29.2, "ndvi": 0.56, "waterUsage": 115.0},
    {"id": "A3", "crop": "Macadamia Orchard", "moisture": 62.0, "temp": 22.8, "ndvi": 0.86, "waterUsage": 195.0},
    {"id": "A4", "crop": "Avocado Terrace", "moisture": 54.0, "temp": 23.6, "ndvi": 0.81, "waterUsage": 175.0},
    {"id": "B1", "crop": "Strawberry Greenhouse", "moisture": 21.0, "temp": 31.0, "ndvi": 0.51, "waterUsage": 95.0},
    {"id": "B2", "crop": "Winter Maize", "moisture": 48.0, "temp": 25.1, "ndvi": 0.77, "waterUsage": 160.0},
    {"id": "B3", "crop": "Upland Rice", "moisture": 68.0, "temp": 23.0, "ndvi": 0.89, "waterUsage": 220.0},
    {"id": "B4", "crop": "Chrysanthemum Plots", "moisture": 29.0, "temp": 27.9, "ndvi": 0.63, "waterUsage": 125.0},
]


def run_simulator() -> None:
    print(f"[IoT Simulator] Starting physical greenhouse sensor stream -> {API_BASE_URL} ({FARM_ID})")
    tick = 0
    while True:
        tick += 1
        diurnal = math.sin(tick * 0.15)

        # 1. Poll cloud server for pending irrigation valve commands triggered from Meta Quest 3S VR
        try:
            cmd_resp = requests.get(f"{API_BASE_URL}/api/v1/farms/{FARM_ID}/commands/pending", timeout=3)
            if cmd_resp.ok:
                commands = cmd_resp.json().get("commands", [])
                for cmd in commands:
                    target_id = cmd.get("zoneId")
                    boost = float(cmd.get("moistureBoost", 22.0))
                    print(f"[IoT Simulator] Executing physical valve command {cmd.get('action')} on Zone {target_id} (+{boost}% moisture)")
                    for z in ZONES:
                        if z["id"] == target_id:
                            z["moisture"] = min(92.0, z["moisture"] + boost)
                            z["temp"] = max(18.5, z["temp"] - 1.5)
                            z["ndvi"] = min(0.95, z["ndvi"] + 0.04)
                            z["waterUsage"] += 25.0
        except Exception as exc:
            print(f"[IoT Simulator] Waiting for API server ({exc})...")
            time.sleep(TICK_INTERVAL)
            continue

        # 2. Simulate natural evapotranspiration & sensor drift and push telemetry to Cloud API
        air_temp = 25.0 + diurnal * 2.4 + random.uniform(-0.3, 0.3)
        soil_temp = 18.0 + diurnal * 1.2 + random.uniform(-0.2, 0.2)
        humidity = max(40.0, min(95.0, 68.0 - diurnal * 8.0 + random.uniform(-1.5, 1.5)))
        wind_speed = max(2.0, 14.0 + math.cos(tick * 0.2) * 3.5 + random.uniform(-0.8, 0.8))
        pressure = 1018.0 + math.sin(tick * 0.07) * 4.0

        zone = ZONES[tick % len(ZONES)]
        zone["moisture"] = max(14.0, min(92.0, zone["moisture"] - random.uniform(0.15, 0.55)))
        zone["temp"] = max(18.0, min(35.0, zone["temp"] + diurnal * 0.15 + random.uniform(-0.2, 0.25)))
        if zone["moisture"] < 28.0:
            zone["ndvi"] = max(0.35, zone["ndvi"] - 0.004)
        else:
            zone["ndvi"] = min(0.94, zone["ndvi"] + 0.002)

        payload = {
            "farmId": FARM_ID,
            "zoneId": zone["id"],
            "crop": zone["crop"],
            "moisture": round(zone["moisture"], 2),
            "temp": round(zone["temp"], 2),
            "ndvi": round(zone["ndvi"], 3),
            "waterUsage": round(zone["waterUsage"], 1),
            "airTemp": round(air_temp, 1),
            "soilTemp": round(soil_temp, 1),
            "humidity": round(humidity, 1),
            "windSpeedMs": round(wind_speed, 1),
            "pressureHpa": round(pressure, 1),
        }

        try:
            r = requests.post(f"{API_BASE_URL}/api/v1/telemetry", json=payload, timeout=3)
            if r.ok:
                rec = r.json().get("rlRecommendation", {})
                print(
                    f"[IoT -> Cloud] Zone {zone['id']} | Moisture={zone['moisture']:.1f}% Temp={zone['temp']:.1f}C "
                    f"| RL Policy: {rec.get('recommendedAction')} (R={rec.get('currentReward')})"
                )
        except Exception as exc:
            print(f"[IoT Simulator] Telemetry post failed: {exc}")

        time.sleep(TICK_INTERVAL)


if __name__ == "__main__":
    run_simulator()
