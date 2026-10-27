from typing import Any, Dict, List


class AgriTwinRLAdvisor:
    """
    Tabular Q-Learning & Contextual Policy Engine for the AgriTwin Digital Twin.
    Matches the 'Reinforcement Learning' block in the architecture diagram:
      - State s_t: discretized (moisture_bucket, temp_bucket, stress_bucket)
      - Actions a_t:
          0 = HOLD_VALVE (0 L/m2)
          1 = PULSE_DRIP_15M (+12% moisture)
          2 = DEEP_IRRIGATE_30M (+24% moisture)
          3 = DEPLOY_MIST_COOLING (+8% moisture, -1.8C temp)
      - Reward R_t = 1.5 * NDVI - 0.002 * WaterUsage - 2.2 * StressPenalty
    """

    ACTIONS = [
        {"actionId": 0, "code": "HOLD_VALVE", "label": "Maintain Current Schedule", "moistureDelta": 0.0, "tempDelta": 0.0},
        {"actionId": 1, "code": "PULSE_DRIP_15M", "label": "Pulse Drip Irrigation (15 min)", "moistureDelta": 12.0, "tempDelta": -0.6},
        {"actionId": 2, "code": "DEEP_IRRIGATE_30M", "label": "Deep Root Irrigation (30 min)", "moistureDelta": 24.0, "tempDelta": -1.4},
        {"actionId": 3, "code": "DEPLOY_MIST_COOLING", "label": "Canopy Mist & Ventilation", "moistureDelta": 8.0, "tempDelta": -2.1},
    ]

    def __init__(self, alpha: float = 0.25, gamma: float = 0.88) -> None:
        self.alpha = alpha
        self.gamma = gamma
        self.q_table: Dict[str, List[float]] = {}
        self.training_steps = 0

    def _state_key(self, moisture: float, temp: float) -> str:
        if moisture < 30.0:
            m_bin = "DRY"
        elif moisture <= 65.0:
            m_bin = "OPTIMAL"
        else:
            m_bin = "WET"

        if temp > 28.0:
            t_bin = "HOT"
        elif temp >= 20.0:
            t_bin = "MILD"
        else:
            t_bin = "COOL"

        return f"{m_bin}_{t_bin}"

    def _ensure_state(self, state_key: str) -> List[float]:
        if state_key not in self.q_table:
            # Initialize Q-values with domain-informed priors so recommendations are immediately safe
            if state_key.startswith("DRY_HOT"):
                self.q_table[state_key] = [-0.5, 0.65, 0.92, 0.78]
            elif state_key.startswith("DRY"):
                self.q_table[state_key] = [-0.3, 0.84, 0.76, 0.40]
            elif state_key.startswith("OPTIMAL_HOT"):
                self.q_table[state_key] = [0.45, 0.52, 0.20, 0.88]
            elif state_key.startswith("OPTIMAL"):
                self.q_table[state_key] = [0.90, 0.35, 0.10, 0.25]
            else:
                self.q_table[state_key] = [0.95, -0.40, -0.75, 0.15]
        return self.q_table[state_key]

    def compute_reward(self, moisture: float, temp: float, ndvi: float, water_usage: float) -> float:
        moisture_error = abs(moisture - 52.0) / 52.0
        temp_penalty = max(0.0, (temp - 26.5) * 0.12)
        stress = min(1.0, moisture_error * 0.7 + temp_penalty)
        reward = (1.5 * ndvi) - (0.0015 * water_usage) - (2.0 * stress)
        return round(reward, 4)

    def observe_and_update(self, zone: Dict[str, Any], action_id: int = 0) -> Dict[str, Any]:
        moisture = float(zone.get("moisture", 45.0))
        temp = float(zone.get("temp", 25.0))
        ndvi = float(zone.get("ndvi", 0.72))
        water = float(zone.get("waterUsage", 150.0))

        state_key = self._state_key(moisture, temp)
        q_vals = self._ensure_state(state_key)
        reward = self.compute_reward(moisture, temp, ndvi, water)

        action_idx = max(0, min(len(self.ACTIONS) - 1, action_id))
        best_next_q = max(q_vals)
        q_vals[action_idx] = round(
            (1.0 - self.alpha) * q_vals[action_idx] + self.alpha * (reward + self.gamma * best_next_q),
            4,
        )
        self.training_steps += 1
        return self.recommend_for_zone(zone)

    def recommend_for_zone(self, zone: Dict[str, Any]) -> Dict[str, Any]:
        moisture = float(zone.get("moisture", 45.0))
        temp = float(zone.get("temp", 25.0))
        ndvi = float(zone.get("ndvi", 0.72))
        water = float(zone.get("waterUsage", 150.0))

        state_key = self._state_key(moisture, temp)
        q_vals = self._ensure_state(state_key)
        best_idx = int(max(range(len(q_vals)), key=lambda i: q_vals[i]))
        best_action = self.ACTIONS[best_idx]
        reward = self.compute_reward(moisture, temp, ndvi, water)

        return {
            "zoneId": zone.get("id", "A1"),
            "stateKey": state_key,
            "recommendedAction": best_action["code"],
            "recommendedLabel": best_action["label"],
            "expectedQValue": q_vals[best_idx],
            "currentReward": reward,
            "autoIrrigateTriggered": best_idx in (1, 2) and moisture < 28.0,
        }

    def evaluate_farm(self, zones: List[Dict[str, Any]]) -> Dict[str, Any]:
        recommendations = [self.recommend_for_zone(z) for z in zones]
        urgent = [r for r in recommendations if r["recommendedAction"] != "HOLD_VALVE"]
        avg_reward = round(
            sum(r["currentReward"] for r in recommendations) / max(1, len(recommendations)),
            3,
        )
        return {
            "policyIteration": self.training_steps,
            "averageFarmReward": avg_reward,
            "urgentZoneCount": len(urgent),
            "zoneRecommendations": recommendations,
        }
