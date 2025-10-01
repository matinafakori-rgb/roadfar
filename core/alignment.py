# core/alignment.py
# -*- coding: utf-8 -*-
"""
Alignment core module — مدیریت عناصر پلان (لاین، آرک، کلوتوئید-نمونه‌سازی شده)

ویژگی‌ها:
- کلاس‌های LineElement, ArcElement, ClothoidElement (تقریب)
- کلاس Alignment برای نگهداری عناصر به ترتیب، نمونه‌سازی، ذخیره/بارگذاری
- توابع هندسی کمکی: dist, bearing, rotate, normalize_angle, project_point_on_line
- استفاده از numpy در صورت وجود برای بهینه‌سازی نمونه‌گیری
- تحمل خطاهای هندسی و پیام‌های مناسب
"""

from __future__ import annotations
import math
import json
from typing import List, Tuple, Dict, Any, Optional, Union
from typing import Union
from pathlib import Path

# numpy اختیاری (برای نمونه‌گیری سریع‌تر)
try:
    import numpy as np  # type: ignore
    _HAS_NUMPY = True
except Exception:
    np = None
    _HAS_NUMPY = False

Point = Tuple[float, float]
Point3 = Tuple[float, float, float]


# --------------------- توابع هندسی کمکی ---------------------
def dist(a: Point, b: Point) -> float:
    """فاصله اقلیدسی بین دو نقطه"""
    return math.hypot(a[0] - b[0], a[1] - b[1])


def bearing(a: Point, b: Point) -> float:
    """زاویه (heading) خط از نقطه a به b بر حسب رادیان در [-pi, pi]"""
    return math.atan2(b[1] - a[1], b[0] - a[0])


def rotate(point: Point, angle: float, origin: Point = (0.0, 0.0)) -> Point:
    """چرخش نقطه حول origin به اندازه angle (رادیان)"""
    x, y = point[0] - origin[0], point[1] - origin[1]
    ca, sa = math.cos(angle), math.sin(angle)
    rx = x * ca - y * sa
    ry = x * sa + y * ca
    return (rx + origin[0], ry + origin[1])


def normalize_angle(a: float) -> float:
    """نرمال‌سازی زاویه به بازه (-pi, pi]"""
    a = math.fmod(a, 2.0 * math.pi)
    if a <= -math.pi:
        a += 2.0 * math.pi
    elif a > math.pi:
        a -= 2.0 * math.pi
    return a


def project_point_on_line(p: Point, a: Point, b: Point, clamp: bool = True) -> Tuple[Point, float]:
    """
    تصویر نقطه p روی خط AB را برمی‌گرداند به همراه پارامتر t در بازه [0,1] (اگر clamp=True).
    اگر clamp=False، t می‌تواند فراتر از [0,1] باشد.
    """
    ax, ay = a; bx, by = b; px, py = p
    dx, dy = bx - ax, by - ay
    L2 = dx * dx + dy * dy
    if L2 == 0:
        return (a, 0.0)
    t = ((px - ax) * dx + (py - ay) * dy) / L2
    t_out = max(0.0, min(1.0, t)) if clamp else t
    proj = (ax + t_out * dx, ay + t_out * dy)
    return proj, t_out


# --------------------- کلاس‌های عناصر ---------------------
class BaseElement:
    """کلاس پایه برای عناصر الایمنت"""
    def __init__(self, el_type: str):
        self.type = el_type
        self.length: float = 0.0

    def sample(self, step: float = 1.0) -> List[Point]:
        """نمونه‌گیری عنصر؛ مشتقات باید پیاده‌سازی کنند."""
        raise NotImplementedError("sample must be implemented by subclasses")

    def to_dict(self) -> Dict[str, Any]:
        """سریال‌سازی به dict — مشتقات باید بازنویسی کنند."""
        raise NotImplementedError()

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "BaseElement":
        """بازسازی از dict — باید توسط Alignment مدیریت شود."""
        raise NotImplementedError()


