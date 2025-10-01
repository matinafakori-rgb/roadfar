# core/design_standards.py
# -*- coding: utf-8 -*-
"""
Design standards helper — راهنمای طراحی (نسخه بازنویسی‌شده)

این ماژول:
 - فرمول‌های متداول SSD و رابطهٔ شعاع/سوپرالوِوِیشن را فراهم می‌کند.
 - جداول مرجع اصطکاک (قابل جایگزینی) برای پیشنهاد شعاع استفاده می‌شوند.
 - توابع کمکی برای پیشنهاد بازه‌های شعاع و طول کلوتوئید و مقادیر سوپرالِوِیشن و فواصل لیبل‌گذاری ارائه می‌دهد.
 - تابع validate_curve_parameters پارامترهای پیشنهادی برای UI برمی‌گرداند:
     {'ok': bool, 'errors': [...], 'warnings': [...], 'suggestions': {...}}
 - همهٔ خروجی‌ها عددی و متنی مناسب برای نمایش در دیالوگ هستند.

نکته: مقادیر پیش‌فرض بر پایهٔ مقادیر مرجع عمومی (AASHTO/FHWA-like) تنظیم شده‌اند و
قابل جایگزینی با جداول محلی/ملی می‌باشند.
"""

from __future__ import annotations
import math
from typing import Tuple, Dict, List, Optional

# ----------------- پارامترهای پیش‌فرض (قابل سفارشی‌سازی) -----------------
DEFAULT_REACTION_TIME: float = 2.5   # s (طبیعی برای طراحی)
DEFAULT_DECELERATION: float = 3.4    # m/s^2 (مقدار نمونه)

SUPERELEVATION = {
    "typical": 0.04,          # 4% معمول
    "max_recommended": 0.06,  # 6% پیشنهادشده در بسیاری از راهنماها
    "absolute_max": 0.08      # حد مطلق نمونه
}

# جدول اصطکاک جانبی تقریب زده شده (km/h -> friction)
# این مقادیر نمونه اند؛ در صورت داشتن دادهٔ محلی، این جدول را جایگزین کنید.
FRICTION_TABLE: List[Tuple[float, float]] = [
    (20.0, 0.24),
    (30.0, 0.22),
    (50.0, 0.18),
    (70.0, 0.16),
    (90.0, 0.14),
    (120.0, 0.12),
]


# ----------------- توابع کمکی -----------------
def kmh_to_ms(v_kmh: float) -> float:
    """تبدیل سرعت از km/h به m/s."""
    try:
        return float(v_kmh) / 3.6
    except Exception:
        return 0.0


def linear_interpolate(x: float, x0: float, y0: float, x1: float, y1: float) -> float:
    """درون‌یابی خطی ساده."""
    if x1 == x0:
        return 0.5 * (y0 + y1)
    t = (x - x0) / (x1 - x0)
    return y0 + t * (y1 - y0)


def recommend_friction(speed_kmh: float) -> float:
    """
    مقدار اصطکاک جانبی پیشنهادی بر اساس سرعت (در صورت نبود دادهٔ محلی).
    این مقدار از جدول نمونه در بالا درون‌یابی می‌شود.
    """
    try:
        table = sorted(FRICTION_TABLE, key=lambda t: t[0])
        if speed_kmh <= table[0][0]:
            return float(table[0][1])
        if speed_kmh >= table[-1][0]:
            return float(table[-1][1])
        for i in range(len(table) - 1):
            x0, y0 = table[i]
            x1, y1 = table[i + 1]
            if x0 <= speed_kmh <= x1:
                return float(linear_interpolate(speed_kmh, x0, y0, x1, y1))
        return float(table[-1][1])
    except Exception:
        return 0.15


# ----------------- فرمول‌های اصلی -----------------
def stopping_sight_distance(speed_kmh: float,
                            reaction_time: float = DEFAULT_REACTION_TIME,
                            decel: float = DEFAULT_DECELERATION) -> float:
    """
    SSD = v * t + v^2 / (2 * a)
    v: m/s, result: meters
    reaction_time: زمان واکنش (s)
    decel: شتاب ترمز طراحی (m/s^2)
    """
    try:
        v = kmh_to_ms(speed_kmh)
        ssd = v * float(reaction_time) + (v * v) / (2.0 * float(decel))
        return float(ssd)
    except Exception:
        return 0.0


def min_radius_from_superelevation_and_friction(speed_kmh: float,
                                                e: Optional[float] = None,
                                                f: Optional[float] = None) -> float:
    """
    رابطهٔ معمول: R = V^2 / (127 * (e + f))   (V in km/h) -> R in meters
    e: سوپرالِوِیشن (fraction), f: اصطکاک جانبی
    """
    try:
        if e is None:
            e = SUPERELEVATION['typical']
        if f is None:
            f = recommend_friction(speed_kmh)
        v = float(speed_kmh)
        denom = 127.0 * (float(e) + float(f))
        if denom <= 0.0:
            return float('inf')
        return float((v * v) / denom)
    except Exception:
        return float('inf')


