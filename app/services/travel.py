from __future__ import annotations

import json
import os
from datetime import datetime
from urllib import error, parse, request

from app.models import TravelEstimateRequest, TravelEstimateResponse
from app.services.settings import load_planner_config


def estimate_travel(payload: TravelEstimateRequest) -> TravelEstimateResponse:
    api_key = os.getenv("GOOGLE_MAPS_API_KEY", "").strip()
    if api_key:
        via_google = _estimate_via_google(payload, api_key)
        if via_google:
            return via_google

    fallback_minutes = int(load_planner_config().get("travel_defaults", {}).get("school_one_way_minutes", 30))
    return TravelEstimateResponse(
        provider="fallback",
        duration_minutes=fallback_minutes,
        status="estimated",
        detail="Google Maps API not configured or unavailable. Used planner default.",
    )


def _estimate_via_google(payload: TravelEstimateRequest, api_key: str) -> TravelEstimateResponse | None:
    departure = int((payload.departure_time or datetime.now()).timestamp())
    query = parse.urlencode(
        {
            "origins": payload.origin,
            "destinations": payload.destination,
            "mode": payload.mode,
            "departure_time": departure,
            "key": api_key,
            "language": "cs",
        }
    )
    url = f"https://maps.googleapis.com/maps/api/distancematrix/json?{query}"

    try:
        with request.urlopen(url, timeout=8) as resp:
            raw = json.loads(resp.read().decode("utf-8"))
    except (error.URLError, TimeoutError, json.JSONDecodeError):
        return None

    rows = raw.get("rows", [])
    if not rows:
        return None
    elements = rows[0].get("elements", [])
    if not elements:
        return None

    element = elements[0]
    duration = element.get("duration") or element.get("duration_in_traffic")
    if not duration:
        return None

    seconds = int(duration.get("value", 0))
    minutes = max(1, round(seconds / 60))
    return TravelEstimateResponse(
        provider="google_maps",
        duration_minutes=minutes,
        status="estimated",
        detail=duration.get("text"),
    )