class LineElement(BaseElement):
    def __init__(self, A: Point, B: Point):
        super().__init__('line')
        self.A = (float(A[0]), float(A[1]))
        self.B = (float(B[0]), float(B[1]))
        self.length = dist(self.A, self.B)

    def sample(self, step: float = 1.0) -> List[Point]:
        if self.length <= 0 or step <= 0:
            return [self.A, self.B] if self.length > 0 else [self.A]
        n = max(1, int(math.ceil(self.length / step)))
        if _HAS_NUMPY:
            t = np.linspace(0.0, 1.0, n + 1)
            xs = self.A[0] + (self.B[0] - self.A[0]) * t
            ys = self.A[1] + (self.B[1] - self.A[1]) * t
            return [(float(x), float(y)) for x, y in zip(xs, ys)]
        pts = []
        for i in range(n + 1):
            t = i / n
            x = self.A[0] + (self.B[0] - self.A[0]) * t
            y = self.A[1] + (self.B[1] - self.A[1]) * t
            pts.append((x, y))
        return pts

    def to_dict(self) -> Dict[str, Any]:
        return {'type': 'line', 'A': [self.A[0], self.A[1]], 'B': [self.B[0], self.B[1]], 'length': self.length}

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "LineElement":
        return cls(tuple(d['A']), tuple(d['B']))