def recommend_radius_range(chord_length: float, speed_kmh: float) -> Tuple[float, float]:
    """
    پیشنهاد بازهٔ شعاع (r_min, r_max) بر اساس طول اتصال (chord) و سرعت.
    منطق:
      - حداقل هندسی از chord (مثلاً chord/8 یا حداقل ثابت)
      - حداقل ایمنی از سوپرالِوِیشن/اصطکاک
      - حداکثر هندسی از chord*8 یا حداقل بزرگ
    این تابع یک بازه معقول برای راهنمای UI بازمی‌گرداند.
    """
    try:
        chord = max(0.0, float(chord_length))
        # پایه‌های هندسی
        baseline_min = max(3.0, chord / 8.0)   # حداقل هندسی
        baseline_max = max(30.0, chord * 8.0)  # حداکثر هندسی
        # حداقل از نظر سوپرالِوِیشن و اصطکاک
        r_from_sup = min_radius_from_superelevation_and_friction(speed_kmh)
        # اجازه ده کمی کمتر از مقدار sup برای انعطاف (مثلاً 20%)
        r_min = max(baseline_min, r_from_sup * 0.2)
        r_max = baseline_max
        # مرزبندی منطقی
        r_min = max(3.0, float(r_min))
        r_max = max(r_min + 1.0, float(r_max))
        return (float(r_min), float(r_max))
    except Exception:
        return (3.0, 1000.0)


def recommend_spiral_length_range(radius: float, speed_kmh: float) -> Tuple[float, float]:
    """
    بازهٔ پیشنهادی برای طول کلوتوئید (Ls).
    یک قاعدهٔ سرانگشتی: Ls ∈ [0.04*R, 0.15*R] با حداقل ~3m.
    """
    try:
        R = max(0.0, float(radius))
        Ls_min = max(3.0, 0.04 * R)
        Ls_max = min(max(10.0, 0.15 * R), 200.0)
        if Ls_min > Ls_max:
            Ls_min = Ls_max * 0.5
        return (float(Ls_min), float(Ls_max))
    except Exception:
        return (3.0, 50.0)


def recommend_superelevation(speed_kmh: float) -> float:
    """
    پیشنهادی سوپرالِوِیشن بر اساس سرعت — مقدار عقلانی و محافظه‌کارانه.
    """
    try:
        v = float(speed_kmh)
        if v <= 50.0:
            return float(SUPERELEVATION['typical'])
        if v <= 100.0:
            return float(min(SUPERELEVATION['max_recommended'], SUPERELEVATION['typical'] + 0.01))
        return float(min(SUPERELEVATION['absolute_max'], SUPERELEVATION['typical'] + 0.02))
    except Exception:
        return float(SUPERELEVATION['typical'])


def recommend_label_interval(speed_kmh: float) -> float:
    """
    پیشنهاد فاصلهٔ لیبل‌گذاری برای منحنی‌های میزان یا کیلومتراژ
    (مقادیر نمونه — قابل تنظیم).
    """
    try:
        v = float(speed_kmh)
        if v <= 40.0:
            return 5.0
        if v <= 80.0:
            return 10.0
        return 20.0
    except Exception:
        return 10.0


