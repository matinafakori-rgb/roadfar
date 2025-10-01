# gui/canvas.py
# -*- coding: utf-8 -*-
"""
CanvasWidget — نمایش و مدیریت نقاط و سطوح (نهایی / کامل)
ویژگی‌ها:
 - pan (Left drag)، zoom (wheel)، rubber zoom (Right drag)
 - fit_all، world/screen transforms
 - افزودن نقطه با double-click، ویرایش با double-click، حذف با متد
 - مثلث‌بندی: اگر scipy.spatial.Delaunay نصب باشد از آن استفاده می‌شود، در غیر این صورت fallback
 - افزودن مثلث دستی با 3 کلیک، حذف مثلث با کلیک روی ضلع
 - محاسبهٔ منحنی‌های میزان (از مثلث‌ها) برای نمایش در PlanCanvas
 - حالت انتخاب نقاط اجباری (selection_mode == 'mandatory_select') برای کلیک و انتخاب شناسه‌ها
 - سیگنال points_changed هنگام تغییر مجموعه نقاط
"""
from __future__ import annotations
from typing import List, Tuple, Dict, Any, Optional
import math
import traceback

from PyQt5.QtWidgets import (
    QWidget, QDialog, QFormLayout, QLineEdit, QHBoxLayout, QPushButton,
    QMessageBox, QLabel
)
from PyQt5.QtGui import QPainter, QBrush, QColor, QPen, QPolygon, QFont
from PyQt5.QtCore import Qt, QRect, pyqtSignal, QPoint

# optional numeric libs
_HAS_NUMPY = False
_HAS_DELAUNAY = False
np = None
Delaunay = None
try:
    import numpy as np  # type: ignore
    _HAS_NUMPY = True
    try:
        from scipy.spatial import Delaunay  # type: ignore
        _HAS_DELAUNAY = True
    except Exception:
        Delaunay = None
        _HAS_DELAUNAY = False
except Exception:
    np = None
    _HAS_NUMPY = False
    _HAS_DELAUNAY = False
    Delaunay = None


# ------------------ small dialog for editing a point ------------------
class PointEditDialog(QDialog):
    def __init__(self, parent=None, point_data=None):
        super().__init__(parent)
        self.setWindowTitle("ویرایش نقطه")
        self.setModal(True)
        p = dict(point_data or {'id': '', 'x': 0.0, 'y': 0.0, 'z': 0.0, 'code': ''})
        self.point = p
        layout = QFormLayout(self)
        self.id_edit = QLineEdit(str(p.get('id', '')))
        self.x_edit = QLineEdit(str(p.get('x', 0.0)))
        self.y_edit = QLineEdit(str(p.get('y', 0.0)))
        self.z_edit = QLineEdit(str(p.get('z', 0.0)))
        self.code_edit = QLineEdit(str(p.get('code', '')))
        layout.addRow("id:", self.id_edit)
        layout.addRow("X:", self.x_edit)
        layout.addRow("Y:", self.y_edit)
        layout.addRow("Z:", self.z_edit)
        layout.addRow("code:", self.code_edit)
        btns = QHBoxLayout()
        ok = QPushButton("OK"); cancel = QPushButton("Cancel")
        ok.clicked.connect(self.on_ok); cancel.clicked.connect(self.reject)
        btns.addWidget(ok); btns.addWidget(cancel)
        layout.addRow(btns)

    def on_ok(self):
        try:
            idtxt = self.id_edit.text().strip()
            try:
                idval = int(idtxt)
            except Exception:
                try:
                    idval = int(float(idtxt))
                except Exception:
                    idval = idtxt
            x = float(self.x_edit.text().strip())
            y = float(self.y_edit.text().strip())
            z = float(self.z_edit.text().strip())
            code = self.code_edit.text().strip()
            self.point = {'id': idval, 'x': x, 'y': y, 'z': z, 'code': code}
            self.accept()
        except Exception as e:
            QMessageBox.warning(self, "ورودی نامعتبر", f"مقادیر معتبر وارد کنید.\n{e}")


