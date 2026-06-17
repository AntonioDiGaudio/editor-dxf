from typing import Any

from ezdxf.math import Vec2, Vec3


def point_line_distance(p: Vec2, a: Vec2, b: Vec2) -> float:
    ab = b - a
    ap = p - a
    t = ap.dot(ab) / ab.magnitude_square if ab.magnitude_square > 0 else 0
    t = max(0.0, min(1.0, t))
    closest = a + ab * t
    return (p - closest).magnitude


def segment_in_rect(
    a: Vec2, b: Vec2,
    xmin: float, ymin: float, xmax: float, ymax: float,
) -> bool:
    if (xmin <= a.x <= xmax and ymin <= a.y <= ymax) or \
       (xmin <= b.x <= xmax and ymin <= b.y <= ymax):
        return True
    for x in (xmin, xmax):
        for y in (ymin, ymax):
            d = point_line_distance(Vec2(x, y), a, b)
            if d < 1e-6:
                return True
    return False


def is_polygon_closed(pts: list[tuple[float, float]]) -> bool:
    if len(pts) < 3:
        return False
    first = Vec2(pts[0][0], pts[0][1])
    last = Vec2(pts[-1][0], pts[-1][1])
    return (first - last).magnitude < 1e-6


def safe_dxf(entity: Any, attr: str, default: Any = None) -> Any:
    try:
        return getattr(entity.dxf, attr)
    except Exception:
        return default


def get_location(entity: Any) -> Vec2:
    t = safe_dxf(entity, "insert", None)
    if t is not None:
        return Vec2(t[0], t[1])
    for attr in ("center",):
        v = safe_dxf(entity, attr, None)
        if v is not None:
            return Vec2(v[0], v[1])
    for attr in ("start", "defpoint"):
        v = safe_dxf(entity, attr, None)
        if v is not None:
            return Vec2(v[0], v[1])
    return Vec2(0, 0)


def clean_mtext(raw_text: str | None) -> str:
    if not raw_text:
        return ""
    raw_text = str(raw_text)

    # Replace DXF escape sequences first
    raw_text = raw_text.replace("\\\\", "\x00")  # temp protect \\
    raw_text = raw_text.replace("\\{", "\x01")    # temp protect \{
    raw_text = raw_text.replace("\\}", "\x02")    # temp protect \}

    # Remove all \X; \NN; style formatting codes
    # \P → newline (keep)
    raw_text = raw_text.replace("\\P", "\n")
    # \~ → non-breaking space → space
    raw_text = raw_text.replace("\\~", " ")

    # Remove known single-char formatting commands
    for code in ("L", "l", "O", "o", "K", "k", "X"):
        raw_text = raw_text.replace(f"\\{code}", "")

    # Remove parameterized commands: \H2.5; \W0.8; \C1; \A1; \Ffont; \S...; \T...; \Q...
    import re
    raw_text = re.sub(r"(?i)\\(H|W|C|A|F|S|T|Q)[^;]*;", "", raw_text)

    # Remove any remaining backslash codes (like stray \S, \P at end, etc.)
    raw_text = re.sub(r"\\.", "", raw_text)

    # Remove mtext grouping braces
    raw_text = raw_text.replace("{", "").replace("}", "")

    # AutoCAD %%-codes (used in TEXT, sometimes MTEXT):
    #   %%d = degree, %%c = diameter, %%p = plus/minus,
    #   %%u/%%o = under/over-line toggle, %%% = literal percent
    raw_text = re.sub(r"(?i)%%d", "°", raw_text)
    raw_text = re.sub(r"(?i)%%c", "ø", raw_text)
    raw_text = re.sub(r"(?i)%%p", "±", raw_text)
    raw_text = re.sub(r"(?i)%%[uo]", "", raw_text)
    raw_text = raw_text.replace("%%%", "%")
    # %%nnn → character by code
    raw_text = re.sub(r"%%(\d{1,3})",
                      lambda m: chr(int(m.group(1))) if int(m.group(1)) < 0x110000 else "",
                      raw_text)

    # Restore protected chars
    raw_text = raw_text.replace("\x00", "\\")
    raw_text = raw_text.replace("\x01", "{")
    raw_text = raw_text.replace("\x02", "}")

    # Collapse multiple spaces (not newlines)
    raw_text = re.sub(r" +", " ", raw_text)

    return raw_text


