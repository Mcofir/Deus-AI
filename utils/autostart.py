"""
הפעלה אוטומטית עם Windows, בשקט (בלי חלון קונסולה, ישר למגש המערכת).

משתמשים במפתח הריגיסטרי הסטנדרטי:
    HKEY_CURRENT_USER\\Software\\Microsoft\\Windows\\CurrentVersion\\Run

זה לא דורש הרשאות מנהל (זה בהקשר של המשתמש הנוכחי בלבד), וזו הדרך
המקובלת ביותר להפעלה אוטומטית של אפליקציית שולחן עבודה פשוטה.
"""

import os
import sys

try:
    import winreg
    _HAS_WINREG = True
except ImportError:
    _HAS_WINREG = False

_RUN_KEY_PATH = r"Software\Microsoft\Windows\CurrentVersion\Run"
_APP_NAME = "Deus"


def _get_launch_command() -> str:
    """
    בונה את הפקודה שתופעל בהפעלת Windows.
    אם רצים כ-exe קפוא (PyInstaller) - מפעילים אותו ישירות.
    אם רצים מ-python.exe בזמן פיתוח - מפעילים עם pythonw.exe (בלי קונסולה)
    ומצרפים את הנתיב ל-main.py ואת הדגל --minimized.
    """
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}" --minimized'

    pythonw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    if not os.path.exists(pythonw):
        pythonw = sys.executable  # נופל חזרה אם pythonw לא נמצא
    script_path = os.path.abspath(sys.argv[0])
    return f'"{pythonw}" "{script_path}" --minimized'


def is_autostart_enabled() -> bool:
    if not _HAS_WINREG:
        return False
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY_PATH, 0, winreg.KEY_READ) as key:
            winreg.QueryValueEx(key, _APP_NAME)
            return True
    except FileNotFoundError:
        return False
    except OSError:
        return False


def enable_autostart() -> bool:
    if not _HAS_WINREG:
        return False
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, _RUN_KEY_PATH, 0, winreg.KEY_SET_VALUE
        ) as key:
            winreg.SetValueEx(key, _APP_NAME, 0, winreg.REG_SZ, _get_launch_command())
        return True
    except OSError:
        return False


def disable_autostart() -> bool:
    if not _HAS_WINREG:
        return False
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, _RUN_KEY_PATH, 0, winreg.KEY_SET_VALUE
        ) as key:
            winreg.DeleteValue(key, _APP_NAME)
        return True
    except FileNotFoundError:
        return True  # כבר לא קיים - זה בסדר
    except OSError:
        return False