class ArcElement(BaseElement):
    """
    قوس دایره‌ای تعریف شده توسط نقاط ابتدا A و انتها B و شعاع (radius).
    side: 'left' یا 'right' برای تعیین مرکز از دو حالت ممکن.
    """
    def __init__(self, A: Point, B: Point, radius: float, side: str = 'left'):
        super().__init__('arc')
        self.A = (float(A[0]), float(A[1]))
        self.B = (float(B[0]), float(B[1]))
        self.radius = float(radius)
        self.side = 'left' if side not in ('left', 'right') else side
        # محاسبه مرکز، زوایا و طول
        self.center: Point
        self.start_angle: float
        self.end_angle: float
        self.ccw: bool
        self._compute_center_angles()
        # طول قوس
        ang = normalize_angle(self.end_angle - self.start_angle)
        if self.ccw and ang < 0:
            ang += 2 * math.pi
        if not self.ccw and ang > 0:
            ang = ang - 2 * math.pi
        self.length = abs(self.radius * ang)

    def _compute_center_angles(self):
        x1, y1 = self.A; x2, y2 = self.B
        dx, dy = x2 - x1, y2 - y1
        chord = math.hypot(dx, dy)
        if chord == 0:
            raise ValueError("Arc: chord length is zero (A and B identical).")
        if abs(self.radius) < chord / 2.0:
            raise ValueError("Arc: radius is too small for the chord length.")
        # midpoint and perpendicular direction
        mx, my = (x1 + x2) / 2.0, (y1 + y2) / 2.0
        # distance from midpoint to center
        h = math.sqrt(max(0.0, self.radius * self.radius - (chord / 2.0) ** 2))
        ux, uy = -dy / chord, dx / chord  # unit perpendicular (right of AB)
        if self.side == 'left':
            cx, cy = mx + ux * h, my + uy * h
        else:
            cx, cy = mx - ux * h, my - uy * h
        self.center = (cx, cy)
        a1 = math.atan2(y1 - cy, x1 - cx)
        a2 = math.atan2(y2 - cy, x2 - cx)
        # تعیین جهت ccw: اگر از a1 به a2 پادساعتگرد کوتاه‌تر باشد، ccw=True
        delta = normalize_angle(a2 - a1)
        ccw = True if delta > 0 else False
        # ولی باید کوتاه‌ترین قوس بین دو نقاط را انتخاب کنیم (معمولاً کوتاه‌تر)
        # اگر delta بزرگتر از pi باشد، معنایش این است که مسیر کوتاه‌تر در جهت مخالف است.
        if abs(delta) > math.pi:
            ccw = not ccw
        self.start_angle = a1
        self.end_angle = a2
        self.ccw = ccw

    def sample(self, step: float = 1.0) -> List[Point]:
        if self.length <= 0 or step <= 0:
            return [self.A, self.B]
        n = max(1, int(math.ceil(self.length / step)))
        pts: List[Point] = []
        # محاسبه span با توجه به جهت
        if self.ccw:
            span = normalize_angle(self.end_angle - self.start_angle)
            if span <= 0:
                span += 2 * math.pi
            if _HAS_NUMPY:
                t = np.linspace(0.0, 1.0, n + 1)
                angs = self.start_angle + span * t
                xs = self.center[0] + self.radius * np.cos(angs)
                ys = self.center[1] + self.radius * np.sin(angs)
                return [(float(x), float(y)) for x, y in zip(xs, ys)]
            for i in range(n + 1):
                t = i / n
                ang = self.start_angle + span * t
                x = self.center[0] + self.radius * math.cos(ang)
                y = self.center[1] + self.radius * math.sin(ang)
                pts.append((x, y))
        else:
            span = normalize_angle(self.start_angle - self.end_angle)
            if span <= 0:
                span += 2 * math.pi
            if _HAS_NUMPY:
                t = np.linspace(0.0, 1.0, n + 1)
                angs = self.start_angle - span * t
                xs = self.center[0] + self.radius * np.cos(angs)
                ys = self.center[1] + self.radius * np.sin(angs)
                return [(float(x), float(y)) for x, y in zip(xs, ys)]
            for i in range(n + 1):
                t = i / n
                ang = self.start_angle - span * t
                x = self.center[0] + self.radius * math.cos(ang)
                y = self.center[1] + self.radius * math.sin(ang)
                pts.append((x, y))
        return pts

    def to_dict(self) -> Dict[str, Any]:
        return {
            'type': 'arc',
            'A': [self.A[0], self.A[1]],
            'B': [self.B[0], self.B[1]],
            'radius': self.radius,
            'side': self.side,
            'center': [self.center[0], self.center[1]],
            'start_angle': self.start_angle,
            'end_angle': self.end_angle,
            'ccw': self.ccw,
            'length': self.length
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "ArcElement":
        A = tuple(d['A']); B = tuple(d['B'])
        radius = float(d.get('radius', 0.0))
        side = d.get('side', 'left')
        return cls(A, B, radius, side)


class ClothoidElement(BaseElement):
    """
    تقریب سادهٔ کلوتوئید (برای نمایش و ویرایش در GUI).
    این نسخه یک منحنی نرم بین دو نقطه می‌سازد با شیفت جانبی محدود — نه محاسبه دقیق Fresnel،
    اما برای نمایش پلان و محاسبهٔ طول/نمونه‌گیری مناسب است.

    پارامترها:
      - P0, P1: نقاط ابتدا و انتها
      - radius: شعاع هدف برای قوس مرکزی (برای تعیین شدت منحنی)
      - spiral_length: طول تقریبی هر انتقال (برای ویژوال)
      - samples: تعداد نمونه‌ها برای تقریب (تعداد نقاط داخلی)
      - side: 'left' یا 'right' جهت منحنی
    """
    def __init__(self, P0: Point, P1: Point, radius: float, spiral_length: float = 10.0, samples: int = 64, side: str = 'left'):
        super().__init__('clothoid_poly')
        self.P0 = (float(P0[0]), float(P0[1]))
        self.P1 = (float(P1[0]), float(P1[1]))
        self.radius = float(radius)
        self.spiral_length = float(max(0.0, spiral_length))
        self.samples = max(4, int(samples))
        self.side = side if side in ('left', 'right') else 'left'
        self.poly: List[Point] = self._build_poly()
        self.length = 0.0
        for i in range(len(self.poly) - 1):
            self.length += dist(self.poly[i], self.poly[i + 1])

    def _build_poly(self) -> List[Point]:
        """ساخت پلی‌لاین تقریب برای کلوتوئید — ترکیب ساده‌ی blend زاویه + offset جانبی"""
        x0, y0 = self.P0; x1, y1 = self.P1
        L = dist(self.P0, self.P1)
        if L <= 1e-9:
            return [self.P0]
        # headings
        h0 = bearing(self.P0, self.P1)
        # برای نمایش ساده از همان heading استفاده می‌کنیم؛ در طراحی واقعی باید headingهای ورودی باشد
        h1 = h0
        # easing function (smoothstep)
        def ease(t: float) -> float:
            return 3 * t * t - 2 * t * t * t
        # اگر numpy باشد از آن استفاده می‌کنیم
        pts: List[Point] = []
        if _HAS_NUMPY:
            ts = np.linspace(0.0, 1.0, self.samples + 1)
            chord_vec = (x1 - x0, y1 - y0)
            chord_len = L
            # جهت نرمال عمود به راستِ AB
            ux, uy = -chord_vec[1] / chord_len, chord_vec[0] / chord_len
            if self.side == 'right':
                ux, uy = -ux, -uy
            # عمق افست متناسب با sin(pi*t) و با ضریب وابسته به radius و spiral_length
            offset_scale = (self.spiral_length / max(1.0, L)) * (1.0 / max(1.0, abs(self.radius))) * 0.5
            for t in ts:
                px = x0 + chord_vec[0] * t
                py = y0 + chord_vec[1] * t
                off = math.sin(math.pi * t) * (L * offset_scale)
                px += ux * off; py += uy * off
                pts.append((float(px), float(py)))
            return pts
        # fallback pure python
        ux = -(y1 - y0) / L; uy = (x1 - x0) / L
        if self.side == 'right':
            ux, uy = -ux, -uy
        offset_scale = (self.spiral_length / max(1.0, L)) * (1.0 / max(1.0, abs(self.radius))) * 0.5
        for i in range(self.samples + 1):
            t = i / self.samples
            px = x0 + (x1 - x0) * t
            py = y0 + (y1 - y0) * t
            off = math.sin(math.pi * t) * (L * offset_scale)
            px += ux * off; py += uy * off
            pts.append((px, py))
        return pts

    def sample(self, step: float = 1.0) -> List[Point]:
        """نمونه‌گیری مجدد پلی‌لاین با گام تقریبی step"""
        if not self.poly:
            return [self.P0]
        if step <= 0:
            return list(self.poly)
        out: List[Point] = [self.poly[0]]
        acc = 0.0
        prev = self.poly[0]
        for i in range(1, len(self.poly)):
            cur = self.poly[i]
            seg = dist(prev, cur)
            if seg <= 1e-12:
                prev = cur
                continue
            while acc + seg >= step:
                need = step - acc
                r = need / seg
                x = prev[0] + (cur[0] - prev[0]) * r
                y = prev[1] + (cur[1] - prev[1]) * r
                out.append((x, y))
                prev = (x, y)
                seg = dist(prev, cur)
                acc = 0.0
            acc += seg
            prev = cur
        if not out or out[-1] != self.poly[-1]:
            out.append(self.poly[-1])
        return out

    def to_dict(self) -> Dict[str, Any]:
        return {
            'type': 'clothoid_poly',
            'P0': [self.P0[0], self.P0[1]],
            'P1': [self.P1[0], self.P1[1]],
            'radius': self.radius,
            'spiral_length': self.spiral_length,
            'samples': self.samples,
            'side': self.side
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "ClothoidElement":
        return cls(tuple(d['P0']), tuple(d['P1']), float(d.get('radius', 0.0)),
                   float(d.get('spiral_length', 10.0)), int(d.get('samples', 64)), d.get('side', 'left'))


# --------------------- کلاس Alignment ---------------------
class Alignment:
    """
    نگهدارندهٔ مجموعه عناصر پلان به ترتیب (line, arc, clothoid_poly).
    امکانات:
      - افزودن عناصر (add_line, add_arc_by_points_radius, add_clothoid)
      - درج/حذف/جابجایی عناصر
      - نمونه‌گیری کل الایمنت با given step
      - ذخیره/بارگذاری به/از dict یا فایل JSON
    """
    def __init__(self, name: str = "alignment"):
        self.name = name
        self.elements: List[BaseElement] = []
        self._rebuild_cache()

    def _rebuild_cache(self):
        self.total_length = sum(el.length for el in self.elements)

    # --- مدیریت عناصر ---
    def add_line(self, A: Point, B: Point) -> LineElement:
        el = LineElement(A, B)
        self.elements.append(el)
        self._rebuild_cache()
        return el

    def insert_element(self, index: int, el: BaseElement) -> None:
        if index < 0:
            index = max(0, len(self.elements) + 1 + index)
        self.elements.insert(index, el)
        self._rebuild_cache()

    def remove_element(self, index: int) -> None:
        if 0 <= index < len(self.elements):
            del self.elements[index]
            self._rebuild_cache()

    def move_element(self, from_idx: int, to_idx: int) -> None:
        if 0 <= from_idx < len(self.elements) and 0 <= to_idx <= len(self.elements):
            el = self.elements.pop(from_idx)
            self.elements.insert(to_idx, el)
            self._rebuild_cache()

    def add_arc_by_points_radius(self, A: Point, B: Point, radius: float, side: str = 'left') -> ArcElement:
        el = ArcElement(A, B, radius, side=side)
        self.elements.append(el)
        self._rebuild_cache()
        return el

    def add_clothoid(self, P0: Point, P1: Point, radius: float, spiral_length: float = 10.0, samples: int = 64, side: str = 'left') -> ClothoidElement:
        el = ClothoidElement(P0, P1, radius, spiral_length, samples, side)
        self.elements.append(el)
        self._rebuild_cache()
        return el

    # --- نمونه‌گیری کل الایمنت ---
    def sample(self, step: float = 1.0) -> List[Point]:
        """
        نمونه‌گیری کل الایمنت با گام تقریبی step.
        نتیجه لیستی از نقاط 2D است.
        """
        pts: List[Point] = []
        for el in self.elements:
            try:
                spts = el.sample(step=step)
            except Exception:
                # اگر نمونه‌گیری عنصر با خطا مواجه شد، از روش ساده‌تر استفاده می‌کنیم
                spts = [p for p in getattr(el, 'poly', [])] if hasattr(el, 'poly') else []
                if not spts and hasattr(el, 'A') and hasattr(el, 'B'):
                    spts = [el.A, el.B]
            if not spts:
                continue
            if pts and pts[-1] == spts[0]:
                pts.extend(spts[1:])
            else:
                pts.extend(spts)
        return pts

    # --- سریال‌سازی / ذخیره و بارگذاری ---
    def to_dict(self) -> Dict[str, Any]:
        return {
            'name': self.name,
            'elements': [el.to_dict() for el in self.elements]
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Alignment":
        aln = cls(d.get('name', 'alignment'))
        el_list = d.get('elements', [])
        for ed in el_list:
            try:
                t = ed.get('type', '').lower()
                if t == 'line':
                    aln.elements.append(LineElement.from_dict(ed))
                elif t == 'arc':
                    aln.elements.append(ArcElement.from_dict(ed))
                elif t in ('clothoid_poly', 'clothoid'):
                    aln.elements.append(ClothoidElement.from_dict(ed))
                else:
                    # اگر نوع ناشناخته بود، سعی می‌کنیم از اطلاعات خام استفاده کنیم (نادیده می‌گیریم)
                    continue
            except Exception:
                # از ادامه خواندن عناصر چشم‌پوشی می‌کنیم ولی بارگذاری بقیه را ادامه می‌دهیم
                continue
        aln._rebuild_cache()
        return aln

    def save_to_file(self, filepath: Union[str, Path]) -> None:
        p = str(filepath)
        with open(p, 'w', encoding='utf-8') as f:
            json.dump(self.to_dict(), f, ensure_ascii=False, indent=2)

    @classmethod
    def load_from_file(cls, filepath: Union[str, Path]) -> "Alignment":
        p = str(filepath)
        with open(p, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return cls.from_dict(data)

    # --- متدهای کاربردی کمکی ---
    def flatten_vertices(self) -> List[Point]:
        """لیستی از رئوس اصلی (A/B برای خطوط و آرک‌ها، ابتدا/انتها برای کلوتوئید)"""
        pts: List[Point] = []
        for el in self.elements:
            if isinstance(el, LineElement):
                pts.append(el.A); pts.append(el.B)
            elif isinstance(el, ArcElement):
                pts.append(el.A); pts.append(el.B)
            elif isinstance(el, ClothoidElement):
                if el.poly:
                    pts.append(el.poly[0]); pts.append(el.poly[-1])
        # حذف تکراری‌ها به نحوی که ترتیب حفظ شود
        seen = set()
        out: List[Point] = []
        for p in pts:
            key = (round(p[0], 9), round(p[1], 9))
            if key not in seen:
                out.append(p); seen.add(key)
        return out

    def clear(self):
        self.elements = []
        self._rebuild_cache()

    def nearest_vertex(self, pt: Point, max_dist: float = 10.0) -> Optional[Tuple[int, Point]]:
        """نزدیک‌ترین رأس را پیدا می‌کند؛ خروجی (index_in_flat_list, point) یا None"""
        best_idx = None; best_d = None
        verts = self.flatten_vertices()
        for i, v in enumerate(verts):
            d = dist(pt, v)
            if best_d is None or d < best_d:
                best_d = d; best_idx = i
        if best_d is None or best_d > max_dist:
            return None
        return best_idx, verts[best_idx]


# --------------------- تست ساده هنگام اجرای ماژول ---------------------
if __name__ == "__main__":
    # تست سریع صحت عملکرد
    aln = Alignment("test-aln")
    aln.add_line((0, 0), (100, 0))
    try:
        aln.add_arc_by_points_radius((100, 0), (150, 50), 60.0, side='left')
    except Exception as e:
        print("Arc creation error:", e)
    aln.add_clothoid((150, 50), (200, 100), 200.0, spiral_length=15.0, samples=48, side='left')
    pts = aln.sample(step=5.0)
    print("Elements:", len(aln.elements))
    print("Sample points:", len(pts))
    # roundtrip
    aln.save_to_file("alignment_test.json")
    aln2 = Alignment.load_from_file("alignment_test.json")
    print("Loaded elements:", len(aln2.elements))
