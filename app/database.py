import os
import time
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional

import boto3
from botocore.exceptions import ClientError, EndpointConnectionError


def _to_decimal(obj: Any) -> Any:
    """Recursively convert floats to Decimal for DynamoDB serialization."""
    if isinstance(obj, float):
        return Decimal(str(round(obj, 4)))
    if isinstance(obj, dict):
        return {k: _to_decimal(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_to_decimal(v) for v in obj]
    return obj


def _from_decimal(obj: Any) -> Any:
    """Recursively convert DynamoDB Decimals back to float/int for JSON."""
    if isinstance(obj, Decimal):
        return int(obj) if obj % 1 == 0 else float(obj)
    if isinstance(obj, dict):
        return {k: _from_decimal(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_from_decimal(v) for v in obj]
    return obj


DEFAULT_ZONES: List[Dict[str, Any]] = [
    {"id": "A1", "crop": "Arabica Coffee", "moisture": 38.0, "temp": 24.5, "ndvi": 0.72, "waterUsage": 140.0, "stress": 0.25},
    {"id": "A2", "crop": "Highland Tea", "moisture": 24.0, "temp": 29.2, "ndvi": 0.56, "waterUsage": 115.0, "stress": 0.68},
    {"id": "A3", "crop": "Macadamia Orchard", "moisture": 62.0, "temp": 22.8, "ndvi": 0.86, "waterUsage": 195.0, "stress": 0.08},
    {"id": "A4", "crop": "Avocado Terrace", "moisture": 54.0, "temp": 23.6, "ndvi": 0.81, "waterUsage": 175.0, "stress": 0.12},
    {"id": "B1", "crop": "Strawberry Greenhouse", "moisture": 21.0, "temp": 31.0, "ndvi": 0.51, "waterUsage": 95.0, "stress": 0.76},
    {"id": "B2", "crop": "Winter Maize", "moisture": 48.0, "temp": 25.1, "ndvi": 0.77, "waterUsage": 160.0, "stress": 0.18},
    {"id": "B3", "crop": "Upland Rice", "moisture": 68.0, "temp": 23.0, "ndvi": 0.89, "waterUsage": 220.0, "stress": 0.06},
    {"id": "B4", "crop": "Chrysanthemum Plots", "moisture": 29.0, "temp": 27.9, "ndvi": 0.63, "waterUsage": 125.0, "stress": 0.49},
]


class AgriTwinDatabase:
    """
    Manages Amazon DynamoDB tables matching the AgriTwin architecture:
      1. AgriTwin_Telemetry (PK: zone_id, SK: timestamp)
      2. AgriTwin_TwinState (PK: farm_id, SK: entity_id)
      3. AgriTwin_Commands  (PK: farm_id, SK: command_id)
    Automatically falls back to an in-memory store if DynamoDB Local is unreachable.
    """

    def __init__(self) -> None:
        self.endpoint_url = os.getenv("DYNAMODB_ENDPOINT", "http://localhost:8001")
        self.region = os.getenv("AWS_REGION", "ap-southeast-1")
        self.use_memory_fallback = False

        self._mem_telemetry: List[Dict[str, Any]] = []
        self._mem_state: Dict[str, Dict[str, Any]] = {}
        self._mem_commands: List[Dict[str, Any]] = []

        try:
            self.dynamodb = boto3.resource(
                "dynamodb",
                endpoint_url=self.endpoint_url,
                region_name=self.region,
                aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID", "local"),
                aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY", "local"),
            )
            self._ensure_tables()
        except Exception as exc:
            print(f"[AgriTwinDB] DynamoDB endpoint ({self.endpoint_url}) unavailable ({exc}). Using in-memory fallback.")
            self.use_memory_fallback = True

        self.seed_default_farm(os.getenv("FARM_ID", "MaeFahLuang_Greenhouse_01"))

    def _ensure_tables(self) -> None:
        existing = [t.name for t in self.dynamodb.tables.all()]

        if "AgriTwin_Telemetry" not in existing:
            self.dynamodb.create_table(
                TableName="AgriTwin_Telemetry",
                KeySchema=[
                    {"AttributeName": "zone_id", "KeyType": "HASH"},
                    {"AttributeName": "timestamp", "KeyType": "RANGE"},
                ],
                AttributeDefinitions=[
                    {"AttributeName": "zone_id", "AttributeType": "S"},
                    {"AttributeName": "timestamp", "AttributeType": "S"},
                ],
                BillingMode="PAY_PER_REQUEST",
            )

        if "AgriTwin_TwinState" not in existing:
            self.dynamodb.create_table(
                TableName="AgriTwin_TwinState",
                KeySchema=[
                    {"AttributeName": "farm_id", "KeyType": "HASH"},
                    {"AttributeName": "entity_id", "KeyType": "RANGE"},
                ],
                AttributeDefinitions=[
                    {"AttributeName": "farm_id", "AttributeType": "S"},
                    {"AttributeName": "entity_id", "AttributeType": "S"},
                ],
                BillingMode="PAY_PER_REQUEST",
            )

        if "AgriTwin_Commands" not in existing:
            self.dynamodb.create_table(
                TableName="AgriTwin_Commands",
                KeySchema=[
                    {"AttributeName": "farm_id", "KeyType": "HASH"},
                    {"AttributeName": "command_id", "KeyType": "RANGE"},
                ],
                AttributeDefinitions=[
                    {"AttributeName": "farm_id", "AttributeType": "S"},
                    {"AttributeName": "command_id", "AttributeType": "S"},
                ],
                BillingMode="PAY_PER_REQUEST",
            )

    def seed_default_farm(self, farm_id: str) -> None:
        state = self.get_farm_state(farm_id)
        if state.get("zones"):
            return

        now_iso = datetime.now(timezone.utc).isoformat()
        weather = {
            "airTemp": 25.0,
            "soilTemp": 18.0,
            "humidity": 68.0,
            "windSpeedMs": 14.0,
            "pressureHpa": 1018.0,
            "updatedAt": now_iso,
        }
        self.save_entity_state(farm_id, "WEATHER#CURRENT", weather)

        for z in DEFAULT_ZONES:
            self.save_entity_state(farm_id, f"ZONE#{z['id']}", {**z, "updatedAt": now_iso})

    def save_entity_state(self, farm_id: str, entity_id: str, payload: Dict[str, Any]) -> None:
        item = {"farm_id": farm_id, "entity_id": entity_id, **payload}
        if self.use_memory_fallback:
            self._mem_state[f"{farm_id}|{entity_id}"] = item
            return
        table = self.dynamodb.Table("AgriTwin_TwinState")
        table.put_item(Item=_to_decimal(item))

    def record_telemetry(self, zone_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        ts = payload.get("timestamp") or datetime.now(timezone.utc).isoformat()
        item = {"zone_id": zone_id, "timestamp": ts, **payload}
        if self.use_memory_fallback:
            self._mem_telemetry.append(item)
            if len(self._mem_telemetry) > 500:
                self._mem_telemetry.pop(0)
            return item
        table = self.dynamodb.Table("AgriTwin_Telemetry")
        table.put_item(Item=_to_decimal(item))
        return item

    def get_farm_state(self, farm_id: str) -> Dict[str, Any]:
        if self.use_memory_fallback:
            items = [v for k, v in self._mem_state.items() if k.startswith(f"{farm_id}|")]
        else:
            table = self.dynamodb.Table("AgriTwin_TwinState")
            resp = table.query(
                KeyConditionExpression=boto3.dynamodb.conditions.Key("farm_id").eq(farm_id)
            )
            items = [_from_decimal(i) for i in resp.get("Items", [])]

        zones: List[Dict[str, Any]] = []
        buildings: List[Dict[str, Any]] = []
        weather: Dict[str, Any] = {}

        for item in items:
            eid = item.get("entity_id", "")
            if eid.startswith("ZONE#"):
                zones.append(item)
            elif eid.startswith("BUILDING#"):
                buildings.append(item)
            elif eid == "WEATHER#CURRENT":
                weather = item

        zones.sort(key=lambda z: z.get("id", ""))
        return {
            "farmId": farm_id,
            "weather": weather,
            "zones": zones,
            "buildings": buildings,
            "storageBackend": "In-Memory Fallback" if self.use_memory_fallback else "Amazon DynamoDB",
            "serverTime": datetime.now(timezone.utc).isoformat(),
        }

    def enqueue_command(self, farm_id: str, command: Dict[str, Any]) -> Dict[str, Any]:
        cmd_id = f"CMD#{int(time.time() * 1000)}_{command.get('zoneId', 'ALL')}"
        item = {
            "farm_id": farm_id,
            "command_id": cmd_id,
            "status": "PENDING",
            "createdAt": datetime.now(timezone.utc).isoformat(),
            **command,
        }
        if self.use_memory_fallback:
            self._mem_commands.append(item)
            return item
        table = self.dynamodb.Table("AgriTwin_Commands")
        table.put_item(Item=_to_decimal(item))
        return item

    def pop_pending_commands(self, farm_id: str) -> List[Dict[str, Any]]:
        if self.use_memory_fallback:
            pending = [c for c in self._mem_commands if c["farm_id"] == farm_id and c["status"] == "PENDING"]
            for c in pending:
                c["status"] = "EXECUTED"
            return pending

        table = self.dynamodb.Table("AgriTwin_Commands")
        resp = table.query(
            KeyConditionExpression=boto3.dynamodb.conditions.Key("farm_id").eq(farm_id)
        )
        items = [_from_decimal(i) for i in resp.get("Items", [])]
        pending = [i for i in items if i.get("status") == "PENDING"]
        for cmd in pending:
            table.update_item(
                Key={"farm_id": farm_id, "command_id": cmd["command_id"]},
                UpdateExpression="SET #s = :val",
                ExpressionAttributeNames={"#s": "status"},
                ExpressionAttributeValues={":val": "EXECUTED"},
            )
        return pending
