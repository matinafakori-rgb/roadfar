# gui/main_window.py
# -*- coding: utf-8 -*-
from __future__ import annotations
import sys
import os
import json
import traceback
from pathlib import Path
from typing import Any, Dict, List, Tuple, Optional

from PyQt5.QtWidgets import (
    QMainWindow, QAction, QFileDialog, QMessageBox, QDockWidget, QWidget, QVBoxLayout,
    QTableWidget, QTableWidgetItem, QCheckBox, QTabWidget, QGroupBox, QHBoxLayout,
    QInputDialog, QLineEdit, QPushButton, QLabel, QSpinBox, QFormLayout, QDialog,
    QDialogButtonBox, QDoubleSpinBox
)
from PyQt5.QtCore import Qt, pyqtSignal

# try optional imports
try:
    import pandas as pd
    _HAS_PANDAS = True
except Exception:
    pd = None
    _HAS_PANDAS = False

# canvas import (required)
try:
    from gui.canvas import CanvasWidget
except Exception:
    # safe fallback minimal CanvasWidget (very small)
    from PyQt5.QtWidgets import QWidget
    class CanvasWidget(QWidget):
        def __init__(self, parent=None):
            super().__init__(parent)
            self.shapes = []
            self.contour_main_interval = 5.0
            self.contour_sub_divisions = 4
            self.show_triangulation = False
            self.show_contours = False
            self.boundaries = []
            self.triangles = []
        def fit_all(self): pass
        def compute_contours(self, main_interval=None, sub_divisions=None): return {}
        def start_add_triangle_mode(self): pass
        def set_delete_triangle_mode(self, f): pass
        def update(self): super().update()
        def export_points_to_csv(self, p): return False
        def export_contours_simple(self, p, *a, **k): return False

# plan_canvas import (optional)
try:
    from gui.plan_canvas import PlanCanvas
except Exception:
    # Minimal PlanCanvas stub to avoid crashes and allow testing
    from PyQt5.QtWidgets import QWidget
    class PlanCanvas(QWidget):
        def __init__(self, parent=None):
            super().__init__(parent)
            self.aln = {}
            self.shapes = []
            self.chainage_step = 10
            self._contours = {}
            self.mandatory_points = []
        def set_contours(self, contours):
            self._contours = contours
            self.update()
        def start_plan_drawing(self): pass
        def generate_suggested_route(self, params, mandatory_points):
            self.aln = {'name': params.get('name','suggested'), 'elements': []}
            for i, p in enumerate(mandatory_points):
                self.aln['elements'].append({'type':'pt','index':i,'pos':p})
            self.update(); QMessageBox.information(self, "مسیر پیشنهادی", "مسیر نمونه ایجاد شد (stub).")
        def fit_contours(self): pass
        def set_mode_select_tangents(self): pass
        def set_mode_edit(self): pass
        def set_chainage_step(self, v): self.chainage_step = int(v)
        def update(self): super().update()
        def start_select_mandatory(self, clear_previous=True): pass
        def get_mandatory_points(self): return []

# AlignmentParamsDialog: fallback simple dialog
try:
    from gui.alignment_dialogs import AlignmentParamsDialog
except Exception:
    class AlignmentParamsDialog(QDialog):
        """Fallback dialog to request a name and mandatory point ids (comma-separated)."""
        def __init__(self, parent=None):
            super().__init__(parent)
            self.setWindowTitle("پارامترهای پیشنهاد مسیر (ساده)")
            layout = QFormLayout(self)
            self.name_edit = QLineEdit("AutoRoute")
            self.mandatory_edit = QLineEdit("")
            layout.addRow("نام مسیر:", self.name_edit)
            layout.addRow("شناسه‌های نقاط اجباری (با کاما):", self.mandatory_edit)
            btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
            btns.accepted.connect(self.accept)
            btns.rejected.connect(self.reject)
            layout.addRow(btns)
        def get_values(self) -> Dict[str, Any]:
            txt = self.mandatory_edit.text().strip()
            ids = [s.strip() for s in txt.split(',') if s.strip()]
            return {'name': self.name_edit.text().strip(), 'mandatory_ids': ids}

# Project config
ROOT_DIR = Path(__file__).resolve().parents[1]
PROJECTS_DIR = ROOT_DIR / "projects"
RECENT_FILE = Path.home() / ".roadfar_recent.json"
PROJECTS_DIR.mkdir(parents=True, exist_ok=True)

def read_recent_list() -> List[str]:
    try:
        if RECENT_FILE.exists():
            with open(RECENT_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list):
                    return data
    except Exception:
        pass
    return []

def write_recent_list(lst: List[str]):
    try:
        with open(RECENT_FILE, "w", encoding="utf-8") as f:
            json.dump(lst, f, ensure_ascii=False, indent=2)
    except Exception:
        pass

# Settings dialog
class SettingsDialog(QDialog):
    def __init__(self, parent=None, show_points_table=True, contour_interval=5.0, contour_sub=4.0):
        super().__init__(parent)
        self.setWindowTitle("تنظیمات برنامه")
        layout = QFormLayout(self)
        self.chk_table = QCheckBox("نمایش جدول نقاط")
        self.chk_table.setChecked(bool(show_points_table))
        layout.addRow(self.chk_table)
        self.spin_cont_main = QDoubleSpinBox(); self.spin_cont_main.setRange(0.01, 10000.0); self.spin_cont_main.setDecimals(3)
        self.spin_cont_main.setValue(float(contour_interval))
        self.spin_cont_sub = QDoubleSpinBox(); self.spin_cont_sub.setRange(0.0, 100.0); self.spin_cont_sub.setDecimals(3)
        self.spin_cont_sub.setValue(float(contour_sub))
        layout.addRow("فاصلهٔ اصلی منحنی (m):", self.spin_cont_main)
        layout.addRow("تعداد فرعی بین خطوط اصلی:", self.spin_cont_sub)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept); buttons.rejected.connect(self.reject)
        layout.addRow(buttons)
    def get_values(self) -> Dict[str, Any]:
        return {
            'show_points_table': self.chk_table.isChecked(),
            'contour_interval': float(self.spin_cont_main.value()),
            'contour_sub': float(self.spin_cont_sub.value())
        }

