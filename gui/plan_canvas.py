# gui/plan_canvas.py
# -*- coding: utf-8 -*-
"""
PlanCanvas — کانواس مخصوص تب پلان / الایمنت (بازنویسی کامل)
ویژگی‌ها (خلاصه):
 - set_contours(contours) : دریافت و نمایش منحنی‌های میزان
 - fit_contours() : زوم/مرکز کردن برای نمایش همهٔ منحنی‌ها
 - start_plan_drawing() : رسم دستی پلان (کلیک چپ اضافه، کلیک راست پایان)
 - start_select_mandatory() / stop_select_mandatory() : انتخاب نقاط اجباری با کلیک روی منحنی‌ها
 - generate_suggested_route(params, mandatory_points=None) : تولید مسیر پیشنهادی ساده
 - ویرایش رأس‌ها با دبل کلیک، حذف رأس با منوی راست کلیک
 - توابع کمکی: to_dict / from_dict / delete_vertex_by_index / delete_vertex_at_screen
"""
from __future__ import annotations
from typing import List, Tuple, Dict, Any, Optional
import math, traceback

from PyQt5.QtWidgets import QWidget, QMessageBox, QInputDialog, QMenu, QAction
from PyQt5.QtGui import QPainter, QPen, QColor, QFont, QBrush
from PyQt5.QtCore import Qt, QPoint, QRect, pyqtSignal

Point = Tuple[float, float]
ContourDict = Dict[float, List[Tuple[Point, Point]]]


def _intersect_lines(p1: Point, d1: Point, p2: Point, d2: Point) -> Optional[Point]:
    """
    تقاطع دو خط پارامتریک p1 + t*d1 و p2 + s*d2 را برمی‌گرداند یا None اگر موازی باشند.
    """
    x1, y1 = p1; dx1, dy1 = d1
    x2, y2 = p2; dx2, dy2 = d2
    denom = dx1 * dy2 - dy1 * dx2
    if abs(denom) < 1e-12:
        return None
    t = ((x2 - x1) * dy2 - (y2 - y1) * dx2) / denom
    return (x1 + t * dx1, y1 + t * dy1)


