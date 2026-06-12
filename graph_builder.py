"""
graph_builder.py
Loads railways.gpkg (LineString features only) and builds a NetworkX graph.

Node  = (lon, lat) tuple — every unique endpoint of every segment.
Edge  = (node_a, node_b, {weight: metres, geom: [...coords]})
       — one edge per LineString segment, weight = geodetic length in metres.

The module also builds an rtree spatial index over ALL vertices (not just
endpoints) so that the snapper can find the nearest track point even for
GPS coordinates that sit several hundred metres off the centreline.
"""

from __future__ import annotations

import logging
import math
from pathlib import Path
from typing import Dict, List, Tuple

import geopandas as gpd
import networkx as nx
import numpy as np
from pyproj import Geod
from rtree import index as rtree_index
from shapely.geometry import LineString, Point

log = logging.getLogger(__name__)

# Geodetic calculator (WGS-84)
_GEOD = Geod(ellps="WGS84")

# Coordinate precision for node de-duplication (≈ 1 cm at equator)
_COORD_PRECISION = 6


def _round(coord: Tuple[float, float]) -> Tuple[float, float]:
    return (round(coord[0], _COORD_PRECISION), round(coord[1], _COORD_PRECISION))


def haversine_dist(lon1, lat1, lon2, lat2):
    R = 6371000.0  # radius of Earth in meters
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2.0)**2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2.0)**2
    return 2.0 * R * math.asin(math.sqrt(a))

def _geodetic_length_m(coords: List[Tuple[float, float]]) -> float:
    """Return geodetic length of a polyline (lon, lat pairs) in metres."""
    if len(coords) < 2:
        return 0.0
    dist = 0.0
    for i in range(len(coords) - 1):
        dist += haversine_dist(coords[i][0], coords[i][1], coords[i+1][0], coords[i+1][1])
    return dist


# ---------------------------------------------------------------------------
# Public data structures returned by build_graph()
# ---------------------------------------------------------------------------

class RailwayGraph:
    """Container for the built graph and its spatial index."""

    def __init__(
        self,
        graph: nx.Graph,
        node_coords: Dict[Tuple[float, float], Tuple[float, float]],
        vertex_index: rtree_index.Index,
        vertex_list: List[Tuple[float, float]],
        segment_index: rtree_index.Index,
        segments: List[dict],
    ):
        self.graph = graph
        # node_coords: node_id -> (lon, lat)  [identity mapping here, but
        # kept explicit so callers never need to guess]
        self.node_coords = node_coords
        # All vertices across ALL geometries (for nearest-point snapping)
        self.vertex_index = vertex_index          # rtree index id -> vertex_list[id]
        self.vertex_list = vertex_list            # [(lon, lat), ...]
        # Segment-level spatial index (bounding boxes of each LineString)
        self.segment_index = segment_index        # rtree index id -> segments[id]
        self.segments = segments                  # list of {id, coords, start_node, end_node}

    # Convenience: look up a graph node's (lon, lat) by its id
    def node_lonlat(self, node_id: Tuple[float, float]) -> Tuple[float, float]:
        return node_id   # nodes ARE (lon, lat) tuples


def build_graph(gpkg_path: str | Path) -> RailwayGraph:
    """
    Load *gpkg_path*, extract LineString railway features, build and return
    a RailwayGraph.  Raises FileNotFoundError / ValueError on bad input.
    """
    gpkg_path = Path(gpkg_path)
    if not gpkg_path.exists():
        raise FileNotFoundError(gpkg_path)

    log.info("Loading %s …", gpkg_path)
    gdf = gpd.read_file(gpkg_path, layer="railways")

    # Keep only LineStrings
    lines = gdf[gdf.geom_type == "LineString"].copy()
    if lines.empty:
        raise ValueError("No LineString features found in railways layer.")
    log.info("  %d LineString segments loaded.", len(lines))

    # Build NetworkX undirected graph
    G = nx.Graph()
    node_coords: Dict[Tuple[float, float], Tuple[float, float]] = {}

    # Vertex spatial index (all coords in all segments)
    vertex_list: List[Tuple[float, float]] = []
    vi_props = rtree_index.Property()
    vi_props.dimension = 2

    # Segment spatial index (bounding boxes)
    si_props = rtree_index.Property()
    si_props.dimension = 2
    segments: List[dict] = []
    segments: List[dict] = []

    log.info("Building graph …")

    seg_id = 0
    for _, row in lines.iterrows():
        geom: LineString = row.geometry
        coords = [_round(c[:2]) for c in geom.coords]   # drop Z if present

        if len(coords) < 2:
            continue

        # Register ALL nodes and pairwise edges
        for i in range(len(coords) - 1):
            start_node = coords[i]
            end_node = coords[i+1]
            
            for node in (start_node, end_node):
                if node not in node_coords:
                    node_coords[node] = node
                    G.add_node(node)
                    
            length_m = _geodetic_length_m([start_node, end_node])
            
            # Add (or relax) edge
            if G.has_edge(start_node, end_node):
                existing = G[start_node][end_node]
                if length_m < existing["weight"]:
                    G[start_node][end_node]["weight"] = length_m
                    G[start_node][end_node]["geom"] = [start_node, end_node]
                    G[start_node][end_node]["seg_id"] = seg_id
            else:
                G.add_edge(
                    start_node,
                    end_node,
                    weight=length_m,
                    geom=[start_node, end_node],
                    seg_id=seg_id,
                )

        # Accumulate ALL vertices for snapping
        for lon, lat in coords:
            vertex_list.append((lon, lat))

        # Accumulate segment bounding box
        lons = [c[0] for c in coords]
        lats = [c[1] for c in coords]
        seg_bbox = (min(lons), min(lats), max(lons), max(lats))
        segments.append(
            {
                "id": seg_id,
                "bbox": seg_bbox,
                "coords": coords,
                "start_node": start_node,
                "end_node": end_node,
                "feature_id": str(row.get("id", seg_id)),
                "name": str(row.get("name", "")),
            }
        )
        seg_id += 1

    log.info("Bulk-loading spatial indices...")
    def vertex_gen():
        for vid, (lon, lat) in enumerate(vertex_list):
            yield (vid, (lon, lat, lon, lat), None)
            
    vertex_idx = rtree_index.Index(vertex_gen(), properties=vi_props)

    def seg_gen():
        for seg in segments:
            yield (seg["id"], seg["bbox"], None)
            
    seg_idx = rtree_index.Index(seg_gen(), properties=si_props)

    log.info(
        "Graph ready: %d nodes, %d edges, %d vertices indexed.",
        G.number_of_nodes(),
        G.number_of_edges(),
        len(vertex_list),
    )

    return RailwayGraph(
        graph=G,
        node_coords=node_coords,
        vertex_index=vertex_idx,
        vertex_list=vertex_list,
        segment_index=seg_idx,
        segments=segments,
    )