# ----------------- Main Window -----------------
class CADMainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Roadfar — CAD Program")
        self.resize(1300, 900)

        # Tabs: points, surface, plan
        self.tabs = QTabWidget()
        self.setCentralWidget(self.tabs)

        # Points tab
        self.points_tab = QWidget()
        self.points_layout = QVBoxLayout(self.points_tab)
        self.points_canvas = CanvasWidget()
        self.points_canvas.mode = 'points'
        self.points_layout.addWidget(self.points_canvas)
        self.tabs.addTab(self.points_tab, "نمایش نقاط")

        # Surface tab
        self.surface_tab = QWidget()
        self.surface_layout = QVBoxLayout(self.surface_tab)
        self.surface_canvas = CanvasWidget()
        self.surface_canvas.mode = 'surface'
        self.surface_layout.addWidget(self.surface_canvas)
        self.tabs.addTab(self.surface_tab, "نمایش سطح")

        # Plan tab
        self.plan_tab = QWidget()
        self.plan_layout = QVBoxLayout(self.plan_tab)
        self.plan_canvas = PlanCanvas()
        self.plan_layout.addWidget(self.plan_canvas)
        self.tabs.addTab(self.plan_tab, "پلان / مسیر")

        # create docks BEFORE building menus (so menu toggles can reference them)
        self.create_points_dock()
        self.create_surface_dock()
        self.create_plan_dock()

        # build menus
        self.build_menus()

        # connections
        self.tabs.currentChanged.connect(self.on_tab_changed)
        # ensure initial state
        self.on_tab_changed(0)

    # ---------- Menus ----------
    def build_menus(self):
        menubar = self.menuBar()

        # File menu
        file_menu = menubar.addMenu("فایل")
        new_proj = QAction("پروژه جدید...", self); new_proj.triggered.connect(self.create_project)
        open_proj = QAction("باز کردن پروژه...", self); open_proj.triggered.connect(self.open_project)
        recent = QAction("پروژه‌های اخیر...", self); recent.triggered.connect(self.show_recent_projects)
        file_menu.addAction(new_proj); file_menu.addAction(open_proj); file_menu.addAction(recent)
        file_menu.addSeparator()
        save_all = QAction("ذخیره پروژه جاری...", self); save_all.triggered.connect(self.save_current_project)
        file_menu.addAction(save_all)
        file_menu.addSeparator()
        exit_action = QAction("خروج", self); exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)

        # Points menu
        points_menu = menubar.addMenu("نقاط")
        imp = QAction("اضافه کردن نقاط...", self); imp.triggered.connect(self.import_points)
        del_all = QAction("حذف همه نقاط", self); del_all.triggered.connect(self.delete_all_points)
        toggle_tbl = QAction("نمایش/مخفی جدول نقاط", self); toggle_tbl.setCheckable(True); toggle_tbl.setChecked(self.points_dock.isVisible())
        toggle_tbl.triggered.connect(self.toggle_points_dock)
        points_menu.addAction(imp); points_menu.addAction(del_all); points_menu.addAction(toggle_tbl)

        export_menu = points_menu.addMenu("خروجی نقاط")
        for fmt in ("CSV","TXT","GSI"):
            a = QAction(fmt, self); a.triggered.connect(lambda ch, f=fmt: self.export_points(f))
            export_menu.addAction(a)

        filter_menu = points_menu.addMenu("فیلتر و مرتب‌سازی")
        sort_action = QAction("مرتب‌سازی", self); sort_action.triggered.connect(self.sort_points)
        filter_action = QAction("فیلتر بازه‌ای", self); filter_action.triggered.connect(self.filter_points)
        modify_action = QAction("اصلاح جمعی X/Y/Z", self); modify_action.triggered.connect(self.modify_coordinates)
        filter_menu.addAction(sort_action); filter_menu.addAction(filter_action); filter_menu.addAction(modify_action)

        # Surface menu
        surface_menu = menubar.addMenu("سطح")
        create_surface_action = QAction("ایجاد سطح از نقاط...", self); create_surface_action.triggered.connect(self.create_surface)
        save_surface_action = QAction("ذخیره سطح...", self); save_surface_action.triggered.connect(self.save_surface)
        load_surface_action = QAction("بارگذاری سطح...", self); load_surface_action.triggered.connect(self.load_surface)
        delete_surface_action = QAction("حذف سطح...", self); delete_surface_action.triggered.connect(self.delete_surface)
        surface_menu.addAction(create_surface_action); surface_menu.addAction(save_surface_action); surface_menu.addAction(load_surface_action); surface_menu.addAction(delete_surface_action)

        tri_menu = surface_menu.addMenu("مثلث‌بندی")
        tri_compute = QAction("محاسبه مثلث‌بندی", self); tri_compute.triggered.connect(self._surface_compute_triangulation)
        tri_show = QAction("نمایش/مخفی مثلث‌بندی", self); tri_show.setCheckable(True); tri_show.setChecked(self.surface_canvas.show_triangulation)
        tri_show.toggled.connect(lambda ch: self._set_surface_flag('show_triangulation', ch))
        tri_manual_add = QAction("افزودن مثلث با 3 نقطه", self); tri_manual_add.triggered.connect(self.surface_start_add_triangle)
        tri_manual_del = QAction("حذف مثلث با کلیک", self); tri_manual_del.setCheckable(True); tri_manual_del.toggled.connect(self.surface_toggle_delete_triangle_mode)
        tri_menu.addAction(tri_compute); tri_menu.addAction(tri_show); tri_menu.addAction(tri_manual_add); tri_menu.addAction(tri_manual_del)

        contour_menu = surface_menu.addMenu("منحنی‌میزان")
        contour_compute = QAction("محاسبه منحنی‌میزان", self); contour_compute.triggered.connect(self._surface_compute_contours)
        contour_params = QAction("تنظیم فواصل منحنی‌ها...", self); contour_params.triggered.connect(self.set_contour_intervals)
        contour_menu.addAction(contour_compute); contour_menu.addAction(contour_params)

        # Alignment / Plan
        align_menu = menubar.addMenu("الایمنت / پلان")
        new_align = QAction("ایجاد مسیر جدید...", self); new_align.triggered.connect(self.create_new_alignment)
        open_align = QAction("باز کردن مسیر...", self); open_align.triggered.connect(self.open_alignment)
        save_align = QAction("ذخیره مسیر جاری...", self); save_align.triggered.connect(self.save_alignment)
        align_menu.addAction(new_align); align_menu.addAction(open_align); align_menu.addAction(save_align)

        plan_menu = align_menu.addMenu("پلان")
        show_contours_plan = QAction("نمایش منحنی سطح در تب پلان", self)
        show_contours_plan.triggered.connect(self.show_surface_contours_in_plan)
        plan_menu.addAction(show_contours_plan)
        draw_plan_action = QAction("رسم پلان دستی (شروع)", self)
        draw_plan_action.triggered.connect(lambda: self.plan_canvas.start_plan_drawing() if hasattr(self.plan_canvas,'start_plan_drawing') else QMessageBox.warning(self,"خطا","قابلیت رسم پلان در این نسخه موجود نیست."))
        plan_menu.addAction(draw_plan_action)
        auto_route_action = QAction("پیشنهاد مسیر خودکار", self)
        auto_route_action.triggered.connect(self.on_auto_route_requested)
        plan_menu.addAction(auto_route_action)
        select_mand_action = QAction("انتخاب نقاط اجباری...", self)
        select_mand_action.triggered.connect(lambda: self.plan_canvas.start_select_mandatory(True) if hasattr(self.plan_canvas,'start_select_mandatory') else QMessageBox.information(self,"خطا","قابلیت انتخاب نقاط اجباری موجود نیست."))
        plan_menu.addAction(select_mand_action)
        clear_mand_action = QAction("پاک کردن نقاط اجباری", self)
        clear_mand_action.triggered.connect(lambda: (setattr(self.plan_canvas,'mandatory_points',[]), self.plan_canvas.update()) if hasattr(self.plan_canvas,'mandatory_points') else None)
        plan_menu.addAction(clear_mand_action)
        plan_menu.addSeparator()
        del_plan_action = QAction("حذف پلان جاری", self); del_plan_action.triggered.connect(self.delete_plan)
        plan_menu.addAction(del_plan_action)
        del_vertex_action = QAction("حذف رأس پلان (شماره/نزدیک‌ترین)", self); del_vertex_action.triggered.connect(self.delete_plan_vertex_prompt)
        plan_menu.addAction(del_vertex_action)

        # Settings
        settings_menu = menubar.addMenu("تنظیمات")
        edit_settings = QAction("پیکربندی برنامه...", self); edit_settings.triggered.connect(self.open_settings_dialog)
        settings_menu.addAction(edit_settings)

    # ---------- tab changed ----------
    def on_tab_changed(self, idx: int):
        if idx == 0:
            self.current_canvas = self.points_canvas
        elif idx == 1:
            self.current_canvas = self.surface_canvas
        elif idx == 2:
            self.current_canvas = self.plan_canvas
        else:
            self.current_canvas = None
        self.refresh_points_table()

    # ---------- points dock ----------
    def create_points_dock(self):
        self.points_dock = QDockWidget("لیست نقاط و کنترل‌ها", self)
        self.points_dock.setAllowedAreas(Qt.LeftDockWidgetArea | Qt.RightDockWidgetArea)
        w = QWidget(); v = QVBoxLayout(w)
        self.show_table_checkbox = QCheckBox("نمایش جدول نقاط"); self.show_table_checkbox.setChecked(True)
        self.show_table_checkbox.stateChanged.connect(lambda st: self.points_dock.setVisible(st == 2))
        v.addWidget(self.show_table_checkbox)
        self.points_table = QTableWidget(); self.points_table.setColumnCount(5)
        self.points_table.setHorizontalHeaderLabels(["ID","X","Y","Z","Code"]); self.points_table.verticalHeader().setVisible(False)
        v.addWidget(self.points_table)
        cols_group = QGroupBox("نمایش ستون‌ها"); cols_layout = QHBoxLayout()
        self.chk_id = QCheckBox("ID"); self.chk_id.setChecked(True)
        self.chk_x = QCheckBox("X"); self.chk_x.setChecked(True)
        self.chk_y = QCheckBox("Y"); self.chk_y.setChecked(True)
        self.chk_z = QCheckBox("Z"); self.chk_z.setChecked(True)
        self.chk_code = QCheckBox("Code"); self.chk_code.setChecked(True)
        for chk in (self.chk_id, self.chk_x, self.chk_y, self.chk_z, self.chk_code):
            chk.stateChanged.connect(self.on_column_checkbox_changed); cols_layout.addWidget(chk)
        cols_group.setLayout(cols_layout); v.addWidget(cols_group)
        self.points_dock.setWidget(w); self.addDockWidget(Qt.RightDockWidgetArea, self.points_dock)

    def toggle_points_dock(self):
        if hasattr(self, 'points_dock'):
            vis = not self.points_dock.isVisible()
            self.points_dock.setVisible(vis)
            if hasattr(self, 'show_table_checkbox'):
                self.show_table_checkbox.setChecked(vis)

    def on_column_checkbox_changed(self):
        if not hasattr(self, 'points_table'):
            return
        for idx, chk in enumerate([self.chk_id, self.chk_x, self.chk_y, self.chk_z, self.chk_code]):
            try:
                self.points_table.setColumnHidden(idx, not chk.isChecked())
            except Exception:
                pass
        for cv in (getattr(self, 'points_canvas', None), getattr(self, 'surface_canvas', None)):
            if cv is None: continue
            cv.show_id = self.chk_id.isChecked()
            cv.show_x = self.chk_x.isChecked()
            cv.show_y = self.chk_y.isChecked()
            cv.show_z = self.chk_z.isChecked()
            cv.show_code = self.chk_code.isChecked()
            try: cv.update()
            except Exception: pass

    def refresh_points_table(self):
        if not hasattr(self, 'points_table'):
            return
        canvas = getattr(self, 'current_canvas', None) or getattr(self, 'points_canvas', None)
        if canvas is None:
            self.points_table.setRowCount(0); return
        shapes = getattr(canvas, 'shapes', [])
        pts = [s for s in shapes if isinstance(s, dict) and s.get('type') == 'point']
        try:
            self.points_table.setRowCount(len(pts))
            for i, s in enumerate(pts):
                d = s.get('data', {})
                self.points_table.setItem(i, 0, QTableWidgetItem(str(d.get('id', ''))))
                self.points_table.setItem(i, 1, QTableWidgetItem(str(d.get('x', ''))))
                self.points_table.setItem(i, 2, QTableWidgetItem(str(d.get('y', ''))))
                self.points_table.setItem(i, 3, QTableWidgetItem(str(d.get('z', ''))))
                self.points_table.setItem(i, 4, QTableWidgetItem(str(d.get('code', ''))))
            self.on_column_checkbox_changed()
        except Exception:
            traceback.print_exc()
            self.points_table.setRowCount(0)

    # ---------- surface dock ----------
    def create_surface_dock(self):
        self.surface_dock = QDockWidget("کنترل‌های سطح", self)
        self.surface_dock.setAllowedAreas(Qt.LeftDockWidgetArea | Qt.RightDockWidgetArea)
        w = QWidget(); v = QVBoxLayout(w)
        cont_group = QGroupBox("پارامترهای منحنی‌میزان")
        f = QFormLayout()
        self.spin_cont_main = QDoubleSpinBox(); self.spin_cont_main.setRange(0.01, 10000.0); self.spin_cont_main.setDecimals(3)
        self.spin_cont_main.setValue(float(getattr(self, 'surface_canvas', None).contour_main_interval if hasattr(self, 'surface_canvas') else 5.0))
        self.spin_cont_sub = QDoubleSpinBox(); self.spin_cont_sub.setRange(0.0, 100.0); self.spin_cont_sub.setDecimals(3)
        self.spin_cont_sub.setValue(float(getattr(self, 'surface_canvas', None).contour_sub_divisions if hasattr(self, 'surface_canvas') else 4.0))
        f.addRow("فاصلهٔ اصلی (m):", self.spin_cont_main)
        f.addRow("تعداد فرعی بین اصلی:", self.spin_cont_sub)
        cont_group.setLayout(f); v.addWidget(cont_group)

        lbl_group = QGroupBox("لیبل منحنی‌ها"); lf = QFormLayout()
        self.spin_label_every = QSpinBox(); self.spin_label_every.setRange(1, 100); self.spin_label_every.setValue(1)
        self.spin_label_size = QSpinBox(); self.spin_label_size.setRange(6, 72); self.spin_label_size.setValue(9)
        lf.addRow("برچسب هر چند خط اصلی:", self.spin_label_every)
        lf.addRow("اندازه فونت:", self.spin_label_size)
        lbl_group.setLayout(lf); v.addWidget(lbl_group)

        btn_compute_tri = QPushButton("محاسبه مثلث‌بندی"); btn_compute_tri.clicked.connect(self._surface_compute_triangulation)
        btn_compute_cont = QPushButton("محاسبه منحنی‌ها"); btn_compute_cont.clicked.connect(self._surface_compute_contours)
        v.addWidget(btn_compute_tri); v.addWidget(btn_compute_cont)

        tri_manual_box = QGroupBox("کنترل مثلث‌بندی دستی"); tri_layout = QHBoxLayout()
        btn_add_tri = QPushButton("افزودن مثلث (3 کلیک)"); btn_add_tri.clicked.connect(self.surface_start_add_triangle)
        self.chk_del_tri = QCheckBox("حالت حذف ضلع"); self.chk_del_tri.stateChanged.connect(lambda st: self.surface_toggle_delete_triangle_mode(st==2))
        tri_layout.addWidget(btn_add_tri); tri_layout.addWidget(self.chk_del_tri)
        tri_manual_box.setLayout(tri_layout); v.addWidget(tri_manual_box)

        self.surface_dock.setWidget(w); self.addDockWidget(Qt.RightDockWidgetArea, self.surface_dock)

    # ---------- plan dock ----------
    def create_plan_dock(self):
        self.plan_dock = QDockWidget("کنترل‌های پلان", self)
        self.plan_dock.setAllowedAreas(Qt.LeftDockWidgetArea | Qt.RightDockWidgetArea)
        w = QWidget(); v = QVBoxLayout(w)
        btn_select_t = QPushButton("انتخاب تانژانت‌ها برای قوس"); btn_select_t.clicked.connect(lambda: self.plan_canvas.set_mode_select_tangents() if hasattr(self.plan_canvas,'set_mode_select_tangents') else QMessageBox.information(self,"اطلاع","این‌یک دکمهٔ نمونه است."))
        btn_edit_mode = QPushButton("حالت ویرایش الایمنت"); btn_edit_mode.clicked.connect(lambda: self.plan_canvas.set_mode_edit() if hasattr(self.plan_canvas,'set_mode_edit') else QMessageBox.information(self,"اطلاع","این‌یک دکمهٔ نمونه است."))
        btn_select_mp = QPushButton("انتخاب نقاط اجباری (کلیک روی منحنی)"); btn_select_mp.clicked.connect(lambda: self.plan_canvas.start_select_mandatory(True) if hasattr(self.plan_canvas,'start_select_mandatory') else QMessageBox.information(self,"اطلاع","این‌نسخه این قابلیت را ندارد."))
        v.addWidget(btn_select_t); v.addWidget(btn_edit_mode); v.addWidget(btn_select_mp)
        v.addWidget(QLabel("فاصله لیبل کیلومتراژ (m):"))
        spin = QSpinBox(); spin.setRange(1,1000); spin.setValue(int(getattr(self.plan_canvas,'chainage_step',10))); spin.valueChanged.connect(lambda val: self.plan_canvas.set_chainage_step(val) if hasattr(self.plan_canvas,'set_chainage_step') else None)
        v.addWidget(spin); self.plan_dock.setWidget(w); self.addDockWidget(Qt.RightDockWidgetArea, self.plan_dock)

    # ---------- points operations ----------
    def import_points(self):
        fp, _ = QFileDialog.getOpenFileName(self, "انتخاب فایل نقاط", "", "All Files (*);;Text Files (*.txt);;CSV Files (*.csv);;Excel Files (*.xls *.xlsx);;GSI Files (*.gsi)")
        if not fp: return
        try:
            added = 0
            if _HAS_PANDAS and fp.lower().endswith(('.xls','.xlsx')):
                df = pd.read_excel(fp)
                col_map = {}
                for c in list(df.columns):
                    low = str(c).strip().lower()
                    if low in ['id','point','no','شماره']: col_map[c]='id'
                    elif low in ['x','east','easting']: col_map[c]='x'
                    elif low in ['y','north','northing']: col_map[c]='y'
                    elif low in ['z','elev','height','ارتفاع']: col_map[c]='z'
                    elif low in ['code','label','کد']: col_map[c]='code'
                df.rename(columns=col_map, inplace=True)
                required = ['id','x','y','z','code']
                if not all(c in df.columns for c in required):
                    QMessageBox.warning(self, "خطا", f"ستون‌های مورد نیاز {required} یافت نشد.")
                    return
                for _, row in df.iterrows():
                    try: pid = int(row['id'])
                    except: pid = row['id']
                    x = float(row['x']); y = float(row['y']); z = float(row['z']); code = str(row['code'])
                    self.points_canvas.shapes.append({'type':'point','pos':(x,y),'data':{'id':pid,'x':x,'y':y,'z':z,'code':code}})
                    added += 1
            else:
                with open(fp,'r',encoding='utf-8',errors='ignore') as f:
                    for ln in f:
                        ln = ln.strip()
                        if not ln: continue
                        parts = [p.strip() for p in ln.split(',')] if ',' in ln else ln.split()
                        if len(parts) < 5: continue
                        pid = parts[0]
                        try: x = float(parts[1]); y = float(parts[2]); z = float(parts[3])
                        except: continue
                        code = parts[4]
                        try: pid2 = int(pid)
                        except: pid2 = pid
                        self.points_canvas.shapes.append({'type':'point','pos':(x,y),'data':{'id':pid2,'x':x,'y':y,'z':z,'code':code}})
                        added += 1
            if added > 0:
                try: self.points_canvas.fit_all()
                except: pass
                self.points_canvas.update(); self.refresh_points_table()
                QMessageBox.information(self, "موفقیت", f"{added} نقطه وارد شد.")
            else:
                QMessageBox.warning(self, "خطا", "هیچ نقطه‌ای از فایل خوانده نشد.")
        except Exception:
            traceback.print_exc(); QMessageBox.critical(self, "خطا", "خطا در وارد کردن فایل نقاط.")

    def delete_all_points(self):
        reply = QMessageBox.question(self, "حذف همه نقاط", "آیا مطمئن هستید؟", QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if reply != QMessageBox.Yes: return
        try:
            for cv in (self.points_canvas, self.surface_canvas):
                cv.shapes = [s for s in getattr(cv, 'shapes', []) if s.get('type') != 'point']
                if hasattr(cv, '_cached_triangles'): cv._cached_triangles = None
                cv.update()
        except Exception:
            traceback.print_exc()
        self.refresh_points_table()

    def export_points(self, fmt):
        fmt = fmt.upper()
        if fmt not in ("CSV","TXT","GSI"): fmt="CSV"
        fp, _ = QFileDialog.getSaveFileName(self, "ذخیره نقاط", f"points.{fmt.lower()}", f"{fmt} Files (*.{fmt.lower()})")
        if not fp: return
        try:
            pts = [s['data'] for s in self.points_canvas.shapes if s.get('type')=='point']
            if _HAS_PANDAS:
                df = pd.DataFrame(pts)
                if fmt=="CSV": df.to_csv(fp,index=False)
                else: df.to_csv(fp,index=False,sep=' ')
            else:
                with open(fp,'w',encoding='utf-8') as f:
                    for p in pts:
                        f.write(f"{p.get('id','')}, {p.get('x','')}, {p.get('y','')}, {p.get('z','')}, {p.get('code','')}\n")
            QMessageBox.information(self, "موفقیت", "فایل ذخیره شد.")
        except Exception:
            traceback.print_exc(); QMessageBox.warning(self, "خطا", "خطا در ذخیره نقاط.")

    def sort_points(self):
        cols = ["id","x","y","z","code"]
        col, ok = QInputDialog.getItem(self, "مرتب‌سازی", "انتخاب ستون:", cols, 0, False)
        if not ok: return
        order, ok2 = QInputDialog.getItem(self, "ترتیب", "صعودی یا نزولی؟", ["صعودی","نزولی"], 0, False)
        if not ok2: return
        rev = (order == "نزولی")
        try:
            self.points_canvas.shapes.sort(key=lambda s: s['data'].get(col,0), reverse=rev)
            self.points_canvas.update(); self.refresh_points_table()
        except Exception:
            traceback.print_exc(); QMessageBox.warning(self, "خطا", "مرتب‌سازی انجام نشد.")

    def filter_points(self):
        cols = ["id","x","y","z"]
        col, ok = QInputDialog.getItem(self, "فیلتر بازه‌ای", "ستون:", cols, 0, False)
        if not ok: return
        minv, ok1 = QInputDialog.getDouble(self, "حداقل", "مقدار حداقل:", 0.0, -1e12, 1e12, 6)
        if not ok1: return
        maxv, ok2 = QInputDialog.getDouble(self, "حداکثر", "مقدار حداکثر:", minv, -1e12, 1e12, 6)
        if not ok2: return
        try:
            self.points_canvas.shapes = [s for s in self.points_canvas.shapes if s.get('type')!='point' or (minv <= float(s['data'].get(col,0)) <= maxv)]
            self.points_canvas.update(); self.refresh_points_table()
        except Exception:
            traceback.print_exc(); QMessageBox.warning(self, "خطا", "فیلتر انجام نشد.")

    def modify_coordinates(self):
        dx, ok1 = QInputDialog.getDouble(self, "اصلاح X", "مقدار:", 0.0, -1e9, 1e9, 6)
        if not ok1: return
        dy, ok2 = QInputDialog.getDouble(self, "اصلاح Y", "مقدار:", 0.0, -1e9, 1e9, 6)
        if not ok2: return
        dz, ok3 = QInputDialog.getDouble(self, "اصلاح Z", "مقدار:", 0.0, -1e9, 1e9, 6)
        if not ok3: return
        try:
            for s in self.points_canvas.shapes:
                if s.get('type')=='point':
                    s['data']['x'] = float(s['data'].get('x',0.0)) + dx
                    s['data']['y'] = float(s['data'].get('y',0.0)) + dy
                    s['data']['z'] = float(s['data'].get('z',0.0)) + dz
                    s['pos'] = (s['data']['x'], s['data']['y'])
            if hasattr(self.points_canvas,'_cached_triangles'): self.points_canvas._cached_triangles = None
            self.points_canvas.update()
            self.refresh_points_table()
        except Exception:
            traceback.print_exc(); QMessageBox.warning(self, "خطا", "اصلاح انجام نشد.")

    # ---------- surface operations ----------
    def create_surface(self):
        pts = [s for s in self.points_canvas.shapes if s.get('type')=='point']
        if not pts:
            QMessageBox.information(self, "ایجاد سطح", "ابتدا نقاط را وارد کنید.")
            return
        self.surface_canvas.shapes = [{'type':'point','pos':s['pos'],'data':dict(s['data'])} for s in pts]
        try:
            if hasattr(self,'spin_cont_main'): self.surface_canvas.contour_main_interval = float(self.spin_cont_main.value())
            if hasattr(self,'spin_cont_sub'): self.surface_canvas.contour_sub_divisions = float(self.spin_cont_sub.value())
            self.surface_canvas.show_triangulation = True
            self.surface_canvas.show_contours = True
            try: self.surface_canvas.fit_all()
            except: pass
            self.surface_canvas.update()
            try:
                contours = self.surface_canvas.compute_contours(main_interval=self.surface_canvas.contour_main_interval,
                                                               sub_divisions=int(self.surface_canvas.contour_sub_divisions))
                if hasattr(self, 'plan_canvas'):
                    self.plan_canvas.set_contours(contours)
            except Exception as e_c:
                traceback.print_exc()
                QMessageBox.warning(self, "هشدار", f"سطح ساخته شد ولی محاسبه منحنی‌ها مشکل داشت.\n{e_c}")
            QMessageBox.information(self, "ایجاد سطح", f"{len(pts)} نقطه منتقل و سطح ساخته شد.")
        except Exception:
            traceback.print_exc(); QMessageBox.warning(self, "هشدار", "سطح ساخته شد اما محاسبات با خطا روبرو شد.")

    def save_surface(self):
        fp, _ = QFileDialog.getSaveFileName(self, "ذخیره سطح...", str(PROJECTS_DIR / "surface.json"), "JSON Files (*.json)")
        if not fp: return
        try:
            serial = {'points':[s['data'] for s in self.surface_canvas.shapes if s.get('type')=='point'],
                      'boundaries': getattr(self.surface_canvas,'boundaries',[]),
                      'triangles': getattr(self.surface_canvas,'triangles',[])}
            with open(fp,'w',encoding='utf-8') as f: json.dump(serial,f,ensure_ascii=False,indent=2)
            QMessageBox.information(self, "ذخیره سطح", "ذخیره انجام شد.")
        except Exception:
            traceback.print_exc(); QMessageBox.warning(self,"خطا","ذخیره سطح انجام نشد.")

    def load_surface(self):
        fp, _ = QFileDialog.getOpenFileName(self, "بارگذاری سطح...", str(PROJECTS_DIR), "JSON Files (*.json);;All Files (*)")
        if not fp: return
        try:
            with open(fp,'r',encoding='utf-8') as f: data = json.load(f)
            pts = data.get('points', [])
            self.surface_canvas.shapes = []
            for p in pts:
                try:
                    x = float(p.get('x', p.get('X',0))); y = float(p.get('y', p.get('Y',0)))
                except Exception:
                    x = float(p.get('lon',0)); y = float(p.get('lat',0))
                pid = p.get('id',''); z = p.get('z',0); code = p.get('code','')
                self.surface_canvas.shapes.append({'type':'point','pos':(x,y),'data':{'id':pid,'x':x,'y':y,'z':z,'code':code}})
            self.surface_canvas.boundaries = data.get('boundaries', [])
            self.surface_canvas.triangles = data.get('triangles', [])
            try: self.surface_canvas.fit_all()
            except: pass
            self.surface_canvas.update()
            try:
                contours = self.surface_canvas.compute_contours(main_interval=self.surface_canvas.contour_main_interval,
                                                               sub_divisions=int(self.surface_canvas.contour_sub_divisions))
                if hasattr(self, 'plan_canvas'):
                    self.plan_canvas.set_contours(contours)
            except Exception as e:
                traceback.print_exc()
                QMessageBox.warning(self, "هشدار", f"سطح بارگذاری شد اما محاسبه منحنی‌ها مشکل داشت.\n{e}")
            QMessageBox.information(self,"بارگذاری سطح","بارگذاری انجام شد.")
        except Exception:
            traceback.print_exc(); QMessageBox.warning(self,"خطا","بارگذاری سطح انجام نشد.")

    def delete_surface(self):
        reply = QMessageBox.question(self,"حذف سطح","مطمئن هستید؟",QMessageBox.Yes|QMessageBox.No,QMessageBox.No)
        if reply != QMessageBox.Yes: return
        self.surface_canvas.shapes = [s for s in getattr(self.surface_canvas,'shapes',[]) if s.get('type')!='point']
        self.surface_canvas.boundaries = []
        self.surface_canvas.triangles = []
        if hasattr(self.surface_canvas,'_cached_triangles'): self.surface_canvas._cached_triangles = None
        self.surface_canvas.update(); QMessageBox.information(self,"حذف سطح","سطح حذف شد.")

    def _surface_compute_triangulation(self):
        try:
            self.surface_canvas.show_triangulation = True
            if hasattr(self.surface_canvas,'_cached_triangles'): self.surface_canvas._cached_triangles = None
            self.surface_canvas.update(); QMessageBox.information(self,"مثلث‌بندی","درخواست ثبت شد.")
        except Exception:
            traceback.print_exc(); QMessageBox.warning(self,"خطا","محاسبه مثلث‌بندی انجام نشد.")

    def _surface_compute_contours(self):
        try:
            if hasattr(self,'spin_cont_main'): self.surface_canvas.contour_main_interval = float(self.spin_cont_main.value())
            if hasattr(self,'spin_cont_sub'): self.surface_canvas.contour_sub_divisions = float(self.spin_cont_sub.value())
            if hasattr(self,'spin_label_every'): self.surface_canvas.contour_label_every = int(self.spin_label_every.value())
            if hasattr(self,'spin_label_size'): self.surface_canvas.label_font_size = int(self.spin_label_size.value())
            self.surface_canvas.show_contours = True
            self.surface_canvas.update(); QMessageBox.information(self,"منحنی‌میزان","درخواست ثبت شد.")
        except Exception:
            traceback.print_exc(); QMessageBox.warning(self,"خطا","محاسبه منحنی‌ها انجام نشد.")

    def _set_surface_flag(self, flag, value):
        try:
            setattr(self.surface_canvas, flag, bool(value)); self.surface_canvas.update()
        except Exception:
            traceback.print_exc()

    def set_contour_intervals(self):
        dlg = QDialog(self); dlg.setWindowTitle("تنظیم فواصل منحنی‌ها"); f = QFormLayout(dlg)
        main_spin = QDoubleSpinBox(); main_spin.setRange(0.01,10000); main_spin.setDecimals(3); main_spin.setValue(float(getattr(self.surface_canvas,'contour_main_interval',5.0)))
        sub_spin = QDoubleSpinBox(); sub_spin.setRange(0.0,100.0); sub_spin.setDecimals(3); sub_spin.setValue(float(getattr(self.surface_canvas,'contour_sub_divisions',4.0)))
        f.addRow("فاصلهٔ اصلی (m):", main_spin); f.addRow("تعداد فرعی بین اصلی:", sub_spin)
        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel); f.addRow(btns)
        btns.accepted.connect(dlg.accept); btns.rejected.connect(dlg.reject)
        if dlg.exec_() == QDialog.Accepted:
            self.surface_canvas.contour_main_interval = float(main_spin.value()); self.surface_canvas.contour_sub_divisions = float(sub_spin.value())
            self.surface_canvas.update(); QMessageBox.information(self,"تنظیم","پارامترها اعمال شد.")

    # ---------- triangle manual ----------
    def surface_start_add_triangle(self):
        sc = getattr(self, 'surface_canvas', None)
        if sc is None:
            QMessageBox.warning(self, "خطا", "کنسول سطح موجود نیست.")
            return
        if hasattr(sc, 'start_add_triangle_mode'):
            sc.start_add_triangle_mode()
            QMessageBox.information(self, "حالت افزودن مثلث", "حالت افزودن مثلث فعال است: سه نقطه کلیک کنید.")
        else:
            QMessageBox.warning(self, "پشتیبانی نشده", "این نسخه از کانواس از افزودن مثلث پویا پشتیبانی نمی‌کند.")

    def surface_toggle_delete_triangle_mode(self, checked: bool):
        sc = getattr(self, 'surface_canvas', None)
        if sc is None:
            return
        if hasattr(sc, 'set_delete_triangle_mode'):
            sc.set_delete_triangle_mode(bool(checked))
            if checked:
                QMessageBox.information(self, "حالت حذف مثلث", "حالت حذف مثلث فعال شد — روی ضلع کلیک کنید تا حذف شود.")
            else:
                QMessageBox.information(self, "حالت حذف مثلث", "حالت حذف مثلث غیر فعال شد.")
        else:
            QMessageBox.warning(self, "پشتیبانی نشده", "کانواس فعلی این قابلیت را ندارد.")

    # ---------- plan helpers ----------
    def show_surface_contours_in_plan(self):
        try:
            contours = self.surface_canvas.compute_contours(main_interval=self.surface_canvas.contour_main_interval,
                                                            sub_divisions=int(self.surface_canvas.contour_sub_divisions))
            if hasattr(self, 'plan_canvas'):
                self.plan_canvas.set_contours(contours)
                self.tabs.setCurrentWidget(self.plan_tab)
                QMessageBox.information(self, "نمایش در پلان", "منحنی‌ها به تب پلان منتقل و نمایش داده شدند.")
            else:
                QMessageBox.warning(self, "خطا", "پلان کانواس یافت نشد.")
        except Exception:
            traceback.print_exc()
            QMessageBox.warning(self, "خطا", "محاسبه/نمایش منحنی‌ها در پلان انجام نشد.")

    # ---------- alignment minimal handlers ----------
    def create_new_alignment(self):
        base_folder = QFileDialog.getExistingDirectory(self, "انتخاب پوشه برای ذخیره مسیر (پوشه پایه)")
        if not base_folder: return
        name, ok = QInputDialog.getText(self,"نام مسیر جدید","نام مسیر:")
        if not ok or not name.strip(): return
        proj_dir = Path(base_folder) / name.strip()
        try:
            proj_dir.mkdir(parents=True, exist_ok=True)
            with open(proj_dir / f"{name}.alignment.json","w",encoding='utf-8') as f: json.dump({'name':name,'elements':[]}, f, ensure_ascii=False, indent=2)
            QMessageBox.information(self,"ایجاد مسیر", f"مسیر '{name}' ایجاد شد.")
            recent = read_recent_list(); pstr = str(proj_dir)
            if pstr in recent: recent.remove(pstr)
            recent.insert(0,pstr); write_recent_list(recent)
        except Exception:
            traceback.print_exc(); QMessageBox.warning(self,"خطا","ایجاد مسیر انجام نشد.")

    def open_alignment(self):
        fp, _ = QFileDialog.getOpenFileName(self,"باز کردن مسیر...", str(PROJECTS_DIR), "Alignment Files (*.alignment.json);;JSON Files (*.json)")
        if not fp: return
        try:
            with open(fp,'r',encoding='utf-8') as f: data = json.load(f)
            if hasattr(self.plan_canvas,'aln'): self.plan_canvas.aln = data
            # If plan_canvas has from_dict method:
            if hasattr(self.plan_canvas, 'from_dict') and isinstance(data, dict):
                try:
                    self.plan_canvas.from_dict(data)
                except Exception:
                    pass
            QMessageBox.information(self,"باز کردن مسیر","مسیر بارگذاری شد.")
        except Exception:
            traceback.print_exc(); QMessageBox.warning(self,"خطا","بارگذاری مسیر انجام نشد.")

    def save_alignment(self):
        fp, _ = QFileDialog.getSaveFileName(self,"ذخیره مسیر...", str(PROJECTS_DIR / "alignment.alignment.json"), "Alignment Files (*.alignment.json);;JSON Files (*.json)")
        if not fp: return
        try:
            aln = getattr(self.plan_canvas,'aln', None)
            if aln is None:
                # try to serialize plan_poly
                if hasattr(self.plan_canvas, 'plan_poly'):
                    serial = {'name':'plan_export', 'elements':[{'type':'poly','points': self.plan_canvas.plan_poly}]}
                else:
                    QMessageBox.warning(self,"خطا","الایمنتی برای ذخیره وجود ندارد."); return
            else:
                serial = aln if isinstance(aln, dict) else getattr(aln,'to_dict', lambda: aln)()
            with open(fp,'w',encoding='utf-8') as f: json.dump(serial, f, ensure_ascii=False, indent=2)
            QMessageBox.information(self,"ذخیره مسیر","ذخیره انجام شد.")
        except Exception:
            traceback.print_exc(); QMessageBox.warning(self,"خطا","ذخیره مسیر انجام نشد.")

    # ---------- projects ----------
    def create_project(self):
        """ایجاد پروژهٔ جدید؛ پوشهٔ پروژه و فایل‌های پایه را می‌سازد."""
        base_folder = QFileDialog.getExistingDirectory(self, "انتخاب پوشهٔ پایه برای پروژه جدید")
        if not base_folder:
            return
        base_folder = Path(base_folder)
        name, ok = QInputDialog.getText(self, "نام پروژه", "نام پروژه (بدون کارکتر غیرمجاز):")
        if not ok or not name.strip():
            QMessageBox.warning(self, "نام نامعتبر", "نام پروژه وارد نشده یا نامعتبر است.")
            return
        proj_dir = base_folder / name.strip()
        try:
            proj_dir.mkdir(parents=True, exist_ok=False)
        except FileExistsError:
            reply = QMessageBox.question(self, "پوشه وجود دارد", "پوشه با این نام قبلاً وجود دارد. استفاده از آن؟", QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if reply != QMessageBox.Yes:
                return
        except Exception as e:
            QMessageBox.critical(self, "خطا", f"ایجاد پوشه پروژه با خطا مواجه شد:\n{e}")
            return
        try:
            with open(proj_dir / "project.json", "w", encoding="utf-8") as f:
                json.dump({"name": name.strip(), "version": "1.0"}, f, ensure_ascii=False, indent=2)
            if _HAS_PANDAS:
                pd.DataFrame(columns=['id','x','y','z','code']).to_csv(proj_dir / "points.csv", index=False)
            else:
                with open(proj_dir / "points.csv", "w", encoding="utf-8") as f:
                    f.write("id,x,y,z,code\n")
            (proj_dir / "surfaces").mkdir(exist_ok=True)
            QMessageBox.information(self, "پروژه ایجاد شد", f"پروژه '{name.strip()}' در:\n{str(proj_dir)}\nایجاد شد.")
            recent = read_recent_list(); pstr = str(proj_dir)
            if pstr in recent: recent.remove(pstr)
            recent.insert(0,pstr); write_recent_list(recent)
        except Exception:
            traceback.print_exc(); QMessageBox.critical(self, "خطا", f"خطا هنگام ساخت فایل‌های پروژه:\n{traceback.format_exc()}")

    def open_project(self):
        folder = QFileDialog.getExistingDirectory(self, "باز کردن پروژه")
        if not folder: return
        proj_dir = Path(folder)
        pj = proj_dir / "project.json"
        if not pj.exists():
            QMessageBox.warning(self,"خطا","project.json یافت نشد در پوشه انتخابی.")
            return
        try:
            with open(pj,'r',encoding='utf-8') as f: meta = json.load(f)
            ptsf = proj_dir / "points.csv"
            if ptsf.exists() and _HAS_PANDAS:
                df = pd.read_csv(ptsf)
                self.points_canvas.shapes = []
                for _, r in df.iterrows():
                    try: pid = int(r['id'])
                    except: pid = r['id']
                    x = float(r['x']); y = float(r['y']); z = float(r.get('z',0)); code = r.get('code','')
                    self.points_canvas.shapes.append({'type':'point','pos':(x,y),'data':{'id':pid,'x':x,'y':y,'z':z,'code':code}})
                try: self.points_canvas.fit_all()
                except: pass
                self.points_canvas.update(); self.refresh_points_table()
            QMessageBox.information(self,"باز کردن پروژه","پروژه باز شد.")
            recent = read_recent_list(); pstr = str(proj_dir)
            if pstr in recent: recent.remove(pstr)
            recent.insert(0,pstr); write_recent_list(recent)
        except Exception:
            traceback.print_exc(); QMessageBox.warning(self,"خطا","باز کردن پروژه انجام نشد.")

    def show_recent_projects(self):
        lst = read_recent_list()
        if not lst:
            QMessageBox.information(self,"اخطار","هیچ پروژهٔ قبلی وجود ندارد.")
            return
        sel, ok = QInputDialog.getItem(self,"پروژه‌های اخیر","انتخاب:", lst, 0, False)
        if ok and sel:
            self.open_project_helper(Path(sel))

    def open_project_helper(self, proj_dir: Path):
        pj = proj_dir / "project.json"
        if not pj.exists():
            QMessageBox.warning(self,"خطا","project.json یافت نشد."); return
        try:
            with open(pj,'r',encoding='utf-8') as f: meta = json.load(f)
            ptsf = proj_dir / "points.csv"
            if ptsf.exists() and _HAS_PANDAS:
                df = pd.read_csv(ptsf)
                self.points_canvas.shapes = []
                for _, r in df.iterrows():
                    try: pid = int(r['id'])
                    except: pid = r['id']
                    x = float(r['x']); y = float(r['y']); z = float(r.get('z',0)); code = r.get('code','')
                    self.points_canvas.shapes.append({'type':'point','pos':(x,y),'data':{'id':pid,'x':x,'y':y,'z':z,'code':code}})
                try: self.points_canvas.fit_all()
                except: pass
                self.points_canvas.update(); self.refresh_points_table()
        except Exception:
            traceback.print_exc(); QMessageBox.warning(self,"خطا","باز کردن پروژه انجام نشد.")

    # ---------- settings ----------
    def open_settings_dialog(self):
        dlg = SettingsDialog(self,
                             show_points_table=getattr(self, 'show_table_checkbox', None) and self.show_table_checkbox.isChecked(),
                             contour_interval=getattr(self.surface_canvas, 'contour_main_interval', 5.0),
                             contour_sub=getattr(self.surface_canvas, 'contour_sub_divisions', 4.0))
        if dlg.exec_() == QDialog.Accepted:
            vals = dlg.get_values()
            if hasattr(self, 'show_table_checkbox'):
                self.show_table_checkbox.setChecked(vals['show_points_table'])
                self.points_dock.setVisible(vals['show_points_table'])
            if hasattr(self.surface_canvas, 'contour_main_interval'):
                self.surface_canvas.contour_main_interval = vals['contour_interval']
            if hasattr(self.surface_canvas, 'contour_sub_divisions'):
                self.surface_canvas.contour_sub_divisions = vals['contour_sub']
            QMessageBox.information(self, "تنظیمات", "تنظیمات ذخیره و اعمال شد.")

    # ---------- auto-route (simple handler) ----------
    def on_auto_route_requested(self):
        dlg = AlignmentParamsDialog(self)
        if dlg.exec_() != QDialog.Accepted:
            return
        params = dlg.get_values() if hasattr(dlg,'get_values') else {}
        mandatory_ids = params.get('mandatory_ids', [])
        pts_map = { str(s['data'].get('id','')): s for s in self.points_canvas.shapes if s.get('type')=='point' }
        mandatory_points = []
        for mid in mandatory_ids:
            if mid in pts_map:
                p = pts_map[mid]
                mandatory_points.append( (float(p['data']['x']), float(p['data']['y'])) )
        if not mandatory_points:
            QMessageBox.warning(self, "نقاط اجباری", "هیچ نقطهٔ اجباری معتبری یافت نشد. لطفاً شناسه‌های نقاط را کنترل کنید یا نقاط را انتخاب کنید.")
            return
        if hasattr(self.plan_canvas, 'generate_suggested_route'):
            try:
                self.plan_canvas.generate_suggested_route(params, mandatory_points)
                self.tabs.setCurrentWidget(self.plan_tab)
            except Exception:
                traceback.print_exc(); QMessageBox.warning(self, "خطا", "ساخت مسیر پیشنهادی انجام نشد.")
        else:
            QMessageBox.warning(self, "پشتیبانی نشده", "این نسخه از پلان کانواس تابع ساخت مسیر پیشنهادی را ندارد.")

    # ---------- project save (points/surface/plan) ----------
    def save_current_project(self):
        """
        ذخیرهٔ سادهٔ وضعیت فعلی پروژه:
         - نقاط -> CSV
         - سطح -> surface.json
         - پلان -> plan.json
        مسیر ذخیره را از کاربر می‌گیرد (پوشه پروژه).
        """
        folder = QFileDialog.getExistingDirectory(self, "انتخاب پوشهٔ ذخیره پروژه (یا پوشه پروژه قبلی)")
        if not folder:
            return
        proj_dir = Path(folder)
        try:
            # نقاط
            pts = [s['data'] for s in self.points_canvas.shapes if s.get('type')=='point']
            ptsf = proj_dir / "points.csv"
            if _HAS_PANDAS:
                pd.DataFrame(pts).to_csv(ptsf, index=False)
            else:
                with open(ptsf, 'w', encoding='utf-8') as f:
                    f.write("id,x,y,z,code\n")
                    for p in pts:
                        f.write(f"{p.get('id','')},{p.get('x','')},{p.get('y','')},{p.get('z','')},{p.get('code','')}\n")
            # surface
            surface_serial = {'points':[s['data'] for s in self.surface_canvas.shapes if s.get('type')=='point'],
                              'boundaries': getattr(self.surface_canvas,'boundaries',[]),
                              'triangles': getattr(self.surface_canvas,'triangles',[])}
            with open(proj_dir / "surface.json", 'w', encoding='utf-8') as f:
                json.dump(surface_serial, f, ensure_ascii=False, indent=2)
            # plan
            plan_serial = {}
            if hasattr(self.plan_canvas, 'to_dict'):
                try:
                    plan_serial = self.plan_canvas.to_dict()
                except Exception:
                    plan_serial = {'plan_poly': getattr(self.plan_canvas, 'plan_poly', [])}
            else:
                plan_serial = {'plan_poly': getattr(self.plan_canvas, 'plan_poly', [])}
            with open(proj_dir / "plan.json", 'w', encoding='utf-8') as f:
                json.dump(plan_serial, f, ensure_ascii=False, indent=2)
            QMessageBox.information(self, "ذخیره پروژه", f"پروژه در {str(proj_dir)} ذخیره شد.")
            recent = read_recent_list(); pstr = str(proj_dir)
            if pstr in recent: recent.remove(pstr)
            recent.insert(0,pstr); write_recent_list(recent)
        except Exception:
            traceback.print_exc(); QMessageBox.warning(self, "خطا", "ذخیره پروژه انجام نشد.")

    # ---------- plan deletion helpers ----------
    def delete_plan(self):
        reply = QMessageBox.question(self, "حذف پلان", "آیا می‌خواهید پلان جاری پاک شود؟", QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if reply != QMessageBox.Yes:
            return
        try:
            if hasattr(self.plan_canvas, 'plan_poly'):
                self.plan_canvas.plan_poly = []
            if hasattr(self.plan_canvas, 'aln'):
                self.plan_canvas.aln = None
            try: self.plan_canvas.update()
            except: pass
            QMessageBox.information(self, "حذف پلان", "پلان حذف شد.")
        except Exception:
            traceback.print_exc(); QMessageBox.warning(self, "خطا", "حذف پلان انجام نشد.")

    def delete_plan_vertex_prompt(self):
        """
        کاربر می‌تواند index رأس را وارد کند یا 'closest' را انتخاب کند تا نزدیک‌ترین رأس به کلیک حذف شود.
        این نسخه ساده است: اگر 'closest' برگزیده شود، از کاربر می‌خواهد روی کانواس کلیک کند.
        """
        choices = ["شماره رأس (index)", "حذف نزدیک‌ترین رأس (کلیک روی پلان)"]
        sel, ok = QInputDialog.getItem(self, "حذف رأس پلان", "روش حذف:", choices, 0, False)
        if not ok:
            return
        if sel == choices[0]:
            idx, ok2 = QInputDialog.getInt(self, "حذف رأس", "شماره نمایه (0-based):", 0, 0, 1000000, 1)
            if not ok2: return
            try:
                if hasattr(self.plan_canvas, 'delete_vertex_by_index'):
                    okrem = self.plan_canvas.delete_vertex_by_index(idx)
                    if okrem:
                        QMessageBox.information(self, "حذف", f"رأس با اندیس {idx} حذف شد.")
                        return
                else:
                    if hasattr(self.plan_canvas, 'plan_poly'):
                        if 0 <= idx < len(self.plan_canvas.plan_poly):
                            del self.plan_canvas.plan_poly[idx]
                            try: self.plan_canvas.update()
                            except: pass
                            QMessageBox.information(self, "حذف", f"رأس با اندیس {idx} حذف شد.")
                            return
                QMessageBox.warning(self, "حذف", "حذف انجام نشد؛ اندیس نامعتبر یا تابع موجود نبود.")
            except Exception:
                traceback.print_exc(); QMessageBox.warning(self, "خطا", "حذف رأس انجام نشد.")
        else:
            QMessageBox.information(self, "حذف نزدیک‌ترین", "لطفاً در پنجرهٔ پلان روی نزدیک‌ترین نقطه کلیک کنید تا حذف شود.")
            # connect a one-shot click handler
            def one_shot_click(xy):
                try:
                    sx, sy = xy
                    if hasattr(self.plan_canvas, 'delete_vertex_at_screen'):
                        okrem = self.plan_canvas.delete_vertex_at_screen(sx, sy)
                        if okrem:
                            QMessageBox.information(self, "حذف", "رأس نزدیک حذف شد.")
                        else:
                            QMessageBox.warning(self, "حذف", "رأسی نزدیک به کلیک پیدا نشد.")
                    else:
                        # fallback: remove nearest by converting coords
                        wx, wy = self.plan_canvas.screen_to_world(sx, sy)
                        best = None; best_d = None
                        for i, (x,y) in enumerate(getattr(self.plan_canvas,'plan_poly',[])):
                            d = (x-wx)**2 + (y-wy)**2
                            if best_d is None or d < best_d:
                                best_d = d; best = i
                        if best is not None and best_d is not None:
                            del self.plan_canvas.plan_poly[best]; QMessageBox.information(self, "حذف", "رأس نزدیک حذف شد.")
                    try:
                        self.plan_canvas.update()
                    except: pass
                except Exception:
                    traceback.print_exc()
            # we'll ask the user to click and then capture mousePressEvent indirectly: simplest is show info and rely on plan_canvas context menu or manual deletion.
            QMessageBox.information(self, "راهنما", "برای حذف نزدیک‌ترین رأس، از منوی راست‌کلیک روی پلان استفاده کنید یا ابتدا شمارهٔ رأس را وارد کنید.")

# ---------- launcher ----------
if __name__ == "__main__":
    from PyQt5.QtWidgets import QApplication
    app = QApplication(sys.argv)
    win = CADMainWindow()
    win.show()
    sys.exit(app.exec_())
