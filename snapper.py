"""
snapper.py
Snaps an arbitrary (lat, lon) GPS coordinate onto the railway graph.

Strategy
--------
1. Build a node-to-component map during graph analysis.
2. When snapping, find the K nearest candidate nodes.
3. Among those, prefer nodes that share a component with the other endpoint
   (passed in as hint_comp), falling back to any node.
4. The router passes components around so both endpoints snap to the same
   connected subgraph before Dijkstra runs.

If no graph node is found within MAX_SEARCH_DEGREES: raises NoTrackFound.
"""

from __future__ import annotations

import math
from typing import Dict, FrozenSet, List, Optional, Set, Tuple

import networkx as nx
from pyproj import Geod

from graph_builder import RailwayGraph

_GEOD = Geod(ellps="WGS84")

# Degree radius to search on each attempt (~111 km per degree)
_SEARCH_RADII_DEG = [0.01, 0.05, 0.1, 0.5, 1.0, 2.0]
_K_NEAREST = 100          # vertices to retrieve per search level
_MAX_CANDIDATES = 500     # hard cap on total candidates examined


class NoTrackFound(Exception):
    pass


def _haversine_m(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    """Fast geodetic distance in metres (WGS-84)."""
    _, _, dist = _GEOD.inv(lon1, lat1, lon2, lat2)
    return abs(dist)


# ---------------------------------------------------------------------------
# Component index (built once, stored on RailwayGraph lazily)
# ---------------------------------------------------------------------------

def _ensure_component_index(rg: RailwayGraph) -> Dict[Tuple[float, float], int]:
    """
    Build (lazily) and return a dict  node -> component_index.
    Component 0 is always the largest connected component.
    """
    if hasattr(rg, "_node_to_comp"):
        return rg._node_to_comp  # type: ignore[attr-defined]

    comps = sorted(nx.connected_components(rg.graph), key=len, reverse=True)
    mapping: Dict[Tuple[float, float], int] = {}
    for i, comp in enumerate(comps):
        for n in comp:
            mapping[n] = i
    rg._node_to_comp = mapping          # type: ignore[attr-defined]
    rg._components   = comps            # type: ignore[attr-defined]
    return mapping


def snap_to_graph(
    lat: float,
    lon: float,
    rg: RailwayGraph,
    required_comp: Optional[int] = None,
) -> Tuple[Tuple[float, float], float, Tuple[float, float]]:
    """
    Find the nearest graph node to (lat, lon).

    Parameters
    ----------
    lat, lon       : GPS coordinate (WGS-84)
    rg             : RailwayGraph
    required_comp  : if not None, only nodes in this component are accepted.
                     The router passes this after snapping the first endpoint,
                     to guarantee both points sit in the same subgraph.

    Returns
    -------
    node_id        : (lon, lat) tuple — the graph node
    distance_m     : geodetic metres from input to node
    snapped_point  : same as node_id

    Raises NoTrackFound if nothing qualifies within the maximum radius.
    """
    graph      = rg.graph
    v_index    = rg.vertex_index
    v_list     = rg.vertex_list
    n2c        = _ensure_component_index(rg)

    best_node  = None
    best_dist  = math.inf
    examined: Set[int] = set()

    for radius in _SEARCH_RADII_DEG:
        bbox = (lon - radius, lat - radius, lon + radius, lat + radius)
        hits = list(v_index.nearest(bbox, _K_NEAREST))

        for vid in hits:
            if vid in examined:
                continue
            examined.add(vid)

            v_lon, v_lat = v_list[vid]
            node = (v_lon, v_lat)

            if node not in graph:
                continue

            # If a component constraint is active, filter
            if required_comp is not None and n2c.get(node) != required_comp:
                continue

            dist = _haversine_m(lon, lat, v_lon, v_lat)
            if dist < best_dist:
                best_dist = dist
                best_node = node

        if best_node is not None:
            break

        if len(examined) >= _MAX_CANDIDATES:
            break

    if best_node is None:
        raise NoTrackFound(
            f"No railway track found near ({lat:.4f}, {lon:.4f}) "
            f"within {_SEARCH_RADII_DEG[-1]}° "
            f"(component filter={required_comp})"
        )

    return best_node, best_dist, best_node


def snap_pair(
    start_lat: float, start_lng: float,
    end_lat: float,   end_lng: float,
    rg: RailwayGraph,
) -> Tuple[
    Tuple[float, float],
    float,
    Tuple[float, float],
    float,
]:
    """
    Snap start and end coordinates to the same connected component.

    Algorithm
    ---------
    1. Snap start with no component filter → get start_node and its component.
    2. Try to snap end in the SAME component.
    3. If that fails, try the next largest component containing a node near end,
       then re-snap start there.  Repeat for top-N components.

    Returns
    -------
    start_node, snap_start_dist_m, end_node, snap_end_dist_m
    """
    n2c   = _ensure_component_index(rg)
    comps = rg._components  # type: ignore[attr-defined]

    # Step 1: Snap start unconstrained
    start_node, s_dist, _ = snap_to_graph(start_lat, start_lng, rg)
    start_comp = n2c[start_node]

    # Step 2: Try to snap end into the same component
    try:
        end_node, e_dist, _ = snap_to_graph(
            end_lat, end_lng, rg, required_comp=start_comp
        )
        return start_node, s_dist, end_node, e_dist
    except NoTrackFound:
        pass  # Fall through to broader search

    # Step 3: For each of the largest K components, try snapping both endpoints
    K = min(20, len(comps))
    for ci in range(K):
        try:
            sn, sd, _ = snap_to_graph(start_lat, start_lng, rg, required_comp=ci)
            en, ed, _ = snap_to_graph(end_lat,   end_lng,   rg, required_comp=ci)
            return sn, sd, en, ed
        except NoTrackFound:
            continue

    # Give up — no shared component found
    raise NoTrackFound(
        "Could not snap both endpoints to any shared connected railway component."
    )


def nearest_track_info(
    lat: float,
    lon: float,
    rg: RailwayGraph,
) -> dict:
    """
    Return info about the nearest track segment for /nearest-track endpoint.
    """
    seg_index = rg.segment_index
    segments  = rg.segments

    best_seg     = None
    best_dist    = math.inf
    best_snapped = None

    for radius in _SEARCH_RADII_DEG:
        bbox = (lon - radius, lat - radius, lon + radius, lat + radius)
        hits = list(seg_index.nearest(bbox, 5))

        for sid in hits:
            seg    = segments[sid]
            coords = seg["coords"]
            for v_lon, v_lat in coords:
                dist = _haversine_m(lon, lat, v_lon, v_lat)
                if dist < best_dist:
                    best_dist    = dist
                    best_snapped = (v_lon, v_lat)
                    best_seg     = seg

        if best_seg is not None:
            break

    if best_seg is None:
        raise NoTrackFound(f"No railway segment near ({lat}, {lon})")

    return {
        "track_id":        best_seg["feature_id"],
        "name":            best_seg["name"],
        "distance_meters": round(best_dist, 2),
        "snapped_point":   [best_snapped[0], best_snapped[1]],
    }
