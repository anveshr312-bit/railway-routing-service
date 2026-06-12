"""
router.py
Railway routing along the NetworkX graph using Dijkstra's algorithm.

Given two (lat, lon) points:
  1. Snap BOTH to the same connected component (snap_pair).
  2. Run networkx.shortest_path (Dijkstra, weight='weight').
  3. Assemble the full coordinate geometry from edge 'geom' attributes.
  4. Return distance_meters + GeoJSON Feature.

If no path exists: raises RouteNotFound.
No straight-line fallback — ever.
"""

from __future__ import annotations

import logging
from typing import List, Tuple

import networkx as nx

from graph_builder import RailwayGraph
from snapper import NoTrackFound, snap_pair

log = logging.getLogger(__name__)


class RouteNotFound(Exception):
    pass


def _build_geojson(coords: List[Tuple[float, float]]) -> dict:
    return {
        "type": "Feature",
        "geometry": {
            "type": "LineString",
            "coordinates": [[lon, lat] for lon, lat in coords],
        },
        "properties": {},
    }


def _edge_coords(
    graph: nx.Graph,
    node_a: Tuple[float, float],
    node_b: Tuple[float, float],
) -> List[Tuple[float, float]]:
    """
    Return the coordinate list for edge (node_a, node_b) oriented a→b.
    """
    geom: List[Tuple[float, float]] = graph[node_a][node_b]["geom"]
    # Orient so geom starts at node_a
    if geom[0] == node_a or (abs(geom[0][0] - node_a[0]) < 1e-9 and
                              abs(geom[0][1] - node_a[1]) < 1e-9):
        return geom
    return list(reversed(geom))


def compute_route(
    start_lat: float,
    start_lng: float,
    end_lat: float,
    end_lng: float,
    rg: RailwayGraph,
) -> dict:
    """
    Compute the shortest railway route.

    Returns
    -------
    {
        "distance_meters": float,
        "geojson": GeoJSON Feature (LineString),
        "snap_start_dist_m": float,
        "snap_end_dist_m": float,
    }

    Raises
    ------
    NoTrackFound   – if neither endpoint can be snapped to a shared component.
    RouteNotFound  – if no path exists between the two snapped nodes.
    """
    # --- 1. Snap both endpoints to the same connected component ---
    try:
        start_node, snap_start_dist, end_node, snap_end_dist = snap_pair(
            start_lat, start_lng, end_lat, end_lng, rg
        )
    except NoTrackFound as exc:
        raise NoTrackFound(str(exc)) from exc

    log.info(
        "Routing %s → %s  (snap Δ %.1f m / %.1f m)",
        start_node, end_node, snap_start_dist, snap_end_dist,
    )

    if start_node == end_node:
        lon, lat = start_node
        return {
            "distance_meters":   0.0,
            "geojson":           _build_geojson([(lon, lat)]),
            "snap_start_dist_m": round(snap_start_dist, 2),
            "snap_end_dist_m":   round(snap_end_dist, 2),
            "snapped_start":     [lon, lat],
            "snapped_end":       [lon, lat],
            "eta_minutes":       0.0,
        }

    # --- 2. Dijkstra ---
    graph = rg.graph
    try:
        node_path: List[Tuple[float, float]] = nx.shortest_path(
            graph, start_node, end_node, weight="weight"
        )
        path_length_m: float = nx.shortest_path_length(
            graph, start_node, end_node, weight="weight"
        )
    except nx.NetworkXNoPath:
        raise RouteNotFound(
            f"No railway path between {start_node} and {end_node}."
        )
    except nx.NodeNotFound as exc:
        raise RouteNotFound(str(exc))

    # --- 3. Assemble geometry ---
    full_coords: List[Tuple[float, float]] = []
    for i in range(len(node_path) - 1):
        a = node_path[i]
        b = node_path[i + 1]
        seg = _edge_coords(graph, a, b)

        if i == 0:
            full_coords.extend(seg)
        else:
            # Drop duplicate junction point
            if full_coords and seg and full_coords[-1] == seg[0]:
                full_coords.extend(seg[1:])
            else:
                full_coords.extend(seg)

    if not full_coords:
        full_coords = [start_node, end_node]

    TRAIN_SPEED_KMPH = 60.0
    return {
        "distance_meters":   round(path_length_m, 2),
        "geojson":           _build_geojson(full_coords),
        "snap_start_dist_m": round(snap_start_dist, 2),
        "snap_end_dist_m":   round(snap_end_dist, 2),
        "snapped_start":     [full_coords[0][0], full_coords[0][1]],
        "snapped_end":       [full_coords[-1][0], full_coords[-1][1]],
        "eta_minutes":       round((path_length_m / 1000.0) / TRAIN_SPEED_KMPH * 60.0, 2),
    }