class PlanCanvas(QWidget):
    """
    کانواس پلان — نمایش منحنی‌ها، رسم پلان، انتخاب نقاط اجباری، تولید مسیر پیشنهادی.
    """
    # سیگنال زمانی که یک نقطهٔ اجباری انتخاب شد: (x: float, y: float)
    mandatory_point_selected = pyqtSignal(float, float)

    def __init__(self, parent=None):
        super().__init__(parent)

        # داده‌ها
        self.aln: Optional[Any] = None
        self.contours: ContourDict = {}
        self.plan_poly: List[Point] = []
        self.mandatory_points: List[Point] = []

        # نمایش / تبدیل
        self.scale: float = 1.0
        self.offset_x: float = 0.0
        self.offset_y: float = 0.0
        self._min_scale = 1e-8
        self._max_scale = 1e8

        # تعامل
        self._panning: bool = False
        self._last_pan_pos = None
        self._adding_plan: bool = False
        self._select_mandatory_mode: bool = False
        self._select_radius_px: int = 8
        self.point_size: int = 6

        # لیبل/فونت
        self.label_font_family: str = "Sans"
        self.label_font_size: int = 10
        self.label_color = QColor(30, 30, 30)
        self.chainage_step: int = 10  # متر

        # حالات نمونه
        self._mode_select_tangents = False
        self._mode_edit = False

        # پیگیری ماوس برای hover
        self.setMouseTracking(True)
        self._hover_world: Optional[Point] = None

    # ---------- تبدیل coordinate ----------
    def world_to_screen(self, x: float, y: float) -> Tuple[int, int]:
        sx = x * self.scale + self.offset_x
        sy = y * self.scale + self.offset_y
        return int(round(sx)), int(round(sy))

    def screen_to_world(self, sx: int, sy: int) -> Tuple[float, float]:
        sc = self.scale if abs(self.scale) > 1e-12 else 1.0
        x = (sx - self.offset_x) / sc
        y = (sy - self.offset_y) / sc
        return float(x), float(y)

    # ---------- aln safe reading ----------
    def get_aln_elements(self) -> List[Any]:
        try:
            aln = self.aln
            if not aln:
                return []
            if hasattr(aln, "elements"):
                elems = getattr(aln, "elements")
                return list(elems) if elems else []
            if isinstance(aln, dict):
                for key in ("elements", "segments", "items", "lines"):
                    if key in aln and isinstance(aln[key], (list, tuple)):
                        return list(aln[key])
                # fallback: اگر dict شامل لیستی از dictها بود
                vals = [v for v in aln.values() if isinstance(v, list)]
                if vals:
                    return vals[0]
            return []
        except Exception:
            traceback.print_exc()
            return []

    # ---------- عمومی: set/get contours ----------
    def set_contours(self, contours: Optional[ContourDict]):
        """
        contours: dict[level] -> [((x1,y1),(x2,y2)), ...]
        این متد ایمن است و مقادیر را نرمال می‌کند.
        """
        try:
            if not contours:
                self.contours = {}
                self.update()
                return
            newc: ContourDict = {}
            for k, segs in contours.items():
                try:
                    level = float(k)
                except Exception:
                    continue
                if not isinstance(segs, (list, tuple)):
                    continue
                normalized = []
                for s in segs:
                    if not isinstance(s, (list, tuple)) or len(s) != 2:
                        continue
                    a, b = s
                    try:
                        ax, ay = float(a[0]), float(a[1])
                        bx, by = float(b[0]), float(b[1])
                        normalized.append(((ax, ay), (bx, by)))
                    except Exception:
                        continue
                if normalized:
                    newc[level] = normalized
            self.contours = newc
            self.fit_contours()
            self.update()
        except Exception:
            traceback.print_exc()

    def fit_contours(self):
        """زوم و آفست را طوری تنظیم می‌کند که همهٔ منحنی‌ها در دید باشند."""
        try:
            pts: List[Point] = []
            for segs in self.contours.values():
                for a, b in segs:
                    pts.append(a); pts.append(b)
            if not pts:
                # اگر پلان موجود است می‌توان آن را فیت کرد
                if self.plan_poly:
                    xs = [p[0] for p in self.plan_poly]; ys = [p[1] for p in self.plan_poly]
                else:
                    return
            else:
                xs = [p[0] for p in pts]; ys = [p[1] for p in pts]
            xmin, xmax = min(xs), max(xs)
            ymin, ymax = min(ys), max(ys)
            w = max(100, self.width() - 40)
            h = max(100, self.height() - 40)
            dx = xmax - xmin if xmax > xmin else 1.0
            dy = ymax - ymin if ymax > ymin else 1.0
            scale_x = w / dx; scale_y = h / dy
            self.scale = max(self._min_scale, min(min(scale_x, scale_y) * 0.9, self._max_scale))
            center_x = (xmin + xmax) / 2.0
            center_y = (ymin + ymax) / 2.0
            self.offset_x = (self.width() / 2.0) - center_x * self.scale
            self.offset_y = (self.height() / 2.0) - center_y * self.scale
            self.update()
        except Exception:
            traceback.print_exc()

    def clear_plan(self):
        self.plan_poly = []
        self.update()

    # ---------- رسم پلان دستی ----------
    def start_plan_drawing(self):
        self._adding_plan = True
        self.plan_poly = []
        QMessageBox.information(self, "رسم پلان", "حالت رسم پلان فعال شد.\nکلیک چپ برای افزودن رأس‌ها، کلیک راست برای پایان.")
        self.update()

    # ---------- انتخاب نقاط اجباری ----------
    def start_select_mandatory(self, clear_previous: bool = True):
        """شروع حالت انتخاب نقاط اجباری (کاربر روی منحنی کلیک می‌کند)."""
        if clear_previous:
            self.mandatory_points = []
        self._select_mandatory_mode = True
        QMessageBox.information(self, "انتخاب نقاط اجباری",
                                "حالت انتخاب نقاط اجباری فعال شد.\nروی خطوط منحنی کلیک کنید؛ برای پایان راست کلیک کنید.")
        self.update()

    def stop_select_mandatory(self):
        self._select_mandatory_mode = False
        self.update()

    def clear_mandatory_points(self):
        self.mandatory_points = []
        self.update()

    def get_mandatory_points(self) -> List[Point]:
        return list(self.mandatory_points)

    # ---------- محاسبات کمکی ----------
    def _closest_point_on_segment(self, px: float, py: float, ax: float, ay: float, bx: float, by: float):
        dx = bx - ax; dy = by - ay
        L2 = dx*dx + dy*dy
        if L2 == 0.0:
            return ax, ay, 0.0
        t = ((px - ax) * dx + (py - ay) * dy) / L2
        t_clamped = max(0.0, min(1.0, t))
        cx = ax + t_clamped * dx
        cy = ay + t_clamped * dy
        return cx, cy, t_clamped

    # ---------- تعامل موس (تغییر یافته برای انتخاب نقاط اجباری / رسم پلان) ----------
    def mousePressEvent(self, event):
        sx, sy = event.pos().x(), event.pos().y()
        if event.button() == Qt.LeftButton and self._select_mandatory_mode:
            wx, wy = self.screen_to_world(sx, sy)
            best = None
            best_px_dist = None
            for lev, segs in self.contours.items():
                for (a, b) in segs:
                    ax, ay = a; bx, by = b
                    cx, cy, t = self._closest_point_on_segment(wx, wy, ax, ay, bx, by)
                    scx, scy = self.world_to_screen(cx, cy)
                    dpx = math.hypot(scx - sx, scy - sy)
                    if best_px_dist is None or dpx < best_px_dist:
                        best_px_dist = dpx
                        best = (cx, cy, lev, (a, b), t)
            if best is not None and best_px_dist is not None and best_px_dist <= 12:
                cx, cy, lev, seg, t = best
                self.mandatory_points.append((float(cx), float(cy)))
                try:
                    self.mandatory_point_selected.emit(float(cx), float(cy))
                except Exception:
                    pass
                self.update()
            else:
                QMessageBox.information(self, "انتخاب نامعتبر", "نزدیک به خط منحنی کلیک کنید.")
            return

        if event.button() == Qt.LeftButton:
            if self._adding_plan:
                wx, wy = self.screen_to_world(sx, sy)
                self.plan_poly.append((wx, wy))
                self.update()
                return
            else:
                self._panning = True
                self._last_pan_pos = event.pos()
        elif event.button() == Qt.RightButton:
            if self._select_mandatory_mode:
                # پایان حالت انتخاب
                self._select_mandatory_mode = False
                QMessageBox.information(self, "انتخاب نقاط", f"انتخاب نقاط اجباری پایان یافت ({len(self.mandatory_points)} نقطه).")
                self.update()
                return
            if self._adding_plan:
                self._adding_plan = False
                QMessageBox.information(self, "پلان", "رسم پلان پایان یافت.")
                self.update()
                return
            # کلیک راست عادی -> فیت همه
            self.fit_contours()
            return

        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._panning and self._last_pan_pos is not None:
            cur = event.pos()
            dx = cur.x() - self._last_pan_pos.x()
            dy = cur.y() - self._last_pan_pos.y()
            self.offset_x += dx
            self.offset_y += dy
            self._last_pan_pos = cur
            self.update()
            return
        # update hover world coordinates
        sx, sy = event.pos().x(), event.pos().y()
        wx, wy = self.screen_to_world(sx, sy)
        self._hover_world = (wx, wy)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._panning = False
            self._last_pan_pos = None
        super().mouseReleaseEvent(event)

    def wheelEvent(self, event):
        try:
            mx, my = event.pos().x(), event.pos().y()
            wx_before, wy_before = self.screen_to_world(mx, my)
            delta = event.angleDelta().y()
            factor = 1.15 if delta > 0 else 1.0 / 1.15
            self.scale *= factor
            self.scale = max(self._min_scale, min(self.scale, self._max_scale))
            self.offset_x = mx - wx_before * self.scale
            self.offset_y = my - wy_before * self.scale
            self.update()
        except Exception:
            traceback.print_exc()

    def mouseDoubleClickEvent(self, event):
        # ویرایش رأس اگر نزدیک بود (دبل کلیک)
        sx, sy = event.pos().x(), event.pos().y()
        best_idx = None; best_d = None
        for i, (x, y) in enumerate(self.plan_poly):
            px, py = self.world_to_screen(x, y)
            d = math.hypot(px - sx, py - sy)
            if best_d is None or d < best_d:
                best_d = d; best_idx = i
        if best_idx is not None and best_d is not None and best_d <= self._select_radius_px:
            oldx, oldy = self.plan_poly[best_idx]
            txt, ok = QInputDialog.getText(self, "ویرایش رأس پلان", "مختصات x,y (با کاما):", text=f"{oldx},{oldy}")
            if ok and txt:
                try:
                    parts = [p.strip() for p in txt.split(',')]
                    nx = float(parts[0]); ny = float(parts[1])
                    self.plan_poly[best_idx] = (nx, ny)
                    self.update()
                except Exception:
                    QMessageBox.warning(self, "خطا", "مختصات نامعتبر.")
        else:
            super().mouseDoubleClickEvent(event)

    # منوی راست کلیک برای حذف رأس (اگر روی رأس کلیک راست شود)
    def contextMenuEvent(self, event):
        sx, sy = event.pos().x(), event.pos().y()
        # بررسی نزدیک بودن به یک رأس پلان
        found_idx = None; found_d = None
        for i, (x, y) in enumerate(self.plan_poly):
            px, py = self.world_to_screen(x, y)
            d = math.hypot(px - sx, py - sy)
            if found_d is None or d < found_d:
                found_d = d; found_idx = i
        menu = QMenu(self)
        if found_idx is not None and found_d is not None and found_d <= max(10, self._select_radius_px):
            act_del = QAction("حذف رأس", self)
            def _del():
                self.delete_vertex_by_index(found_idx)
            act_del.triggered.connect(_del)
            menu.addAction(act_del)
            act_cancel = QAction("انصراف", self)
            menu.addAction(act_cancel)
        else:
            # منوی عمومی
            act_fit = QAction("فیت همه", self)
            act_fit.triggered.connect(self.fit_contours)
            menu.addAction(act_fit)
            act_clear_mand = QAction("پاک کردن نقاط اجباری", self)
            act_clear_mand.triggered.connect(self.clear_mandatory_points)
            menu.addAction(act_clear_mand)
        menu.exec_(event.globalPos())

    # ---------- تولید مسیر پیشنهادی (نسخهٔ ساده) ----------
    def generate_suggested_route(self, params: Dict[str, Any], mandatory_points: Optional[List[Point]] = None):
        """
        الگوریتم نمونه: برای هر محور سه‌تایی (A,B,C) نقاط تانژانت T1/T2 پیدا می‌شود،
        قوس بین آنها تقریب زده می‌شود و polyline خروجی ساخته می‌شود.
        """
        try:
            pts = mandatory_points if mandatory_points is not None else self.mandatory_points
            if not pts or len(pts) < 2:
                QMessageBox.warning(self, "پیشنهاد مسیر", "حداقل دو نقطهٔ اجباری لازم است.")
                return

            speed = float(params.get('design_speed_kmh', 60.0))
            Rmin_user = float(params.get('r_min_m', 0.0))
            superelev = float(params.get('superelevation', 0.06))

            if Rmin_user <= 0:
                V = speed; f = 0.15
                Rmin = max(10.0, (V**2) / (127.0 * (superelev + f)))
            else:
                Rmin = Rmin_user

            if len(pts) == 2:
                self.plan_poly = [pts[0], pts[1]]
                self._adding_plan = False
                self.update()
                QMessageBox.information(self, "پیشنهاد مسیر", "خط مستقیم بین دو نقطه ساخته شد.")
                return

            out_poly: List[Point] = [pts[0]]

            def vec(a: Point, b: Point) -> Point:
                return (b[0] - a[0], b[1] - a[1])

            def length(v: Point) -> float:
                return math.hypot(v[0], v[1])

            def normalize(v: Point) -> Point:
                L = length(v)
                return (v[0] / L, v[1] / L) if L > 1e-12 else (0.0, 0.0)

            def dot(u: Point, v: Point) -> float:
                return u[0]*v[0] + u[1]*v[1]

            def angle_between(u: Point, v: Point) -> float:
                nu = normalize(u); nv = normalize(v)
                d = max(-1.0, min(1.0, dot(nu, nv)))
                return math.acos(d)

            for i in range(1, len(pts) - 1):
                A = pts[i-1]; B = pts[i]; C = pts[i+1]
                AB_v = (B[0] - A[0], B[1] - A[1])
                BC_v = (C[0] - B[0], C[1] - B[1])
                ang = angle_between((-AB_v[0], -AB_v[1]), BC_v)
                if ang < 1e-6 or abs(math.pi - ang) < 1e-6:
                    out_poly.append(B); continue
                len1 = length(AB_v); len2 = length(BC_v)
                R = max(Rmin, 1.0)
                t_req = R * math.tan(ang / 2.0)
                if t_req > 0.45 * len1 or t_req > 0.45 * len2:
                    out_poly.append(B); continue
                dir1 = normalize(AB_v); dir2 = normalize(BC_v)
                T1 = (B[0] - dir1[0] * t_req, B[1] - dir1[1] * t_req)
                T2 = (B[0] + dir2[0] * t_req, B[1] + dir2[1] * t_req)
                if not out_poly or length((out_poly[-1][0]-T1[0], out_poly[-1][1]-T1[1])) > 1e-6:
                    out_poly.append(T1)
                # مرکز قوسی تقریبی: تقاطع نرمال‌ها
                n1 = (-dir1[1], dir1[0]); n2 = (-dir2[1], dir2[0])
                center = _intersect_lines(T1, n1, T2, n2)
                if center is None:
                    center = ((T1[0]+T2[0])/2.0, (T1[1]+T2[1])/2.0)
                Rcalc = math.hypot(T1[0]-center[0], T1[1]-center[1])
                ang1 = math.atan2(T1[1]-center[1], T1[0]-center[0])
                ang2 = math.atan2(T2[1]-center[1], T2[0]-center[0])
                delta = ang2 - ang1
                while delta <= -math.pi: delta += 2*math.pi
                while delta > math.pi: delta -= 2*math.pi
                span = abs(delta)
                nseg = max(6, int(math.ceil(max(6, span / math.radians(10.0)))))
                for k in range(1, nseg):
                    tk = k / float(nseg)
                    angk = ang1 + delta * tk
                    px = center[0] + Rcalc * math.cos(angk)
                    py = center[1] + Rcalc * math.sin(angk)
                    out_poly.append((px, py))
                out_poly.append(T2)

            out_poly.append(pts[-1])
            self.plan_poly = out_poly
            self._adding_plan = False
            self._select_mandatory_mode = False
            # ایجاد یک ساختار سادهٔ aln تا قابل ذخیره باشد
            try:
                self.aln = {'name': params.get('name','suggested'), 'elements': [{'type':'poly','points': self.plan_poly}]}
            except Exception:
                self.aln = None
            self.update()
            QMessageBox.information(self, "پیشنهاد مسیر", f"مسیر پیشنهادی ساخته شد (تقریبی، {len(out_poly)} رأس).")
        except Exception:
            traceback.print_exc()
            QMessageBox.warning(self, "خطا", "خطا در تولید مسیر پیشنهادی.")

    # ---------- ویرایش / حذف رأس ----------
    def delete_vertex_by_index(self, idx: int) -> bool:
        if 0 <= idx < len(self.plan_poly):
            try:
                del self.plan_poly[idx]
                self.update()
                return True
            except Exception:
                traceback.print_exc()
        return False

    def delete_vertex_at_screen(self, sx: int, sy: int) -> bool:
        """حذف رأس نزدیک به مختصات صفحه‌ای (sx,sy) و بازگرداندن نتیجه."""
        for i, (x, y) in enumerate(self.plan_poly):
            px, py = self.world_to_screen(x, y)
            if math.hypot(px - sx, py - sy) <= max(10, self._select_radius_px):
                return self.delete_vertex_by_index(i)
        return False

    # ---------- تنظیمات ساده ----------
    def set_mode_select_tangents(self):
        self._mode_select_tangents = True
        self._mode_edit = False
        QMessageBox.information(self, "تانژانت‌ها", "حالت انتخاب تانژانت فعال شد (نمونه).")

    def set_mode_edit(self):
        self._mode_edit = True
        self._mode_select_tangents = False
        QMessageBox.information(self, "ویرایش", "حالت ویرایش فعال شد: دبل کلیک برای جابجایی راس، راست کلیک برای منو.")

    def set_chainage_step(self, v: int):
        try:
            self.chainage_step = int(v)
            self.update()
        except Exception:
            pass

    def set_label_font(self, family: str, size: int):
        self.label_font_family = family
        self.label_font_size = int(size)
        self.update()

    # ---------- ذخیره/بارگذاری ساده ----------
    def to_dict(self) -> Dict[str, Any]:
        return {
            'plan_poly': [[float(x), float(y)] for (x, y) in self.plan_poly],
            'mandatory_points': [[float(x), float(y)] for (x, y) in self.mandatory_points],
            'contours_levels': sorted(list(self.contours.keys()))
        }

    def from_dict(self, d: Dict[str, Any]):
        try:
            self.plan_poly = [tuple(p) for p in d.get('plan_poly', [])]
            self.mandatory_points = [tuple(p) for p in d.get('mandatory_points', [])]
            self.update()
        except Exception:
            traceback.print_exc()

    # ---------- رسم ----------
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.fillRect(self.rect(), QColor(255, 255, 255))

        # پس‌زمینهٔ شبکه ساده
        try:
            pen_grid = QPen(QColor(245, 245, 245))
            painter.setPen(pen_grid)
            step = 100
            for gx in range(0, self.width(), step):
                painter.drawLine(gx, 0, gx, self.height())
            for gy in range(0, self.height(), step):
                painter.drawLine(0, gy, self.width(), gy)
        except Exception:
            pass

        # رسم contours
        try:
            if self.contours:
                levels = sorted(self.contours.keys())
                main_step = max(1, int(round(len(levels) / 8))) if levels else 1
                for idx, lev in enumerate(levels):
                    segs = self.contours.get(lev, [])
                    is_main = (idx % main_step == 0)
                    pen = QPen(QColor(60, 60, 60) if is_main else QColor(150, 150, 150), 1)
                    painter.setPen(pen)
                    for a, b in segs:
                        sa = self.world_to_screen(a[0], a[1]); sb = self.world_to_screen(b[0], b[1])
                        painter.drawLine(sa[0], sa[1], sb[0], sb[1])
                # لیبل‌های منتخب
                try:
                    painter.setPen(QPen(self.label_color))
                    painter.setFont(QFont(self.label_font_family, max(7, self.label_font_size)))
                    shown = 0
                    for idx, lev in enumerate(levels):
                        if shown >= 6: break
                        if idx % main_step != 0: continue
                        segs = self.contours.get(lev, [])
                        if not segs: continue
                        a, b = segs[0]
                        mx, my = (a[0] + b[0]) / 2.0, (a[1] + b[1]) / 2.0
                        sx, sy = self.world_to_screen(mx, my)
                        painter.drawText(sx + 4, sy - 2, f"{lev:g}")
                        shown += 1
                except Exception:
                    pass
        except Exception:
            traceback.print_exc()

        # رسم پلان polyline
        try:
            if self.plan_poly and len(self.plan_poly) >= 2:
                pen_line = QPen(QColor(30, 120, 200), 2)
                painter.setPen(pen_line)
                pts_screen = [QPoint(*self.world_to_screen(x, y)) for (x, y) in self.plan_poly]
                for i in range(len(pts_screen) - 1):
                    a, b = pts_screen[i], pts_screen[i + 1]
                    painter.drawLine(a, b)
                # رئوس
                painter.setPen(QPen(QColor(10, 60, 120), 1))
                painter.setBrush(QBrush(QColor(180, 210, 240)))
                for p in pts_screen:
                    painter.drawEllipse(p.x() - 4, p.y() - 4, 8, 8)
                # لیبل کیلومتراژ ساده
                if self.chainage_step > 0:
                    try:
                        acc = 0.0; prev = self.plan_poly[0]
                        for i in range(1, len(self.plan_poly)):
                            cur = self.plan_poly[i]
                            seglen = math.hypot(cur[0] - prev[0], cur[1] - prev[1])
                            acc += seglen
                            if acc >= self.chainage_step:
                                sx, sy = self.world_to_screen(cur[0], cur[1])
                                painter.setFont(QFont(self.label_font_family, max(7, self.label_font_size - 1)))
                                painter.setPen(QPen(self.label_color))
                                painter.drawText(sx + 6, sy + 6, f"{int(acc)} m")
                                acc = 0.0
                            prev = cur
                    except Exception:
                        pass
        except Exception:
            traceback.print_exc()

        # رسم نقاط اجباری
        try:
            if self.mandatory_points:
                pen_m = QPen(QColor(200, 30, 30), 2)
                brush_m = QBrush(QColor(255, 200, 200))
                painter.setPen(pen_m)
                painter.setBrush(brush_m)
                for idx, (x, y) in enumerate(self.mandatory_points):
                    sx, sy = self.world_to_screen(x, y)
                    l = 6
                    painter.drawLine(sx - l, sy - l, sx + l, sy + l)
                    painter.drawLine(sx - l, sy + l, sx + l, sy - l)
                    painter.drawText(sx + l + 2, sy - l - 2, f"MP{idx+1}")
        except Exception:
            pass

        # نمایش مختصر hover
        try:
            if self._hover_world is not None:
                wx, wy = self._hover_world
                sx, sy = self.world_to_screen(wx, wy)
                txt = f"x={wx:.3f}, y={wy:.3f}"
                painter.setFont(QFont(self.label_font_family, max(7, self.label_font_size - 1)))
                painter.setPen(QPen(self.label_color))
                painter.drawText(sx + 8, sy - 8, txt)
        except Exception:
            pass

    # ---------- ابزار دیباگ ----------
    def debug_state(self) -> Dict[str, Any]:
        return {
            "contour_levels": sorted(list(self.contours.keys())),
            "plan_len": len(self.plan_poly),
            "mandatory": len(self.mandatory_points),
            "scale": self.scale,
            "offset_x": self.offset_x,
            "offset_y": self.offset_y,
            "aln_type": type(self.aln).__name__ if self.aln is not None else None
        }
