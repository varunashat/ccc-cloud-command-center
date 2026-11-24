"""Bounded forecast invocation through the deployed Azure Foundry agent."""
import json
import math
from datetime import datetime, timezone
from threading import BoundedSemaphore
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from azure.identity import ManagedIdentityCredential

ENDPOINT = "https://ccc-weather-shj-1a5492.services.ai.azure.com/api/projects/weather-forecast/agents/weather-forecast/endpoint/protocols/openai/responses?api-version=v1"
GATE = BoundedSemaphore(1)


def request(url, payload=None, token=None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    raw = json.dumps(payload).encode() if payload is not None else None
    with urlopen(Request(url, data=raw, headers=headers), timeout=60) as response:
        return json.load(response)


def forecast(body, acquire_token=None):
    prompt = body.get("prompt")
    if body.get("consent") is not True:
        raise ValueError("Confirm the Azure model request.")
    if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 800:
        raise ValueError("Enter a location and forecast question, up to 800 characters.")
    if not GATE.acquire(blocking=False):
        raise ValueError("Another forecast is running. Try again shortly.")
    try:
        token = acquire_token() if acquire_token else ManagedIdentityCredential().get_token("https://ai.azure.com/.default").token
        result = request(ENDPOINT, {"input": prompt, "max_output_tokens": 1800}, token)
        sources = []
        usage = []
        for _ in range(3):
            usage.append(result.get("usage", {}))
            calls = [item for item in result.get("output", []) if item.get("type") == "function_call"]
            if not calls:
                break
            outputs = []
            for item in calls:
                if item.get("name") != "get_forecast" or len(outputs) >= 2:
                    raise ValueError("Forecast tool limit reached.")
                args = json.loads(item["arguments"])
                lat, lon = args.get("latitude"), args.get("longitude")
                if any(type(n) not in (float, int) or not math.isfinite(n) for n in (lat, lon)):
                    raise ValueError("Invalid coordinates.")
                if not -90 <= lat <= 90 or not -180 <= lon <= 180:
                    raise ValueError("Coordinates outside allowed range.")
                params = dict(latitude=lat, longitude=lon, timezone="auto", forecast_days=3,
                              daily="temperature_2m_max,temperature_2m_min,precipitation_probability_max,weather_code")
                url = "https://api.open-meteo.com/v1/forecast?" + urlencode(params)
                data = request(url)
                sources.append(url)
                outputs.append({"type": "function_call_output", "call_id": item["call_id"],
                                "output": json.dumps({"forecast": data, "source": url})})
            result = request(ENDPOINT, {"previous_response_id": result["id"], "input": outputs,
                                       "max_output_tokens": 1800}, token)
        else:
            raise ValueError("Forecast did not finish within the tool limit.")
        answer = "\n".join(part.get("text", "") for item in result.get("output", [])
                           if item.get("type") == "message" for part in item.get("content", []))
        if not answer:
            raise ValueError("No forecast answer was returned.")
        return {"answer": answer, "sources": sources, "usage": usage,
                "captured_at": datetime.now(timezone.utc).isoformat()}
    finally:
        GATE.release()
