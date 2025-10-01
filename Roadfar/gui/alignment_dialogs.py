# example: gui/alignment_dialogs.py
from PyQt5.QtWidgets import QDialog, QFormLayout, QLineEdit, QDialogButtonBox, QLabel, QSpinBox, QDoubleSpinBox, QVBoxLayout, QHBoxLayout, QPushButton, QMessageBox
from PyQt5.QtCore import Qt

class AlignmentParamsDialog(QDialog):
    """
    دیالوگ ساده برای دریافت پارامترهای مسیر پیشنهادی.
    برمی‌گرداند dict با کلیدهای: mandatory_ids (str, comma sep), design_speed(km/h), R_min(m), superelevation(%), arc_type ('circle'|'clothoid')
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("پارامترهای مسیر پیشنهادی")
        layout = QFormLayout(self)

        self.mandatory_edit = QLineEdit()
        self.mandatory_edit.setPlaceholderText("مثال: 100, 203, 305  (IDs یا index نقاط)")
        layout.addRow("نقاط اجباری (ID جدا شده با کاما):", self.mandatory_edit)

        self.speed_spin = QDoubleSpinBox(); self.speed_spin.setRange(1,300); self.speed_spin.setValue(60.0); self.speed_spin.setSuffix(" km/h")
        layout.addRow("سرعت طرح (km/h):", self.speed_spin)

        self.rmin_spin = QDoubleSpinBox(); self.rmin_spin.setRange(0.1,100000.0); self.rmin_spin.setValue(50.0)
        layout.addRow("حداقل شعاع مجاز (m) — یا 0 برای محاسبه از سرعت:", self.rmin_spin)

        self.superelev_spin = QDoubleSpinBox(); self.superelev_spin.setRange(0.0,0.2); self.superelev_spin.setDecimals(3); self.superelev_spin.setValue(0.06)
        layout.addRow("تورفتگی عرضی (مثال 0.06 = 6%):", self.superelev_spin)

        self.arc_type_edit = QLineEdit("circle")  # یا 'clothoid'
        layout.addRow("نوع قوس ('circle' یا 'clothoid'):", self.arc_type_edit)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addRow(buttons)

    def get_values(self):
        mand_text = self.mandatory_edit.text().strip()
        mandatory_ids = [s.strip() for s in mand_text.replace(';',',').split(',') if s.strip()] if mand_text else []
        return {
            'mandatory_ids': mandatory_ids,
            'design_speed_kmh': float(self.speed_spin.value()),
            'r_min_m': float(self.rmin_spin.value()),
            'superelevation': float(self.superelev_spin.value()),
            'arc_type': self.arc_type_edit.text().strip().lower()
        }
