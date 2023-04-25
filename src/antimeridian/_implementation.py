"""Implementation module for the antimeridian package.

This is a "private" module that is not part of our public API. Downstream users
should not use these functions and objects directly; instead, use the functions
explicitly imported into the top-level of the package. The interfaces in this
module can change at any time without warning.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Protocol, Tuple, Union, cast

import shapely.geometry
from shapely.geometry import MultiPolygon, Polygon

Point = Tuple[float, float]


class GeoInterface(Protocol):
    """A simple protocol for things that have a ``__geo_interface__`` method.

    The ``__geo_interface__`` protocol is described `here
    <https://gist.github.com/sgillies/2217756>`_, and is used within `shapely
    <https://shapely.readthedocs.io/en/stable/manual.html>`_ to
    extract geometries from objects.
    """

    def __geo_interface__(self) -> Dict[str, Any]:
        ...


def fix_geojson(geojson: Dict[str, Any]) -> Dict[str, Any]:
    """Fixes GeoJSON object that crosses the antimeridian.

    If the object does not cross the antimeridian, it is returned unchanged.

    Args:
        geojson: A GeoJSON object as a dictionary

    Return:
        The same GeoJSON with a fixed geometry or geometries
    """
    type_ = geojson.get("type", None)
    if type_ is None:
        raise ValueError("no 'type' field found in GeoJSON")
    elif type_ == "Feature":
        geometry = geojson.get("geometry", None)
        if geometry is None:
            raise ValueError("no 'geometry' field found in GeoJSON Feature")
        geojson["geometry"] = fix_shape(geometry)
        return geojson
    elif type_ == "FeatureCollection":
        features = geojson.get("features", None)
        if features is None:
            raise ValueError("no 'features' field found in GeoJSON FeatureCollection")
        for i, feature in enumerate(features):
            features[i] = fix_geojson(feature)
        geojson["features"] = features
        return geojson
    else:
        return fix_shape(geojson)


def fix_shape(shape: Dict[str, Any] | GeoInterface) -> Dict[str, Any]:
    """Fixes a shape that crosses the antimeridian.

    Args:
        shape: A polygon or multi-polygon, either as a dictionary or as a
            :py:class:`GeoInterface`. Uses :py:func:`shapely.geometry.shape`
            under the hood.

    Returns:
        The fixed shape as a dictionary
    """
    geom = shapely.geometry.shape(shape)
    if geom.geom_type == "Polygon":
        return cast(Dict[str, Any], shapely.geometry.mapping(fix_polygon(geom)))
    elif geom.geom_type == "MultiPolygon":
        return cast(Dict[str, Any], shapely.geometry.mapping(fix_multi_polygon(geom)))
    else:
        raise ValueError(f"unsupported geom_type: {geom.geom_type}")


def fix_multi_polygon(multi_polygon: MultiPolygon) -> MultiPolygon:
    """Fixes a :py:class:`shapely.geometry.MultiPolygon`.

    Args:
        multi_polygon: The multi-polygon

    Returns:
        The fixed multi-polygon
    """
    polygons = list()
    for polygon in multi_polygon.geoms:
        polygons += fix_polygon_to_list(polygon)
    return MultiPolygon(polygons)


def fix_polygon(polygon: Polygon) -> Union[Polygon, MultiPolygon]:
    """Fixes a :py:class:`shapely.geometry.Polygon`.

    Args:
        polygon: The input polygon

    Returns:
        The fixed polygon, either as a single polygon or a multi-polygon (if it
        was split)
    """
    polygons = fix_polygon_to_list(polygon)
    if len(polygons) == 1:
        return polygons[0]
    else:
        return MultiPolygon(polygons)


def fix_polygon_to_list(polygon: Polygon) -> List[Polygon]:
    segments = segment(list(polygon.exterior.coords))
    if not segments:
        return [polygon]
    else:
        interiors = []
        for interior in polygon.interiors:
            interior_segments = segment(list(interior.coords))
            if interior_segments:
                segments.extend(interior_segments)
            else:
                interiors.append(interior)
    segments = extend_over_poles(segments)
    polygons = build_polygons(segments)
    assert polygons
    for i, polygon in enumerate(polygons):
        for j, interior in enumerate(interiors):
            if polygon.contains(interior):
                interior = interiors.pop(j)
                polygon_interiors = list(polygon.interiors)
                polygon_interiors.append(interior)
                polygons[i] = Polygon(polygon.exterior, polygon_interiors)
    assert not interiors
    return polygons


def segment(coords: List[Point]) -> List[List[Point]]:
    segment = []
    segments = []
    for i, point in enumerate(coords):
        coords[i] = (((point[0] + 180) % 360) - 180, point[1])
    for start, end in zip(coords, coords[1:]):
        segment.append(start)
        if (end[0] - start[0] > 180) and (end[0] - start[0] != 360):  # left
            latitude = crossing_latitude(start, end)
            segment.append((-180, latitude))
            segments.append(segment)
            segment = [(180, latitude)]
        elif (start[0] - end[0] > 180) and (start[0] - end[0] != 360):  # right
            latitude = crossing_latitude(end, start)
            segment.append((180, latitude))
            segments.append(segment)
            segment = [(-180, latitude)]
    if not segments:
        # No antimeridian crossings
        return []
    elif coords[-1] == segments[0][0]:
        # Join polygons
        segments[0] = segment + segments[0]
    return segments


def crossing_latitude(start: Point, end: Point) -> float:
    latitude_delta = end[1] - start[1]
    if end[0] > 0:
        return round(
            start[1]
            + (180.0 - start[0]) * latitude_delta / (end[0] + 360.0 - start[0]),
            7,
        )
    else:
        return round(
            start[1]
            + (start[0] + 180.0) * latitude_delta / (start[0] + 360.0 - end[0]),
            7,
        )


def extend_over_poles(segments: List[List[Point]]) -> List[List[Point]]:
    left_starts = list()
    right_starts = list()
    left_ends = list()
    right_ends = list()
    for i, segment in enumerate(segments):
        if segment[0][0] == -180:
            left_starts.append((i, segment[0][1]))
        else:
            right_starts.append((i, segment[0][1]))
        if segment[-1][0] == -180:
            left_ends.append((i, segment[-1][1]))
        else:
            right_ends.append((i, segment[-1][1]))
    left_ends.sort(key=lambda v: v[1])
    left_starts.sort(key=lambda v: v[1])
    right_ends.sort(key=lambda v: v[1], reverse=True)
    right_starts.sort(key=lambda v: v[1], reverse=True)
    if left_ends and (not left_starts or left_ends[0][1] < left_starts[0][1]):
        segments[left_ends[0][0]] += [(-180, -90), (180, -90)]
    if right_ends and (not right_starts or right_ends[0][1] > right_starts[0][1]):
        segments[right_ends[0][0]] += [(180, 90), (-180, 90)]
    return segments


def build_polygons(
    segments: List[List[Point]],
) -> List[Polygon]:
    if not segments:
        return []
    segment = segments.pop()
    right = (
        segment[-1][0] == 180
    )  # all segments should start and end at abs(180) longitude
    candidates: List[
        Tuple[Optional[int], float, bool]
    ] = list()  # list of (index, latitude, is_start)
    if segment[0][0] == segment[-1][0] and (
        (right and segment[0][1] > segment[-1][1])
        or (not right and segment[0][1] < segment[-1][1])
    ):
        candidates.append((None, segment[0][1], True))
    for i, s in enumerate(segments):
        if s[0][0] == segment[-1][0]:
            if (right and s[0][1] > segment[-1][1]) or (
                not right and s[0][1] < segment[-1][1]
            ):
                candidates.append((i, s[0][1], True))
        if s[-1][0] == segment[-1][0]:
            if (right and s[-1][1] > segment[-1][1]) or (
                not right and s[-1][1] < segment[-1][1]
            ):
                candidates.append((i, s[-1][1], False))
    candidates.sort(key=lambda c: c[1], reverse=not right)
    if candidates and candidates[0][2]:
        index = candidates[0][0]
    else:
        index = None

    if index is not None:
        segment = segments.pop(index) + segment
        segments.append(segment)
        return build_polygons(segments)
    else:
        polygons = build_polygons(segments)
        polygons.append(Polygon(segment))
        return polygons