# ------------------ CanvasWidget ------------------
class CanvasWidget(QWidget):
    points_changed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        # shapes: list of dicts {'type':'point','pos':(x,y),'data':{...}}
        self.shapes: List[Dict[str, Any]] = []
        self.boundaries: List[List[Tuple[float, float]]] = []
        # manual triangles references (indices into shapes)
        self.triangles: List[Tuple[int, int, int]] = []

        # view params
        self.point_size = 8
        self.scale = 1.0
        self.offset_x = 0.0
        self.offset_y = 0.0
        self._margin = 20
        self._min_scale = 1e-8
        self._max_scale = 1e8

        # mouse state
        self._panning = False
        self._last_pan_pos = None
        self._rubber_start = None
        self._rubber_current = None
        self._show_rubber = False
        self._select_radius_px = 10

        # mode flags
        self.mode = 'points'
        self.show_triangulation = False
        self.show_contours = False

        # contour params
        self.contour_main_interval = 5.0
        self.contour_sub_divisions = 4
        self.contour_label_every = 1

        # label style
        self.label_font_name = "Sans"
        self.label_font_size = 9
        self.label_color = QColor(30, 30, 30)

        # hover
        self.setMouseTracking(True)
        self._hover_pos = None
        self._hover_z = None

        # triangulation cache
        self._cached_triangles = None
        self._last_pts_hash = None

        # manual triangle build
        self._add_triangle_mode = False
        self._add_triangle_clicks: List[int] = []
        self._delete_triangle_mode = False

        # selection for mandatory points (used by main window)
        # mode values: 'none', 'mandatory_select'
        self.selection_mode = 'none'
        self.selected_mandatory_ids = set()

    # ---------- transforms ----------
    def world_to_screen(self, x: float, y: float) -> Tuple[int, int]:
        sx = x * self.scale + self.offset_x
        sy = y * self.scale + self.offset_y
        return int(round(sx)), int(round(sy))

    def screen_to_world(self, sx: int, sy: int) -> Tuple[float, float]:
        x = (sx - self.offset_x) / self.scale
        y = (sy - self.offset_y) / self.scale
        return float(x), float(y)

    # ---------- fit ----------
    def fit_all(self):
        pts = [s for s in self.shapes if s.get('type') == 'point']
        if not pts:
            return
        xs = [p['pos'][0] for p in pts]
        ys = [p['pos'][1] for p in pts]
        xmin, xmax = min(xs), max(xs)
        ymin, ymax = min(ys), max(ys)
        self.fit_to_bbox(xmin, xmax, ymin, ymax)

    def fit_to_bbox(self, xmin, xmax, ymin, ymax):
        w = max(100, self.width() - 2 * self._margin)
        h = max(100, self.height() - 2 * self._margin)
        dx = xmax - xmin if xmax > xmin else 1.0
        dy = ymax - ymin if ymax > ymin else 1.0
        scale_x = w / dx
        scale_y = h / dy
        new_scale = min(scale_x, scale_y) * 0.9
        self.scale = max(self._min_scale, min(new_scale, self._max_scale))
        self.offset_x = self._margin - xmin * self.scale
        self.offset_y = self._margin - ymin * self.scale
        self.update()

    # ---------- mouse interaction ----------
    def wheelEvent(self, event):
        try:
            mx, my = event.pos().x(), event.pos().y()
            wx_before, wy_before = self.screen_to_world(mx, my)
            delta = event.angleDelta().y()
            factor = 1.15 if delta > 0 else (1.0 / 1.15)
            self.scale *= factor
            self.scale = max(self._min_scale, min(self.scale, self._max_scale))
            self.offset_x = mx - wx_before * self.scale
            self.offset_y = my - wy_before * self.scale
            self.update()
        except Exception:
            traceback.print_exc()

    def mousePressEvent(self, event):
        sx, sy = event.pos().x(), event.pos().y()

        # add-triangle mode
        if event.button() == Qt.LeftButton and self._add_triangle_mode:
            idx = self._find_nearest_point_index(sx, sy, max_px=12)
            if idx is not None:
                if not self._add_triangle_clicks or idx != self._add_triangle_clicks[-1]:
                    self._add_triangle_clicks.append(idx)
                if len(self._add_triangle_clicks) >= 3:
                    tri = tuple(self._add_triangle_clicks[:3])
                    if not any(set(tri) == set(t) for t in self.triangles):
                        self.triangles.append(tri)
                    self._add_triangle_clicks = []
                    self._add_triangle_mode = False
                    self._cached_triangles = None
                    try:
                        self.points_changed.emit()
                    except Exception:
                        pass
                    QMessageBox.information(self, "مثلث اضافه شد", "مثلث جدید اضافه شد.")
                    self.update()
            return

        # delete-triangle mode
        if event.button() == Qt.LeftButton and self._delete_triangle_mode:
            found = self._find_nearest_triangle_edge(sx, sy, max_px=8)
            if found:
                t_idx, _ = found
                try:
                    del self.triangles[t_idx]
                    self._cached_triangles = None
                    self.update()
                    QMessageBox.information(self, "حذف", "مثلث حذف شد.")
                except Exception:
                    traceback.print_exc()
            return

        # mandatory selection mode (toggle selection of point by click)
        if event.button() == Qt.LeftButton and self.selection_mode == 'mandatory_select':
            idx = self._find_nearest_point_index(sx, sy, max_px=12)
            if idx is not None:
                pid = str(self.shapes[idx]['data'].get('id', ''))
                if pid in self.selected_mandatory_ids:
                    self.selected_mandatory_ids.remove(pid)
                else:
                    self.selected_mandatory_ids.add(pid)
                try:
                    self.points_changed.emit()
                except Exception:
                    pass
                self.update()
            else:
                QMessageBox.information(self, "انتخاب", "نقطهٔ نزدیکی انتخاب نشد.")
            return

        # normal pan / rubber start
        if event.button() == Qt.LeftButton:
            self._panning = True
            self._last_pan_pos = event.pos()
        elif event.button() == Qt.RightButton:
            self._rubber_start = event.pos()
            self._rubber_current = event.pos()
            self._show_rubber = True
            self.update()

    def mouseMoveEvent(self, event):
        if self._panning and self._last_pan_pos:
            cur = event.pos()
            dx = cur.x() - self._last_pan_pos.x()
            dy = cur.y() - self._last_pan_pos.y()
            self.offset_x += dx
            self.offset_y += dy
            self._last_pan_pos = cur
            self.update()
        elif self._show_rubber and self._rubber_start:
            self._rubber_current = event.pos()
            self.update()
        else:
            sx, sy = event.pos().x(), event.pos().y()
            wx, wy = self.screen_to_world(sx, sy)
            self._hover_pos = (wx, wy)
            if self.mode == 'surface':
                self._hover_z = self._interpolate_z_at(wx, wy)
            else:
                self._hover_z = None
            self.update()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._panning = False
            self._last_pan_pos = None
        elif event.button() == Qt.RightButton and self._show_rubber:
            r1 = self._rubber_start
            r2 = self._rubber_current or self._rubber_start
            x1, y1 = min(r1.x(), r2.x()), min(r1.y(), r2.y())
            x2, y2 = max(r1.x(), r2.x()), max(r1.y(), r2.y())
            if abs(x2 - x1) < 6 or abs(y2 - y1) < 6:
                self.fit_all()
            else:
                wx1, wy1 = self.screen_to_world(x1, y1)
                wx2, wy2 = self.screen_to_world(x2, y2)
                self.fit_to_bbox(min(wx1, wx2), max(wx1, wx2), min(wy1, wy2), max(wy1, wy2))
            self._rubber_start = None
            self._rubber_current = None
            self._show_rubber = False
            self.update()

    def mouseDoubleClickEvent(self, event):
        sx, sy = event.pos().x(), event.pos().y()
        best_idx = None; best_d = None
        for idx, shape in enumerate(self.shapes):
            if shape.get('type') != 'point': continue
            px, py = self.world_to_screen(shape['pos'][0], shape['pos'][1])
            d = math.hypot(px - sx, py - sy)
            if best_d is None or d < best_d:
                best_d = d; best_idx = idx
        if best_idx is not None and best_d is not None and best_d <= self._select_radius_px:
            try:
                pdata = dict(self.shapes[best_idx].get('data', {}))
                dlg = PointEditDialog(self, point_data=pdata)
                if dlg.exec_() == QDialog.Accepted:
                    newp = dlg.point
                    shape = self.shapes[best_idx]
                    shape['data']['id'] = newp['id']
                    shape['data']['x'] = float(newp['x'])
                    shape['data']['y'] = float(newp['y'])
                    shape['data']['z'] = float(newp['z'])
                    shape['data']['code'] = newp.get('code', '')
                    shape['pos'] = (float(newp['x']), float(newp['y']))
                    self._cached_triangles = None
                    try:
                        self.points_changed.emit()
                    except Exception:
                        pass
                    self.update()
            except Exception:
                traceback.print_exc()

    # ---------- triangulation ----------
    def _points_hash(self):
        try:
            pts = tuple((p['pos'][0], p['pos'][1], float(p['data'].get('z', 0.0))) for p in self.shapes if p.get('type') == 'point')
            return hash(pts)
        except Exception:
            return None

    def compute_triangulation(self):
        pts = [p for p in self.shapes if p.get('type') == 'point']
        if len(pts) < 3:
            return []
        current_hash = self._points_hash()
        if self._cached_triangles is not None and current_hash == self._last_pts_hash:
            return self._cached_triangles

        coords = [(p['pos'][0], p['pos'][1]) for p in pts]
        zs = [float(p['data'].get('z', 0.0)) for p in pts]
        triangles = []
        try:
            if _HAS_DELAUNAY and _HAS_NUMPY and Delaunay is not None:
                arr = np.array(coords)
                tri = Delaunay(arr)
                for simplex in tri.simplices:
                    i0, i1, i2 = int(simplex[0]), int(simplex[1]), int(simplex[2])
                    triangles.append(((coords[i0][0], coords[i0][1], zs[i0]),
                                      (coords[i1][0], coords[i1][1], zs[i1]),
                                      (coords[i2][0], coords[i2][1], zs[i2])))
            else:
                triangles = self._triangulate_fallback(coords, zs)
        except Exception:
            traceback.print_exc()
            triangles = self._triangulate_fallback(coords, zs)

        # add manual triangles (convert indices relative to shapes)
        manual_tris = []
        for tri_idx in self.triangles:
            try:
                v0 = self.shapes[tri_idx[0]]; v1 = self.shapes[tri_idx[1]]; v2 = self.shapes[tri_idx[2]]
                manual_tris.append(((v0['pos'][0], v0['pos'][1], float(v0['data'].get('z', 0))),
                                     (v1['pos'][0], v1['pos'][1], float(v1['data'].get('z', 0))),
                                     (v2['pos'][0], v2['pos'][1], float(v2['data'].get('z', 0)))))
            except Exception:
                continue

        full = triangles + manual_tris
        self._cached_triangles = full
        self._last_pts_hash = current_hash
        return full

    def _triangulate_fallback(self, coords, zs):
        # trivial fan triangulation around centroid (safe fallback)
        if len(coords) < 3:
            return []
        cx = sum(p[0] for p in coords) / len(coords)
        cy = sum(p[1] for p in coords) / len(coords)

        def get_z_at(pt):
            best_i = None; best_d = None
            for i, c in enumerate(coords):
                d = (c[0]-pt[0])**2 + (c[1]-pt[1])**2
                if best_d is None or d < best_d:
                    best_d = d; best_i = i
            return zs[best_i] if best_i is not None else 0.0

        tris = []
        for i in range(len(coords)):
            a = coords[i]; b = coords[(i+1)%len(coords)]
            tris.append(((cx, cy, get_z_at((cx,cy))), (a[0], a[1], get_z_at(a)), (b[0], b[1], get_z_at(b))))
        return tris

    # ---------- contours from triangles ----------
    def compute_contours(self, main_interval=None, sub_divisions=None):
        try:
            triangles = self.compute_triangulation()
            if not triangles:
                return {}
            if main_interval is None:
                main_interval = float(self.contour_main_interval or 5.0)
            if sub_divisions is None:
                sub_divisions = int(self.contour_sub_divisions or 0)
            zs = [v[2] for tri in triangles for v in tri]
            minz, maxz = min(zs), max(zs)
            if minz == maxz:
                return {}
            start = math.floor(minz / main_interval) * main_interval
            levels = []
            L = start
            eps = 1e-9
            while L <= maxz + eps:
                levels.append(round(L, 9))
                L += main_interval
            if sub_divisions > 0:
                lw = []
                for i in range(len(levels)-1):
                    a = levels[i]; b = levels[i+1]
                    lw.append(a)
                    step = (b - a) / (sub_divisions + 1)
                    for j in range(1, sub_divisions+1):
                        lw.append(round(a + j*step, 9))
                lw.append(levels[-1])
                levels = lw
            segs = {lev: [] for lev in levels}
            for tri in triangles:
                verts = [(float(t[0]), float(t[1]), float(t[2])) for t in tri]
                for lev in levels:
                    pts_on = []
                    for i in range(3):
                        a = verts[i]; b = verts[(i+1)%3]
                        za, zb = a[2], b[2]
                        if (za < lev and zb < lev) or (za > lev and zb > lev):
                            continue
                        if abs(zb - za) < 1e-12:
                            continue
                        t = (lev - za) / (zb - za)
                        if 0.0 <= t <= 1.0:
                            x = a[0] + (b[0]-a[0])*t
                            y = a[1] + (b[1]-a[1])*t
                            pts_on.append((x,y))
                    if len(pts_on) >= 2:
                        pA, pB = pts_on[0], pts_on[1]
                        if math.hypot(pA[0]-pB[0], pA[1]-pB[1]) > 1e-9:
                            segs[lev].append((pA,pB))
            return segs
        except Exception:
            traceback.print_exc()
            return {}

    # ---------- hover/interpolate z ----------
    def _interpolate_z_at(self, x: float, y: float) -> Optional[float]:
        try:
            tris = self.compute_triangulation()
            best_dist = None; best_z = None
            for tri in tris:
                (x0,y0,z0),(x1,y1,z1),(x2,y2,z2) = tri
                denom = (y1 - y2)*(x0 - x2) + (x2 - x1)*(y0 - y2)
                if abs(denom) < 1e-12:
                    continue
                a = ((y1 - y2)*(x - x2) + (x2 - x1)*(y - y2)) / denom
                b = ((y2 - y0)*(x - x2) + (x0 - x2)*(y - y2)) / denom
                c = 1 - a - b
                if a >= -1e-9 and b >= -1e-9 and c >= -1e-9:
                    return float(a*z0 + b*z1 + c*z2)
                mx = (x0+x1+x2)/3.0; my = (y0+y1+y2)/3.0
                d = (mx-x)**2 + (my-y)**2
                if best_dist is None or d < best_dist:
                    best_dist = d; best_z = (z0+z1+z2)/3.0
            if best_z is not None:
                return float(best_z)
            # fallback nearest point
            bd=None; bz=None
            for s in self.shapes:
                if s.get('type') != 'point': continue
                px,py = s['pos']; pz=float(s['data'].get('z',0.0))
                d=(px-x)**2+(py-y)**2
                if bd is None or d<bd:
                    bd=d; bz=pz
            return float(bz) if bz is not None else None
        except Exception:
            traceback.print_exc()
            return None

    # ---------- painting ----------
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.fillRect(self.rect(), Qt.white)

        # grid
        pen_grid = QPen(QColor(245,245,245))
        painter.setPen(pen_grid)
        step = 50
        for gx in range(0, self.width(), step):
            painter.drawLine(gx, 0, gx, self.height())
        for gy in range(0, self.height(), step):
            painter.drawLine(0, gy, self.width(), gy)

        # triangulation (computed)
        if self.show_triangulation:
            try:
                tris = self.compute_triangulation()
                pen_tri = QPen(QColor(120,140,200), 1)
                painter.setPen(pen_tri)
                for tri in tris:
                    s0 = self.world_to_screen(tri[0][0], tri[0][1])
                    s1 = self.world_to_screen(tri[1][0], tri[1][1])
                    s2 = self.world_to_screen(tri[2][0], tri[2][1])
                    painter.drawLine(s0[0], s0[1], s1[0], s1[1])
                    painter.drawLine(s1[0], s1[1], s2[0], s2[1])
                    painter.drawLine(s2[0], s2[1], s0[0], s0[1])
            except Exception:
                traceback.print_exc()

        # contours
        if self.show_contours:
            try:
                segs_by_level = self.compute_contours(main_interval=self.contour_main_interval,
                                                      sub_divisions=int(self.contour_sub_divisions))
                if segs_by_level:
                    levels_sorted = sorted(segs_by_level.keys())
                    for i, level in enumerate(levels_sorted):
                        segs = segs_by_level[level]
                        is_main = abs((level / max(1.0, float(self.contour_main_interval))) - round(level / max(1.0, float(self.contour_main_interval)))) < 1e-6
                        col = QColor(80,80,80) if is_main else QColor(150,150,150)
                        pen = QPen(col, 1)
                        painter.setPen(pen)
                        for seg in segs:
                            (ax,ay),(bx,by) = seg
                            sa = self.world_to_screen(ax, ay); sb = self.world_to_screen(bx, by)
                            painter.drawLine(sa[0], sa[1], sb[0], sb[1])
                        if is_main and segs and (self.contour_label_every <= 1 or (i % self.contour_label_every == 0)):
                            (ax,ay),(bx,by) = segs[0]
                            mx = (ax+bx)/2.0; my = (ay+by)/2.0
                            sx, sy = self.world_to_screen(mx, my)
                            font = QFont(self.label_font_name, max(6, int(self.label_font_size)))
                            painter.setFont(font)
                            painter.setPen(QPen(self.label_color))
                            painter.drawText(sx + 4, sy - 2, f"{level:g}")
            except Exception:
                traceback.print_exc()

        # boundaries
        pen_b = QPen(QColor(200,60,60), 2, Qt.DashLine)
        painter.setPen(pen_b)
        for bpoly in self.boundaries:
            if len(bpoly) < 2: continue
            for i in range(len(bpoly)-1):
                a = bpoly[i]; c = bpoly[i+1]
                sa = self.world_to_screen(a[0], a[1]); sb = self.world_to_screen(c[0], c[1])
                painter.drawLine(sa[0], sa[1], sb[0], sb[1])

        # points
        for shape in self.shapes:
            if shape.get('type') != 'point': continue
            x,y = shape['pos']; d = shape.get('data', {})
            sx, sy = self.world_to_screen(x, y)
            zval = float(d.get('z', 0.0))
            rz = max(0, min(255, 120 + int((zval % 50) * 2)))
            color = QColor(rz, 60, max(0, 200 - (rz // 4)))
            painter.setBrush(QBrush(color))
            painter.setPen(QPen(Qt.black, 1))
            painter.drawEllipse(sx - self.point_size//2, sy - self.point_size//2, self.point_size, self.point_size)

            label_parts = []
            if self.show_id: label_parts.append(str(d.get('id', '')))
            if self.show_x: label_parts.append(f"x={d.get('x', '')}")
            if self.show_y: label_parts.append(f"y={d.get('y', '')}")
            if self.show_z: label_parts.append(f"z={d.get('z', '')}")
            if self.show_code: label_parts.append(str(d.get('code', '')))
            if label_parts:
                painter.setPen(QPen(QColor(20,20,20)))
                font = QFont(self.label_font_name, max(7, int(self.label_font_size - 1)))
                painter.setFont(font)
                for i, lp in enumerate(label_parts):
                    painter.drawText(sx + 6, sy + (i * (self.label_font_size + 2)), lp)

        # manual triangles (filled)
        try:
            pen_tri = QPen(QColor(60,120,60), 1)
            brush_tri = QBrush(QColor(200, 230, 200, 80))
            painter.setPen(pen_tri); painter.setBrush(brush_tri)
            for tri in self.triangles:
                try:
                    p0 = self.shapes[tri[0]]; p1 = self.shapes[tri[1]]; p2 = self.shapes[tri[2]]
                    s0 = QPoint(*self.world_to_screen(p0['pos'][0], p0['pos'][1]))
                    s1 = QPoint(*self.world_to_screen(p1['pos'][0], p1['pos'][1]))
                    s2 = QPoint(*self.world_to_screen(p2['pos'][0], p2['pos'][1]))
                    qp = QPolygon([s0, s1, s2])
                    painter.drawPolygon(qp)
                except Exception:
                    continue
        except Exception:
            traceback.print_exc()

        # rubber band rectangle
        if self._show_rubber and self._rubber_start and self._rubber_current:
            pen = QPen(QColor(50,120,200))
            pen.setStyle(Qt.DashLine)
            painter.setPen(pen)
            r = QRect(self._rubber_start, self._rubber_current)
            painter.drawRect(r.normalized())

        # hover z overlay
        if self._hover_pos is not None and self._hover_z is not None:
            try:
                wx, wy = self._hover_pos
                sx, sy = self.world_to_screen(wx, wy)
                txt = f"Z = {self._hover_z:.3f}"
                font = QFont(self.label_font_name, max(7, int(self.label_font_size)))
                painter.setFont(font)
                painter.setPen(QPen(QColor(10,10,10)))
                metrics = painter.fontMetrics()
                wbox = metrics.horizontalAdvance(txt) + 8
                hbox = metrics.height() + 4
                painter.fillRect(sx + 8, sy - hbox - 8, wbox, hbox, QColor(255,255,240,230))
                painter.drawText(sx + 12, sy - 8, txt)
            except Exception:
                pass

    # ---------- helpers ----------
    def clear(self):
        self.shapes = []
        self.boundaries = []
        self.triangles = []
        self._cached_triangles = None
        self.update()

    def add_point(self, pid, x, y, z=0.0, code=""):
        try:
            pt = {'type': 'point', 'pos': (float(x), float(y)),
                  'data': {'id': pid, 'x': float(x), 'y': float(y), 'z': float(z), 'code': code}}
            self.shapes.append(pt)
            self._cached_triangles = None
            try:
                self.points_changed.emit()
            except Exception:
                pass
            self.update()
        except Exception:
            traceback.print_exc()

    def remove_point_by_id(self, pid):
        self.shapes = [s for s in self.shapes if not (s.get('type') == 'point' and str(s.get('data', {}).get('id')) == str(pid))]
        self._cached_triangles = None
        try:
            self.points_changed.emit()
        except Exception:
            pass
        self.update()

    def get_point_index_by_id(self, pid) -> Optional[int]:
        for i, s in enumerate(self.shapes):
            if s.get('type') == 'point' and str(s.get('data', {}).get('id')) == str(pid):
                return i
        return None

    def start_add_triangle_mode(self):
        self._add_triangle_mode = True
        self._add_triangle_clicks = []
        self._cached_triangles = None
        self.update()

    def set_delete_triangle_mode(self, flag: bool):
        self._delete_triangle_mode = bool(flag)
        self._cached_triangles = None
        self.update()

    def _find_nearest_point_index(self, sx: float, sy: float, max_px: int = 12) -> Optional[int]:
        best_idx = None; best_d = None
        for idx, shape in enumerate(self.shapes):
            if shape.get('type') != 'point': continue
            px, py = self.world_to_screen(shape['pos'][0], shape['pos'][1])
            d = math.hypot(px - sx, py - sy)
            if best_d is None or d < best_d:
                best_d = d; best_idx = idx
        if best_d is not None and best_d <= max_px:
            return best_idx
        return None

    def _find_nearest_triangle_edge(self, sx: float, sy: float, max_px: int = 8):
        best = (None, None); best_d = None
        for ti, tri in enumerate(self.triangles):
            try:
                pts = []
                for pidx in tri:
                    s = self.shapes[pidx]
                    pts.append(self.world_to_screen(s['pos'][0], s['pos'][1]))
                edges = [((pts[0][0],pts[0][1]), (pts[1][0],pts[1][1]), (tri[0],tri[1])),
                         ((pts[1][0],pts[1][1]), (pts[2][0],pts[2][1]), (tri[1],tri[2])),
                         ((pts[2][0],pts[2][1]), (pts[0][0],pts[0][1]), (tri[2],tri[0]))]
                for e in edges:
                    (ax,ay),(bx,by), idxs = e
                    vx = bx - ax; vy = by - ay
                    if vx == 0 and vy == 0: continue
                    t = ((sx - ax) * vx + (sy - ay) * vy) / (vx*vx + vy*vy)
                    t = max(0.0, min(1.0, t))
                    cx = ax + t * vx; cy = ay + t * vy
                    d = math.hypot(cx - sx, cy - sy)
                    if best_d is None or d < best_d:
                        best_d = d; best = (ti, idxs)
            except Exception:
                continue
        if best_d is not None and best_d <= max_px:
            return best
        return None

    # ---------- export helpers ----------
    def export_points_to_csv(self, file_path: str) -> bool:
        try:
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write("id,x,y,z,code\n")
                for s in self.shapes:
                    if s.get('type') != 'point': continue
                    d = s.get('data', {})
                    f.write(f"{d.get('id','')},{d.get('x','')},{d.get('y','')},{d.get('z','')},{d.get('code','')}\n")
            return True
        except Exception:
            traceback.print_exc()
            return False

    def export_contours_simple(self, file_path: str, main_interval=None, sub_divisions=None) -> bool:
        try:
            segs = self.compute_contours(main_interval=main_interval, sub_divisions=sub_divisions)
            with open(file_path, 'w', encoding='utf-8') as f:
                for lev, seglist in segs.items():
                    for a,b in seglist:
                        f.write(f"{lev},{a[0]},{a[1]},{b[0]},{b[1]}\n")
            return True
        except Exception:
            traceback.print_exc()
            return False
