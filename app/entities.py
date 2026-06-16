from typing import Any

from ezdxf.math import Vec2, Vec3, Matrix44

from app.geometry import safe_dxf


def serialize_entity(entity: Any) -> dict:
    """Serialize a DXF entity to a JSON-safe dict."""
    data: dict[str, Any] = {"type": entity.dxftype()}
    for attr, value in entity.dxf.all_existing_dxf_attribs().items():
        if isinstance(value, (Vec2, Vec3)):
            data[attr] = [value.x, value.y, value.z if hasattr(value, 'z') else 0.0]
        elif isinstance(value, (tuple, list)) and len(value) in (2, 3):
            data[attr] = [float(v) for v in value]
        elif isinstance(value, (float, int)):
            data[attr] = value
        elif isinstance(value, str):
            data[attr] = value
        elif isinstance(value, bool):
            data[attr] = value
        elif value is None:
            data[attr] = None
        elif isinstance(value, Matrix44):
            data[attr] = str(value)
        elif hasattr(value, '__iter__') and not isinstance(value, (str, bytes)):
            try:
                data[attr] = [float(v) if isinstance(v, (int, float)) else str(v) for v in value]
            except Exception:
                data[attr] = str(value)
        else:
            try:
                if isinstance(value, (int, float)):
                    data[attr] = value
            except Exception:
                data[attr] = str(value)
    vc = getattr(entity, 'vertices', None)
    if vc is not None:
        try:
            verts = list(vc)
            data['_vertices'] = [
                [v.dxf.location.x, v.dxf.location.y,
                 v.dxf.location.z if hasattr(v.dxf.location, 'z') else 0.0]
                for v in verts
            ]
        except Exception:
            pass
    lwp = getattr(entity, 'get_points', None)
    if lwp is not None:
        try:
            pts = list(entity.get_points(format='xy'))
            data['_lwpoints'] = [[float(p[0]), float(p[1])] for p in pts]
        except Exception:
            pass
    return data


def _prepare_dxfattribs(data: dict, skip: set) -> dict:
    attr_keys = {"layer", "color", "linetype", "lineweight", "transparency",
                 "height", "char_height", "rotation", "style", "width",
                 "text", "name", "dimstyle", "dimtype", "geometry",
                 "hatch_style", "pattern_name", "solid_fill", "associative",
                 "degree", "closed", "periodic", "xscale", "yscale", "zscale",
                 "radius", "start_angle", "end_angle", "ratio",
                 "start_param", "end_param", "close", "flag"}
    geom_keys = {"vtx0", "vtx1", "vtx2", "vtx3"}
    attribs = {}
    for key, value in data.items():
        if key in skip or key in attr_keys or key in geom_keys:
            continue
        if isinstance(value, (int, float, str, bool)):
            attribs[key] = value
    for ak in attr_keys:
        if ak in data and ak not in skip:
            attribs[ak] = data[ak]
    return attribs


def _get_vec(value: Any) -> Any:
    if isinstance(value, (list, tuple)):
        if len(value) >= 3:
            return Vec3(value[0], value[1], value[2])
        elif len(value) >= 2:
            return Vec2(value[0], value[1])
    return value


