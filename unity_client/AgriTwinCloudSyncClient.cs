#nullable enable
using System;
using System.Collections;
using System.Collections.Generic;
using System.Text;
using UnityEngine;
using UnityEngine.Networking;

namespace AgriVision.Twin
{
    [Serializable]
    public class CloudWeatherDto
    {
        public float airTemp = 25.0f;
        public float soilTemp = 18.0f;
        public float humidity = 68.0f;
        public float windSpeedMs = 14.0f;
        public float pressureHpa = 1018.0f;
    }

    [Serializable]
    public class CloudZoneDto
    {
        public string id = "";
        public string crop = "";
        public float moisture;
        public float temp;
        public float ndvi;
        public float waterUsage;
        public float stress;
    }

    [Serializable]
    public class CloudRLZoneRecDto
    {
        public string zoneId = "";
        public string stateKey = "";
        public string recommendedAction = "";
        public string recommendedLabel = "";
        public float expectedQValue;
        public float currentReward;
    }

    [Serializable]
    public class CloudRLSummaryDto
    {
        public int policyIteration;
        public float averageFarmReward;
        public int urgentZoneCount;
        public List<CloudRLZoneRecDto> zoneRecommendations = new List<CloudRLZoneRecDto>();
    }

    [Serializable]
    public class CloudFarmStateResponse
    {
        public string farmId = "MaeFahLuang_Greenhouse_01";
        public string storageBackend = "Amazon DynamoDB";
        public CloudWeatherDto weather = new CloudWeatherDto();
        public List<CloudZoneDto> zones = new List<CloudZoneDto>();
        public CloudRLSummaryDto rlAdvisor = new CloudRLSummaryDto();
    }

    [Serializable]
    public class CloudIrrigationCommandRequest
    {
        public string zoneId = "A1";
        public string action = "OPEN_VALVE";
        public int durationSec = 900;
        public float moistureBoost = 22.0f;
        public string source = "META_QUEST_3S_VR";
    }

    [Serializable]
    public class CloudPlacedBuildingRequest
    {
        public string buildingId = "";
        public string buildingType = "";
        public int gridX;
        public int gridZ;
        public int rotationSteps;
    }

    /// <summary>
    /// Synchronizes the Unity Meta Quest 3S Digital Twin with the Dockerized FastAPI + Amazon DynamoDB
    /// + Reinforcement Learning Cloud Data Management Server.
    /// Automatically falls back to local simulation if the Docker server is not running.
    /// </summary>
    public class AgriTwinCloudSyncClient : MonoBehaviour
    {
        public static AgriTwinCloudSyncClient? Instance { get; private set; }

        [Header("Cloud Server Configuration")]
        public string apiBaseUrl = "http://localhost:8000";
        public string farmId = "MaeFahLuang_Greenhouse_01";
        public float pollIntervalSeconds = 2.5f;
        public bool enableCloudSync = true;

        [Header("Live Sync Diagnostics")]
        public bool isConnectedToCloud = false;
        public string activeStorageBackend = "Offline (Local Simulation)";
        public int rlPolicyIteration = 0;
        public float averageFarmReward = 0f;
        public CloudWeatherDto latestWeather = new CloudWeatherDto();

        public AgriTwinWorldController? worldController;

        private readonly Dictionary<string, CloudRLZoneRecDto> rlRecommendationsByZone =
            new Dictionary<string, CloudRLZoneRecDto>();

        private void Awake()
        {
            Instance = this;
            if (worldController == null)
            {
                worldController = GetComponent<AgriTwinWorldController>();
            }
        }

        private void Start()
        {
            StartCoroutine(PollCloudStateRoutine());
        }

        public CloudRLZoneRecDto? GetRLRecommendation(string zoneId)
        {
            if (rlRecommendationsByZone.TryGetValue(zoneId, out var rec))
            {
                return rec;
            }
            return null;
        }

        private IEnumerator PollCloudStateRoutine()
        {
            var wait = new WaitForSeconds(pollIntervalSeconds);
            while (true)
            {
                if (enableCloudSync && worldController != null)
                {
                    string url = $"{apiBaseUrl.TrimEnd('/')}/api/v1/farms/{farmId}/state";
                    using (UnityWebRequest req = UnityWebRequest.Get(url))
                    {
                        req.timeout = 2;
                        yield return req.SendWebRequest();

                        if (req.result == UnityWebRequest.Result.Success)
                        {
                            ApplyCloudStateJson(req.downloadHandler.text);
                        }
                        else
                        {
                            isConnectedToCloud = false;
                            activeStorageBackend = "Offline (Local Simulation)";
                        }
                    }
                }
                yield return wait;
            }
        }