def move_entity(entity: Any, offset: Vec2) -> None:
    et = entity.dxftype()
    try:
        if et == "LINE":
            s = safe_dxf(entity, "start")
            e = safe_dxf(entity, "end")
            if s is not None:
                entity.dxf.start = Vec3(s[0] + offset.x, s[1] + offset.y, s[2] if len(s) > 2 else 0)
            if e is not None:
                entity.dxf.end = Vec3(e[0] + offset.x, e[1] + offset.y, e[2] if len(e) > 2 else 0)
        elif et == "CIRCLE":
            c = safe_dxf(entity, "center")
            if c is not None:
                entity.dxf.center = Vec3(c[0] + offset.x, c[1] + offset.y, c[2] if len(c) > 2 else 0)
        elif et == "ARC":
            c = safe_dxf(entity, "center")
            if c is not None:
                entity.dxf.center = Vec3(c[0] + offset.x, c[1] + offset.y, c[2] if len(c) > 2 else 0)
        elif et == "LWPOLYLINE":
            pts = [(p[0] + offset.x, p[1] + offset.y) for p in entity.get_points(format='xy')]
            entity.set_points(pts)
        elif et == "POLYLINE":
            for v in getattr(entity, 'vertices', []):
                loc = safe_dxf(v, "location")
                if loc is not None:
                    v.dxf.location = Vec3(loc[0] + offset.x, loc[1] + offset.y, loc[2] if len(loc) > 2 else 0)
        elif et in ("TEXT", "MTEXT"):
            ins = safe_dxf(entity, "insert")
            if ins is not None:
                entity.dxf.insert = Vec3(ins[0] + offset.x, ins[1] + offset.y, ins[2] if len(ins) > 2 else 0)
        elif et == "POINT":
            loc = safe_dxf(entity, "location")
            if loc is not None:
                entity.dxf.location = Vec3(loc[0] + offset.x, loc[1] + offset.y, loc[2] if len(loc) > 2 else 0)
        elif et == "ELLIPSE":
            c = safe_dxf(entity, "center")
            if c is not None:
                entity.dxf.center = Vec3(c[0] + offset.x, c[1] + offset.y, c[2] if len(c) > 2 else 0)
        elif et == "INSERT":
            ins = safe_dxf(entity, "insert")
            if ins is not None:
                entity.dxf.insert = Vec3(ins[0] + offset.x, ins[1] + offset.y, ins[2] if len(ins) > 2 else 0)
        elif et == "SOLID":
            for attr in ("vtx0", "vtx1", "vtx2", "vtx3"):
                v = safe_dxf(entity, attr)
                if v is not None:
                    setattr(entity.dxf, attr, Vec3(v[0] + offset.x, v[1] + offset.y, v[2] if len(v) > 2 else 0))
        elif et == "3DFACE":
            for attr in ("vtx0", "vtx1", "vtx2", "vtx3"):
                v = safe_dxf(entity, attr)
                if v is not None:
                    setattr(entity.dxf, attr, Vec3(v[0] + offset.x, v[1] + offset.y, v[2] if len(v) > 2 else 0))
        elif et == "TRACE":
            for attr in ("vtx0", "vtx1", "vtx2", "vtx3"):
                v = safe_dxf(entity, attr)
                if v is not None:
                    setattr(entity.dxf, attr, Vec3(v[0] + offset.x, v[1] + offset.y, v[2] if len(v) > 2 else 0))
        elif et == "DIMENSION":
            for attr in ("defpoint", "defpoint2", "defpoint3"):
                v = safe_dxf(entity, attr)
                if v is not None:
                    setattr(entity.dxf, attr, Vec3(v[0] + offset.x, v[1] + offset.y, v[2] if len(v) > 2 else 0))
            try:
                tm = safe_dxf(entity, "text_midpoint", None)
                if tm is not None:
                    entity.dxf.text_midpoint = Vec3(tm[0] + offset.x, tm[1] + offset.y, tm[2] if len(tm) > 2 else 0)
            except Exception:
                pass
        elif et == "HATCH":
            c = safe_dxf(entity, "center", None)
            if c is not None:
                entity.dxf.center = Vec3(c[0] + offset.x, c[1] + offset.y, c[2] if len(c) > 2 else 0)
            paths = getattr(entity, 'paths', [])
            if paths:
                try:
                    for path in paths:
                        edges = getattr(path, 'edges', [])
                        for edge in edges:
                            if hasattr(edge, 'start') and hasattr(edge, 'end'):
                                if hasattr(edge.start, 'x'):
                                    edge.start = Vec2(edge.start.x + offset.x, edge.start.y + offset.y)
                                if hasattr(edge.end, 'x'):
                                    edge.end = Vec2(edge.end.x + offset.x, edge.end.y + offset.y)
                            if hasattr(edge, 'center') and hasattr(edge, 'radius') and hasattr(edge, 'start_angle'):
                                if hasattr(edge.center, 'x'):
                                    edge.center = Vec2(edge.center.x + offset.x, edge.center.y + offset.y)
                except Exception:
                    pass
        elif et == "SPLINE":
            cp = safe_dxf(entity, "control_points", None)
            if cp is not None:
                try:
                    new_cp = [(p[0] + offset.x, p[1] + offset.y) for p in cp]
                    entity.dxf.control_points = new_cp
                except Exception:
                    pass
            fp = safe_dxf(entity, "fit_points", None)
            if fp is not None:
                try:
                    new_fp = [(p[0] + offset.x, p[1] + offset.y) for p in fp]
                    entity.dxf.fit_points = new_fp
                except Exception:
                    pass
        elif et == "VIEWPORT":
            c = safe_dxf(entity, "center")
            if c is not None:
                entity.dxf.center = Vec3(c[0] + offset.x, c[1] + offset.y, c[2] if len(c) > 2 else 0)
        elif et == "WIPEOUT":
            for attr in ("vtx0", "vtx1", "vtx2", "vtx3"):
                v = safe_dxf(entity, attr)
                if v is not None:
                    setattr(entity.dxf, attr, Vec3(v[0] + offset.x, v[1] + offset.y, v[2] if len(v) > 2 else 0))
    except Exception as exc:
        print(f"Move error {et}: {exc}")
