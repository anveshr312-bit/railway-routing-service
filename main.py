"""
main.py
FastAPI railway routing server.

Endpoints
---------
GET  /            — health check
POST /rail-route  — shortest path between two lat/lng points
POST /nearest-track — snap a lat/lng to the nearest track segment
"""

from __future__ import annotations

import logging
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from graph_builder import RailwayGraph, build_graph
from router import RouteNotFound, compute_route
from snapper import NoTrackFound, nearest_track_info

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# App state
# ---------------------------------------------------------------------------
_rg: RailwayGraph | None = None

GPKG_PATH = Path(
    os.environ.get("RAILWAYS_GPKG", "/data/railways.gpkg")
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _rg
    log.info("Loading railway network from %s …", GPKG_PATH)
    t0 = time.perf_counter()
    _rg = build_graph(GPKG_PATH)
    log.info("Railway graph ready in %.1f s.", time.perf_counter() - t0)
    yield
    _rg = None


# ---------------------------------------------------------------------------
# Application
# ---------------------------------------------------------------------------
app = FastAPI(
    title="Railway Routing Engine",
    description="Shortest-path routing along Indian railway network (HOTOSM data).",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def _require_graph() -> RailwayGraph:
    if _rg is None:
        raise HTTPException(503, detail="Railway graph not yet loaded.")
    return _rg


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------

class RouteRequest(BaseModel):
    start_lat: float = Field(..., description="ART train latitude", example=13.0827)
    start_lng: float = Field(..., description="ART train longitude", example=80.2707)
    end_lat: float   = Field(..., description="Incident latitude",  example=13.1986)
    end_lng: float   = Field(..., description="Incident longitude", example=80.1760)


class RouteResponse(BaseModel):
    distance_meters: float
    snap_start_dist_m: float
    snap_end_dist_m: float
    snapped_start: list
    snapped_end: list
    eta_minutes: float
    geojson: dict


class NearestTrackRequest(BaseModel):
    lat: float = Field(..., example=13.0827)
    lng: float = Field(..., example=80.2707)


class NearestTrackResponse(BaseModel):
    track_id: str
    name: str
    distance_meters: float
    snapped_point: list


class ErrorResponse(BaseModel):
    error: str
    detail: str | None = None


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/", tags=["health"])
def health():
    rg = _rg
    if rg is None:
        return {"status": "loading"}
    return {
        "status": "ok",
        "nodes": rg.graph.number_of_nodes(),
        "edges": rg.graph.number_of_edges(),
    }


@app.post(
    "/rail-route",
    response_model=RouteResponse,
    responses={404: {"model": ErrorResponse}, 422: {"model": ErrorResponse}},
    tags=["routing"],
    summary="Shortest railway path between two coordinates",
)
def rail_route(req: RouteRequest):
    """
    Returns the shortest path along the railway network between the ART
    train position and the incident location.

    - Both coordinates are snapped to the nearest graph node.
    - Dijkstra's algorithm finds the shortest weighted path.
    - Returns full GeoJSON LineString geometry and distance in metres.
    - Returns `{"error": "ROUTE_NOT_FOUND"}` when no path exists.
    """
    rg = _require_graph()

    try:
        result = compute_route(
            req.start_lat, req.start_lng,
            req.end_lat,   req.end_lng,
            rg,
        )
    except NoTrackFound as exc:
        raise HTTPException(
            status_code=404,
            detail={"error": "TRACK_NOT_FOUND", "detail": str(exc)},
        )
    except RouteNotFound as exc:
        raise HTTPException(
            status_code=404,
            detail={"error": "ROUTE_NOT_FOUND", "detail": str(exc)},
        )

    return RouteResponse(**result)


@app.post(
    "/nearest-track",
    response_model=NearestTrackResponse,
    responses={404: {"model": ErrorResponse}},
    tags=["snapping"],
    summary="Snap a coordinate to the nearest railway track",
)
def nearest_track(req: NearestTrackRequest):
    """
    Finds the nearest railway segment to the given coordinate and returns
    the snapped point, track id, and distance.
    """
    rg = _require_graph()

    try:
        info = nearest_track_info(req.lat, req.lng, rg)
    except NoTrackFound as exc:
        raise HTTPException(
            status_code=404,
            detail={"error": "TRACK_NOT_FOUND", "detail": str(exc)},
        )

    return NearestTrackResponse(**info)

@app.get(
    "/debug-map",
    tags=["debug"],
    summary="Get full debug map GeoJSON FeatureCollection",
)
def debug_map(start_lat: float, start_lng: float, end_lat: float, end_lng: float):
    """
    Returns a full GeoJSON FeatureCollection containing:
    - Start point
    - End point
    - Snapped start point
    - Snapped end point
    - Railway route
    """
    rg = _require_graph()

    try:
        result = compute_route(
            start_lat, start_lng,
            end_lat,   end_lng,
            rg,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    features = []
    
    # 1. Start point
    features.append({
        "type": "Feature",
        "geometry": {
            "type": "Point",
            "coordinates": [start_lng, start_lat]
        },
        "properties": {"name": "Start Request", "marker-color": "#ff0000"}
    })
    
    # 2. End point
    features.append({
        "type": "Feature",
        "geometry": {
            "type": "Point",
            "coordinates": [end_lng, end_lat]
        },
        "properties": {"name": "End Request", "marker-color": "#ff0000"}
    })
    
    # 3. Snapped Start point
    features.append({
        "type": "Feature",
        "geometry": {
            "type": "Point",
            "coordinates": result["snapped_start"]
        },
        "properties": {"name": "Snapped Start", "marker-color": "#00ff00"}
    })
    
    # 4. Snapped End point
    features.append({
        "type": "Feature",
        "geometry": {
            "type": "Point",
            "coordinates": result["snapped_end"]
        },
        "properties": {"name": "Snapped End", "marker-color": "#00ff00"}
    })
    
    # 5. Railway Route
    route_feature = result["geojson"]
    route_feature["properties"] = {
        "name": "Railway Route",
        "distance_meters": result["distance_meters"],
        "eta_minutes": result["eta_minutes"]
    }
    features.append(route_feature)

    return {
        "type": "FeatureCollection",
        "features": features
    }