# ----------------- تابع اعتبارسنجی پارامترهای قوس -----------------
def validate_curve_parameters(P_left: Tuple[float, float],
                              P_right: Tuple[float, float],
                              left_heading_rad: Optional[float],
                              right_heading_rad: Optional[float],
                              curve_type: str,
                              params: Dict,
                              speed_kmh: float = 60.0) -> Dict:
    """
    ورودی‌ها:
      - P_left, P_right: نقاط تانژانت (یا نقاط شروع/پایان) به صورت (x,y)
      - left_heading_rad, right_heading_rad: هدینگ‌ها (رادیان) اگر موجود باشند (برای چک‌کردن تطابق)
      - curve_type: 'arc' یا 'spiral_arc_spiral'
      - params: دیکشنری پارامترها: برای arc -> {'radius':...}
                برای spiral_arc_spiral -> {'radius':..., 'spiral_length': ...}
      - speed_kmh: سرعت طراحی برای محاسبات ایمنی

    خروجی:
      {
        'ok': bool,
        'errors': [str],
        'warnings': [str],
        'suggestions': { 'radius_range_m': (min,max), 'ssd_m': ..., 'spiral_length_range_m':(...),
                         'recommended_e':..., 'recommended_f':..., 'chord_length_m': ... }
      }
    """
    errors: List[str] = []
    warnings: List[str] = []
    suggestions: Dict = {}

    try:
        # محاسبه chord
        dx = float(P_right[0]) - float(P_left[0])
        dy = float(P_right[1]) - float(P_left[1])
        chord = math.hypot(dx, dy)
        suggestions['chord_length_m'] = float(chord)

        # پیشنهادات پایه
        r_min, r_max = recommend_radius_range(chord, speed_kmh)
        suggestions['radius_range_m'] = (r_min, r_max)
        suggestions['ssd_m'] = float(stopping_sight_distance(speed_kmh))
        suggestions['recommended_e'] = float(recommend_superelevation(speed_kmh))
        suggestions['recommended_f'] = float(recommend_friction(speed_kmh))
        suggestions['label_interval_m'] = float(recommend_label_interval(speed_kmh))

        # بر اساس نوع قوس بررسی کن
        t = (curve_type or '').lower().strip()
        if t == 'arc':
            R = float(params.get('radius', 0.0))
            if R <= 0.0:
                errors.append("شعاع باید عددی بزرگ‌تر از صفر باشد.")
            else:
                # هندسهٔ لازم: chord <= 2R
                if chord > 2.0 * R + 1e-9:
                    errors.append(
                        f"فاصلهٔ تانژانت‌ها ({chord:.2f} m) بیش از 2×R است؛ با این شعاع قوس ساده قابل ساخت نیست."
                    )
                # مقایسه با بازهٔ پیشنهادی
                if R < r_min:
                    warnings.append(f"شعاع ({R:.1f} m) کمتر از بازهٔ پیشنهادی ({r_min:.1f}..{r_max:.1f}).")
                if R > r_max:
                    warnings.append(f"شعاع ({R:.1f} m) بیشتر از بازهٔ پیشنهادی ({r_min:.1f}..{r_max:.1f}).")
                # نسبت به شعاع مورد نیاز از سوپرالِوِیشن
                min_r_sup = min_radius_from_superelevation_and_friction(speed_kmh)
                suggestions['min_radius_from_superelevation'] = float(min_r_sup)
                if min_r_sup != float('inf') and R < 0.2 * min_r_sup:
                    warnings.append("شعاع وارد شده بسیار کوچک نسبت به مقدار توصیه‌شده بر اساس سوپرالِوِیشن/اصطکاک است.")
        elif t in ('spiral_arc_spiral', 'spiral', 'clothoid', 'sas'):
            R = float(params.get('radius', 0.0))
            Ls = float(params.get('spiral_length', 0.0))
            if R <= 0.0:
                errors.append("شعاع مرکزی باید عددی بزرگ‌تر از صفر باشد.")
            else:
                # وضعیت chord نسبت به R
                if chord > 20.0 * R:
                    warnings.append("فاصلهٔ تانژانت‌ها نسبت به شعاع بسیار بزرگ است؛ احتمال بروز مشکل هندسی وجود دارد.")
                # پیشنهاد طول کلوتوئید
                Ls_min, Ls_max = recommend_spiral_length_range(R, speed_kmh)
                suggestions['spiral_length_range_m'] = (Ls_min, Ls_max)
                if Ls <= 0.0:
                    errors.append("طول کلوتوئید (spiral_length) باید بزرگ‌تر از صفر باشد.")
                else:
                    if Ls < Ls_min:
                        warnings.append(f"طول کلوتوئید ({Ls:.1f} m) کمتر از بازهٔ پیشنهادی ({Ls_min:.1f}..{Ls_max:.1f}).")
                    if Ls > Ls_max:
                        warnings.append(f"طول کلوتوئید ({Ls:.1f} m) بیشتر از بازهٔ پیشنهادی ({Ls_min:.1f}..{Ls_max:.1f}).")
        else:
            errors.append("نوع قوس نامشخص است؛ مقادیر مجاز: 'arc' یا 'spiral_arc_spiral' (یا معادل‌های کوتاه).")

        # بررسی آشکار هدینگ‌ها اگر داده شده باشد (هشدار نه خطا)
        try:
            if left_heading_rad is not None and right_heading_rad is not None:
                dh = abs((float(right_heading_rad) - float(left_heading_rad)))
                # نرمالایز به [0, pi]
                while dh > math.pi:
                    dh = abs(dh - 2 * math.pi)
                # اگر تغییر خیلی بزرگ است ممکن است پارامترها ناسازگار باشند
                if dh > math.pi * 0.95:
                    warnings.append("تغییر جهت بین هدینگ‌های داده‌شده بسیار زیاد است؛ پارامترهای قوس را بازبینی کنید.")
        except Exception:
            pass

    except Exception as ex:
        errors.append(f"خطا در محاسبات داخلی: {ex}")

    ok = (len(errors) == 0)
    return {
        'ok': ok,
        'errors': errors,
        'warnings': warnings,
        'suggestions': suggestions
    }


# ----------------- اگر خواستید این ماژول را مستقل اجرا کنید یک مثال سریع اجرا می‌شود -----------------
if __name__ == "__main__":
    # تست نمونه
    P1 = (0.0, 0.0)
    P2 = (50.0, 10.0)
    res = validate_curve_parameters(P1, P2, None, None, 'arc', {'radius': 30.0}, speed_kmh=80.0)
    print("OK:", res['ok'])
    print("Errors:", res['errors'])
    print("Warnings:", res['warnings'])
    print("Suggestions:", res['suggestions'])