def deserialize_entity(doc: Any, data: dict, target_layout: Any) -> Any:
    """Create a new entity from serialized data using ezdxf factory methods."""
    et = data["type"]
    skip = {"type", "_vertices", "_lwpoints"}
    attribs = _prepare_dxfattribs(data, skip)

    try:
        if et == "LINE":
            s = _get_vec(data.get("start", (0, 0)))
            e = _get_vec(data.get("end", (0, 0)))
            return target_layout.add_line(s, e, dxfattribs=attribs)
        elif et == "CIRCLE":
            c = _get_vec(data.get("center", (0, 0)))
            r = data.get("radius", 1)
            return target_layout.add_circle(c, r, dxfattribs=attribs)
        elif et == "ARC":
            c = _get_vec(data.get("center", (0, 0)))
            r = data.get("radius", 1)
            sa = data.get("start_angle", 0)
            ea = data.get("end_angle", 360)
            return target_layout.add_arc(c, r, sa, ea, dxfattribs=attribs)
        elif et == "TEXT":
            ins = _get_vec(data.get("insert", (0, 0)))
            text = data.get("text", "")
            height = data.get("height", 10)
            return target_layout.add_text(text, height, dxfattribs={
                **attribs, "insert": ins})
        elif et == "MTEXT":
            ins = _get_vec(data.get("insert", (0, 0)))
            text = data.get("text", "")
            ch = data.get("char_height", 10)
            return target_layout.add_mtext(text, dxfattribs={
                **attribs, "insert": ins, "char_height": ch})
        elif et == "POINT":
            loc = _get_vec(data.get("location", (0, 0)))
            return target_layout.add_point(loc, dxfattribs=attribs)
        elif et == "ELLIPSE":
            c = _get_vec(data.get("center", (0, 0)))
            ma = _get_vec(data.get("major_axis", (1, 0)))
            ratio = data.get("ratio", 1)
            return target_layout.add_ellipse(c, ma, ratio, dxfattribs=attribs)
        elif et == "SOLID":
            v0 = _get_vec(data.get("vtx0", (0, 0)))
            v1 = _get_vec(data.get("vtx1", (1, 0)))
            v2 = _get_vec(data.get("vtx2", (0, 1)))
            v3 = _get_vec(data.get("vtx3", (0, 1)))
            return target_layout.add_solid([v0, v1, v2, v3], dxfattribs=attribs)
        elif et == "TRACE":
            v0 = _get_vec(data.get("vtx0", (0, 0)))
            v1 = _get_vec(data.get("vtx1", (1, 0)))
            v2 = _get_vec(data.get("vtx2", (0, 1)))
            v3 = _get_vec(data.get("vtx3", (0, 1)))
            return target_layout.add_trace([v0, v1, v2, v3], dxfattribs=attribs)
        elif et == "3DFACE":
            v0 = _get_vec(data.get("vtx0", (0, 0)))
            v1 = _get_vec(data.get("vtx1", (1, 0)))
            v2 = _get_vec(data.get("vtx2", (0, 1)))
            v3 = _get_vec(data.get("vtx3", (0, 1)))
            return target_layout.add_3dface([v0, v1, v2, v3], dxfattribs=attribs)
        elif et == "LWPOLYLINE":
            pts_data = data.get("_lwpoints") or data.get("points", [])
            points = [Vec2(p[0], p[1]) for p in pts_data
                      if isinstance(p, (list, tuple)) and len(p) >= 2]
            close = bool(data.get("close", False))
            return target_layout.add_lwpolyline(points, close=close, dxfattribs=attribs)
        elif et == "POLYLINE":
            verts_data = data.get("_vertices", [])
            points = [Vec3(v[0], v[1], v[2] if len(v) > 2 else 0)
                      for v in verts_data if isinstance(v, (list, tuple)) and len(v) >= 2]
            close = bool(data.get("close", False))
            return target_layout.add_polyline(points, close=close, dxfattribs=attribs)
        elif et == "HATCH":
            return target_layout.add_hatch(dxfattribs=attribs)
        elif et == "DIMENSION":
            from ezdxf.entities import factory
            dim = factory.create_db_entry("DIMENSION", attribs, doc)
            if dim is not None:
                target_layout.add_entity(dim)
            return dim
        elif et == "SPLINE":
            cp = data.get("control_points", [])
            fp = data.get("fit_points", [])
            degree = data.get("degree", 3)
            closed = bool(data.get("closed", False))
            periodic = bool(data.get("periodic", False))
            cp_vec = [Vec2(p[0], p[1]) for p in cp
                      if isinstance(p, (list, tuple)) and len(p) >= 2]
            fp_vec = [Vec2(p[0], p[1]) for p in fp
                      if isinstance(p, (list, tuple)) and len(p) >= 2]
            spline = target_layout.add_spline(
                fit_points=fp_vec if fp_vec else None,
                dxfattribs=attribs
            )
            if cp_vec:
                spline.dxf.control_points = cp_vec
            if degree:
                spline.dxf.degree = degree
            spline.closed = closed
            spline.periodic = periodic
            return spline
        elif et == "INSERT":
            name = data.get("name", "")
            ins = _get_vec(data.get("insert", (0, 0)))
            return target_layout.add_blockref(name, ins, dxfattribs=attribs)
        elif et == "RAY":
            start = _get_vec(data.get("start", (0, 0)))
            uv = _get_vec(data.get("unit_vector", (1, 0)))
            return target_layout.add_ray(start, uv, dxfattribs=attribs)
        elif et == "XLINE":
            start = _get_vec(data.get("start", (0, 0)))
            uv = _get_vec(data.get("unit_vector", (1, 0)))
            return target_layout.add_xline(start, uv, dxfattribs=attribs)
        elif et == "VIEWPORT":
            c = _get_vec(data.get("center", (0, 0)))
            w = data.get("width", 100)
            h = data.get("height", 100)
            return target_layout.add_viewport(c, w, h, dxfattribs=attribs)
        elif et == "WIPEOUT":
            v0 = _get_vec(data.get("vtx0", (0, 0)))
            v1 = _get_vec(data.get("vtx1", (1, 0)))
            v2 = _get_vec(data.get("vtx2", (0, 1)))
            v3 = _get_vec(data.get("vtx3", (0, 1)))
            return target_layout.add_wipeout(v0, v1, v2, v3, dxfattribs=attribs)
        else:
            print(f"Unknown entity type for deserialize: {et}")
            return None
    except Exception as e:
        print(f"Failed to create {et}: {e}")
        return None
