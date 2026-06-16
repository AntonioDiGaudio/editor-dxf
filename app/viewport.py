import math
from typing import Any

from ezdxf.math import Vec2


class Viewport:
    """Manages coordinate transforms between world and screen."""

    def __init__(self, width: int = 800, height: int = 600):
        self.width = width
        self.height = height
        self.view_x = 0.0
        self.view_y = 0.0
        self.scale = 1.0
        self.min_scale = 1e-6
        self.max_scale = 1e6

    def world_to_screen(self, wx: float, wy: float) -> tuple[float, float]:
        if math.isnan(wx) or math.isnan(wy) or math.isinf(wx) or math.isinf(wy):
            return (-99999, -99999)
        sx = (wx - self.view_x) * self.scale + self.width / 2
        sy = -(wy - self.view_y) * self.scale + self.height / 2
        return sx, sy

    def screen_to_world(self, sx: float, sy: float) -> tuple[float, float]:
        wx = (sx - self.width / 2) / self.scale + self.view_x
        wy = -(sy - self.height / 2) / self.scale + self.view_y
        return wx, wy

    def fit_to(self, entities: list[Any], pad_ratio: float = 0.1) -> None:
        if not entities:
            self.view_x = 0
            self.view_y = 0
            self.scale = 1.0
            return
        from app.geometry import safe_dxf, get_location
        all_pts: list[Vec2] = []
        for e in entities:
            bb = _manual_entity_bbox(e)
            if bb is not None:
                all_pts.append(bb[0])
                all_pts.append(bb[1])
        if not all_pts:
            return
        xmin = min(p.x for p in all_pts)
        xmax = max(p.x for p in all_pts)
        ymin = min(p.y for p in all_pts)
        ymax = max(p.y for p in all_pts)
        w = xmax - xmin
        h = ymax - ymin
        if w < 1e-10:
            w = 10
        if h < 1e-10:
            h = 10
        sx = (self.width * (1 - 2 * pad_ratio)) / w
        sy = (self.height * (1 - 2 * pad_ratio)) / h
        self.scale = min(sx, sy)
        self.view_x = (xmin + xmax) / 2
        self.view_y = (ymin + ymax) / 2

    def pan(self, dx_screen: float, dy_screen: float) -> None:
        self.view_x -= dx_screen / self.scale
        self.view_y += dy_screen / self.scale

    def zoom_at(self, sx: float, sy: float, factor: float) -> None:
        self.scale = max(self.min_scale, min(self.max_scale, self.scale * factor))
        wx, wy = self.screen_to_world(sx, sy)
        dx = sx - self.width / 2
        dy = sy - self.height / 2
        self.view_x = wx - dx / self.scale
        self.view_y = wy + dy / self.scale


def entity_bbox(entity: Any) -> tuple[Vec2, Vec2] | None:
    try:
        from ezdxf.math import BoundingBox
        bbox = BoundingBox([entity])
        if bbox.is_valid:
            return Vec2(bbox.extmin), Vec2(bbox.extmax)
    except Exception:
        pass
    return _manual_entity_bbox(entity)


def _manual_entity_bbox(entity: Any) -> tuple[Vec2, Vec2] | None:
    from app.geometry import safe_dxf, get_location
    et = entity.dxftype()
    pts: list[Vec2] = []
    try:
        if et == "LINE":
            s = safe_dxf(entity, "start")
            e = safe_dxf(entity, "end")
            if s is not None:
                pts.append(Vec2(s[0], s[1]))
            if e is not None:
                pts.append(Vec2(e[0], e[1]))
        elif et == "CIRCLE":
            c = safe_dxf(entity, "center")
            r = safe_dxf(entity, "radius")
            if c is not None and r is not None:
                pts.append(Vec2(c[0] - r, c[1] - r))
                pts.append(Vec2(c[0] + r, c[1] + r))
        elif et == "ARC":
            c = safe_dxf(entity, "center")
            r = safe_dxf(entity, "radius")
            if c is not None and r is not None:
                pts.append(Vec2(c[0] - r, c[1] - r))
                pts.append(Vec2(c[0] + r, c[1] + r))
        elif et in ("TEXT", "MTEXT"):
            ins = safe_dxf(entity, "insert")
            h = safe_dxf(entity, "height") or safe_dxf(entity, "char_height") or 10
            if ins is not None:
                pts.append(Vec2(ins[0] - h, ins[1] - h))
                pts.append(Vec2(ins[0] + h, ins[1] + h))
        elif et == "LWPOLYLINE":
            for p in entity.get_points(format='xy'):
                pts.append(Vec2(p[0], p[1]))
        elif et == "POLYLINE":
            for v in getattr(entity, 'vertices', []):
                loc = safe_dxf(v, "location")
                if loc is not None:
                    pts.append(Vec2(loc[0], loc[1]))
        elif et == "POINT":
            loc = safe_dxf(entity, "location")
            if loc is not None:
                pts.append(Vec2(loc[0], loc[1]))
        elif et == "ELLIPSE":
            c = safe_dxf(entity, "center")
            ma = safe_dxf(entity, "major_axis")
            ratio = safe_dxf(entity, "ratio", 1) or 1
            if c is not None and ma is not None:
                ml = Vec2(ma[0], ma[1]).magnitude
                pts.append(Vec2(c[0] - ml, c[1] - ml * ratio))
                pts.append(Vec2(c[0] + ml, c[1] + ml * ratio))
        elif et in ("SOLID", "3DFACE", "TRACE", "WIPEOUT"):
            for attr in ("vtx0", "vtx1", "vtx2", "vtx3"):
                v = safe_dxf(entity, attr)
                if v is not None:
                    pts.append(Vec2(v[0], v[1]))
        elif et == "INSERT":
            ins = safe_dxf(entity, "insert")
            if ins is not None:
                pts.append(Vec2(ins[0], ins[1]))
        elif et == "DIMENSION":
            for attr in ("defpoint", "defpoint2", "defpoint3"):
                v = safe_dxf(entity, attr)
                if v is not None:
                    pts.append(Vec2(v[0], v[1]))
        elif et == "SPLINE":
            cp = safe_dxf(entity, "control_points", None)
            if cp is not None:
                for v in cp:
                    if hasattr(v, 'x'):
                        pts.append(Vec2(v.x, v.y))
                    else:
                        pts.append(Vec2(v[0], v[1]))
            fp = safe_dxf(entity, "fit_points", None)
            if fp is not None:
                for v in fp:
                    if hasattr(v, 'x'):
                        pts.append(Vec2(v.x, v.y))
                    else:
                        pts.append(Vec2(v[0], v[1]))
        elif et == "HATCH":
            c = safe_dxf(entity, "center", None)
            if c is not None:
                pts.append(Vec2(c[0], c[1]))
        elif et == "VIEWPORT":
            c = safe_dxf(entity, "center")
            w = safe_dxf(entity, "width", 100) or 100
            h = safe_dxf(entity, "height", 100) or 100
            if c is not None:
                pts.append(Vec2(c[0] - w/2, c[1] - h/2))
                pts.append(Vec2(c[0] + w/2, c[1] + h/2))
    except Exception:
        pass

    if not pts:
        loc = get_location(entity)
        pts.append(loc)

    if pts:
        xs = [p.x for p in pts]
        ys = [p.y for p in pts]
        return Vec2(min(xs), min(ys)), Vec2(max(xs), max(ys))
    return None