        private void ApplyCloudStateJson(string json)
        {
            if (string.IsNullOrEmpty(json) || worldController == null) return;

            try
            {
                var resp = JsonUtility.FromJson<CloudFarmStateResponse>(json);
                if (resp == null || resp.zones == null || resp.zones.Count == 0) return;

                isConnectedToCloud = true;
                activeStorageBackend = resp.storageBackend;
                if (resp.weather != null)
                {
                    latestWeather = resp.weather;
                }

                if (resp.rlAdvisor != null)
                {
                    rlPolicyIteration = resp.rlAdvisor.policyIteration;
                    averageFarmReward = resp.rlAdvisor.averageFarmReward;
                    rlRecommendationsByZone.Clear();
                    if (resp.rlAdvisor.zoneRecommendations != null)
                    {
                        foreach (var rec in resp.rlAdvisor.zoneRecommendations)
                        {
                            if (!string.IsNullOrEmpty(rec.zoneId))
                            {
                                rlRecommendationsByZone[rec.zoneId] = rec;
                            }
                        }
                    }
                }

                var state = worldController.State;
                foreach (var cz in resp.zones)
                {
                    var cfg = state.zoneConfigs.Find(z => z.id == cz.id);
                    if (cfg != null)
                    {
                        cfg.liveMoisture = cz.moisture;
                        cfg.liveTemp = cz.temp;
                        cfg.ndvi = cz.ndvi;
                        cfg.liveWater = Mathf.Clamp(cz.waterUsage * 0.45f, 20f, 98f);
                        if (resp.weather != null && resp.weather.humidity > 0f)
                        {
                            cfg.liveHumidity = resp.weather.humidity;
                        }
                        int lastIdx = AgriTwinState.TotalSteps - 1;
                        cfg.history[lastIdx].moisture = cfg.liveMoisture;
                        cfg.history[lastIdx].temp = cfg.liveTemp;
                        cfg.history[lastIdx].humidity = cfg.liveHumidity;
                        cfg.history[lastIdx].water = cfg.liveWater;
                    }
                }

                worldController.NotifyStateChanged();
            }
            catch (Exception)
            {
                // Ignore malformed payload during server startup
            }
        }

        public void PushIrrigationCommand(string zoneId, float moistureBoost = 22.0f)
        {
            if (!enableCloudSync || !gameObject.activeInHierarchy) return;
            StartCoroutine(PostIrrigationCommandRoutine(zoneId, moistureBoost));
        }

        private IEnumerator PostIrrigationCommandRoutine(string zoneId, float moistureBoost)
        {
            string url = $"{apiBaseUrl.TrimEnd('/')}/api/v1/farms/{farmId}/commands";
            var payload = new CloudIrrigationCommandRequest
            {
                zoneId = zoneId,
                action = "OPEN_VALVE",
                durationSec = 900,
                moistureBoost = moistureBoost,
                source = UnityEngine.XR.XRSettings.isDeviceActive ? "META_QUEST_3S_VR" : "UNITY_DESKTOP_SIM"
            };
            string json = JsonUtility.ToJson(payload);
            byte[] bodyRaw = Encoding.UTF8.GetBytes(json);

            using (var req = new UnityWebRequest(url, "POST"))
            {
                req.uploadHandler = new UploadHandlerRaw(bodyRaw);
                req.downloadHandler = new DownloadHandlerBuffer();
                req.SetRequestHeader("Content-Type", "application/json");
                req.timeout = 3;
                yield return req.SendWebRequest();
            }
        }

        public void PushPlacedBuilding(string buildingId, string buildingType, int gridX, int gridZ, int rotationSteps)
        {
            if (!enableCloudSync || !gameObject.activeInHierarchy) return;
            StartCoroutine(PostPlacedBuildingRoutine(buildingId, buildingType, gridX, gridZ, rotationSteps));
        }

        private IEnumerator PostPlacedBuildingRoutine(string buildingId, string buildingType, int gridX, int gridZ, int rotationSteps)
        {
            string url = $"{apiBaseUrl.TrimEnd('/')}/api/v1/farms/{farmId}/buildings";
            var payload = new CloudPlacedBuildingRequest
            {
                buildingId = buildingId,
                buildingType = buildingType,
                gridX = gridX,
                gridZ = gridZ,
                rotationSteps = rotationSteps
            };
            string json = JsonUtility.ToJson(payload);
            byte[] bodyRaw = Encoding.UTF8.GetBytes(json);

            using (var req = new UnityWebRequest(url, "POST"))
            {
                req.uploadHandler = new UploadHandlerRaw(bodyRaw);
                req.downloadHandler = new DownloadHandlerBuffer();
                req.SetRequestHeader("Content-Type", "application/json");
                req.timeout = 3;
                yield return req.SendWebRequest();
            }
        }
    }
}
