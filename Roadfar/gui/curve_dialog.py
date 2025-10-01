# gui/curve_dialog.py
# -*- coding: utf-8 -*-
from __future__ import annotations
from typing import Tuple, Dict, Any, Optional

from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QComboBox, QFormLayout, QMessageBox, QDoubleSpinBox, QTextEdit, QSpacerItem, QSizePolicy
)
from PyQt5.QtCore import Qt

from core.design_standards import (
    validate_curve_parameters,
    recommend_radius_range,
    recommend_spiral_length_range
)


class CurveDialog(QDialog):
    """
    دیالوگ تنظیم پارامترهای قوس برای افزودن به الایمنت.
    استفاده:
        dlg = CurveDialog(parent, P_left, P_right, left_heading, right_heading, speed_kmh=60.0)
        if dlg.exec_() == QDialog.Accepted:
            params = dlg.get_params()
    ورودی‌ها:
      - P_left, P_right: Tuple[float,float] نقاط تانژانت/ابتدا-انتهای
      - left_heading, right_heading: هدینگ‌ها به رادیان یا None
      - speed_kmh: سرعت طراحی برای محاسبات کمکی
      - default_curve_type: 'spiral_arc_spiral' یا 'arc'
    خروجی:
      get_params() -> dict شامل keys: curve_type, radius, spiral_length (در صورت نیاز)، side
    """

    def __init__(self,
                 parent,
                 P_left: Tuple[float, float],
                 P_right: Tuple[float, float],
                 left_heading: Optional[float],
                 right_heading: Optional[float],
                 speed_kmh: float = 60.0,
                 default_curve_type: str = 'spiral_arc_spiral'):
        super().__init__(parent)
        self.setWindowTitle("تنظیم پارامترهای قوس")
        self.resize(600, 420)

        self.P_left = P_left
        self.P_right = P_right
        self.left_heading = left_heading
        self.right_heading = right_heading
        self.speed_kmh = float(speed_kmh)
        self._final_params: Optional[Dict[str, Any]] = None

        # main layout
        main_l = QVBoxLayout(self)

        # summary / suggestions (read-only)
        self.summary = QTextEdit(self)
        self.summary.setReadOnly(True)
        self.summary.setMinimumHeight(110)
        main_l.addWidget(self.summary)

        # form area
        form = QFormLayout()
        # type combo
        self.type_combo = QComboBox()
        self.type_combo.addItem("قوس دایروی ساده (Arc)")
        self.type_combo.addItem("کلوتوئید - قوس - کلوتوئید (Spiral-Arc-Spiral)")
        if default_curve_type == 'arc':
            self.type_combo.setCurrentIndex(0)
        else:
            self.type_combo.setCurrentIndex(1)
        form.addRow("نوع قوس:", self.type_combo)

        # radius
        self.radius_spin = QDoubleSpinBox()
        self.radius_spin.setDecimals(2)
        self.radius_spin.setRange(0.01, 1e9)
        self.radius_spin.setSingleStep(1.0)
        form.addRow("شعاع (m):", self.radius_spin)

        # spiral length
        self.ls_spin = QDoubleSpinBox()
        self.ls_spin.setDecimals(2)
        self.ls_spin.setRange(0.1, 1e8)
        self.ls_spin.setSingleStep(0.5)
        form.addRow("طول کلوتوئید هر سمت Ls (m):", self.ls_spin)

        # side selection
        self.side_combo = QComboBox()
        self.side_combo.addItems(["left", "right"])
        form.addRow("طرف قوس (side):", self.side_combo)

        main_l.addLayout(form)

        # warnings label
        self.warnings_label = QLabel("")
        self.warnings_label.setWordWrap(True)
        self.warnings_label.setStyleSheet("QLabel { color: #b00; }")
        self.warnings_label.setMinimumHeight(60)
        main_l.addWidget(self.warnings_label)

        # spacer
        main_l.addItem(QSpacerItem(20, 10, QSizePolicy.Minimum, QSizePolicy.Expanding))

        # buttons
        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        self.ok_btn = QPushButton("تأیید و ایجاد")
        self.cancel_btn = QPushButton("لغو")
        btn_row.addWidget(self.ok_btn)
        btn_row.addWidget(self.cancel_btn)
        main_l.addLayout(btn_row)

        # connections
        self.ok_btn.clicked.connect(self.on_ok)
        self.cancel_btn.clicked.connect(self.reject)
        self.type_combo.currentIndexChanged.connect(self.on_type_changed)
        self.radius_spin.valueChanged.connect(self._update_warnings)
        self.ls_spin.valueChanged.connect(self._update_warnings)
        self.side_combo.currentIndexChanged.connect(self._update_warnings)

        # initialize suggested values
        self._apply_suggestions()

        # reflect type enable/disable
        self.on_type_changed(self.type_combo.currentIndex())

    def _apply_suggestions(self) -> None:
        """
        مقداردهی اولیه شعاع و Ls بر اساس توابع راهنمای طراحی.
        """
        try:
            # پیشنهادی پایه از design_standards
            rr = recommend_radius_range(
                chord_length=(((self.P_right[0] - self.P_left[0]) ** 2 + (self.P_right[1] - self.P_left[1]) ** 2) ** 0.5),
                speed_kmh=self.speed_kmh
            )
            # mid radius and set sensible spin ranges
            r_min, r_max = float(rr[0]), float(rr[1])
            r_mid = max(0.1, (r_min + r_max) / 2.0)
            # expand spinner range to be permissive but bounded
            self.radius_spin.setRange(max(0.01, r_min / 10.0), max(1.0, r_max * 10.0))
            self.radius_spin.setValue(round(r_mid, 2))

            # spiral suggestions
            slr = recommend_spiral_length_range(radius=r_mid, speed_kmh=self.speed_kmh)
            ls_min, ls_max = float(slr[0]), float(slr[1])
            ls_mid = max(0.1, (ls_min + ls_max) / 2.0)
            self.ls_spin.setRange(max(0.1, ls_min / 10.0), max(1.0, ls_max * 10.0))
            self.ls_spin.setValue(round(ls_mid, 2))

            # Fill summary text
            # reuse validate_curve_parameters to get SSD etc.
            ctype = 'spiral_arc_spiral' if self.type_combo.currentIndex() == 1 else 'arc'
            v = validate_curve_parameters(self.P_left, self.P_right, self.left_heading, self.right_heading,
                                          ctype, {'radius': r_mid, 'spiral_length': ls_mid},
                                          speed_kmh=self.speed_kmh)
            sug = v.get('suggestions', {})

            lines = []
            lines.append(f"فاصلهٔ تانژانت‌ها: {sug.get('chord_length_m', 0.0):.2f} m")
            rr2 = sug.get('radius_range_m', (r_min, r_max))
            lines.append(f"بازهٔ پیشنهادی شعاع: {rr2[0]:.1f} .. {rr2[1]:.1f} m")
            lines.append(f"SSD پیشنهادی: {sug.get('ssd_m', 0.1):.1f} m")
            lines.append(f"پیشنهاد سوپرالِوِیشن (e): {sug.get('recommended_e', 0.04):.3f}")
            lines.append(f"پیشنهاد ضریب اصطکاک (f): {sug.get('recommended_f', 0.15):.3f}")
            if 'spiral_length_range_m' in sug:
                slr2 = sug['spiral_length_range_m']
                lines.append(f"بازهٔ پیشنهادی طول کلوتوئید (هر سمت): {slr2[0]:.1f} .. {slr2[1]:.1f} m")
            self.summary.setPlainText("\n".join(lines))
        except Exception as e:
            # fail-safe: set some defaults
            self.radius_spin.setRange(0.01, 1e6)
            self.radius_spin.setValue(50.0)
            self.ls_spin.setRange(0.1, 1e5)
            self.ls_spin.setValue(10.0)
            self.summary.setPlainText("پیشنهادها قابل محاسبه نیستند — ورودی‌ها را بررسی کنید.")

        # update warnings based on these defaults
        self._update_warnings()

    def on_type_changed(self, idx: int) -> None:
        """
        اگر arc انتخاب شده، Ls غیرفعال می‌شود.
        """
        if idx == 0:
            # arc
            self.ls_spin.setEnabled(False)
        else:
            self.ls_spin.setEnabled(True)
        # هر تغییری باعث بازبینی هشدارها شود
        self._update_warnings()

    def _update_warnings(self) -> None:
        """
        اعتبارسنجی زنده و نمایش خطا/هشدار.
        """
        try:
            ctype = 'arc' if self.type_combo.currentIndex() == 0 else 'spiral_arc_spiral'
            params = {'radius': float(self.radius_spin.value()), 'spiral_length': float(self.ls_spin.value())}
            res = validate_curve_parameters(self.P_left, self.P_right, self.left_heading, self.right_heading,
                                            ctype, params, speed_kmh=self.speed_kmh)
            parts = []
            if res.get('errors'):
                parts.append("<b style='color:#800000;'>خطاها:</b><br>" + "<br>".join(res['errors']))
            if res.get('warnings'):
                parts.append("<b style='color:#AA6600;'>هشدارها:</b><br>" + "<br>".join(res['warnings']))
            if parts:
                self.warnings_label.setText("<br><br>".join(parts))
            else:
                self.warnings_label.setText("<span style='color:green;'>هیچ خطا یا هشداری یافت نشد.</span>")
        except Exception:
            self.warnings_label.setText("خطا در اعتبارسنجی پارامترها.")

    def on_ok(self) -> None:
        """
        هنگام فشردن تأیید، بررسی نهایی و قبول یا نمایش هشدار/خطا.
        """
        try:
            ctype = 'arc' if self.type_combo.currentIndex() == 0 else 'spiral_arc_spiral'
            params = {
                'radius': float(self.radius_spin.value()),
                'spiral_length': float(self.ls_spin.value()),
                'side': str(self.side_combo.currentText())
            }
            res = validate_curve_parameters(self.P_left, self.P_right, self.left_heading, self.right_heading,
                                            ctype, params, speed_kmh=self.speed_kmh)
            if res.get('errors'):
                QMessageBox.critical(self, "خطا در پارامترها", "\n".join(res['errors']))
                return
            if res.get('warnings'):
                reply = QMessageBox.question(
                    self, "هشدارها", "هشدارهای زیر وجود دارند:\n\n" + "\n".join(res['warnings']) + "\n\nآیا ادامه می‌دهید؟",
                    QMessageBox.Yes | QMessageBox.No, QMessageBox.No
                )
                if reply != QMessageBox.Yes:
                    return
            # accepted
            params['curve_type'] = ctype
            self._final_params = params
            self.accept()
        except Exception as ex:
            QMessageBox.critical(self, "خطا", f"خطا هنگام پردازش پارامترها:\n{ex}")

    def get_params(self) -> Optional[Dict[str, Any]]:
        """
        پارامترهای نهایی (در صورت قبول دیالوگ) را برمی‌گرداند.
        """
        return self._final_params
