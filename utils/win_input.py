"""
שליחת קלט מקלדת ברמה נמוכה (SendInput) עם **קודי סריקה אמיתיים**
(Scan Codes) - לא קודי מקש וירטואליים (Virtual-Key codes, מה שחבילת
Python `keyboard` שולחת כברירת מחדל ב-keyboard.send()/press()).

**למה זה קיים** (נבדק ואומת בפועל, פעמיים, בשני הקשרים שונים):
קודי VK לפעמים לא מזוהים באופן עקבי על ידי מנגנוני OS ברמה נמוכה -
גם קיצורי מקלדת גלובליים שנרשמים דרך RegisterHotKey (למשל Alt+G של
Chrome ל-Ask Gemini), וגם מקשי מדיה מיוחדים (Play/Pause וכו', שגם הם
מטופלים ב-Windows דרך מנגנון נפרד, לא כמו אותיות/ספרות רגילות).
קודי סריקה הם בדיוק מה שמקלדת פיזית שולחת ברמת החומרה - ולכן
מזוהים בעקביות בכל המקרים שבהם קודי VK "עבדו רק לפעמים".

שימוש:
    from utils.win_input import send_key_combo, send_media_key
    send_key_combo(["alt", "g"])       # קיצור מקלדת (Alt+G)
    send_media_key("play_pause")       # מקש מדיה (Play/Pause)
"""

import ctypes
import time

_PUL = ctypes.POINTER(ctypes.c_ulong)


class _KeyBdInput(ctypes.Structure):
    _fields_ = [
        ("wVk", ctypes.c_ushort),
        ("wScan", ctypes.c_ushort),
        ("dwFlags", ctypes.c_ulong),
        ("time", ctypes.c_ulong),
        ("dwExtraInfo", _PUL),
    ]


class _HardwareInput(ctypes.Structure):
    _fields_ = [
        ("uMsg", ctypes.c_ulong),
        ("wParamL", ctypes.c_short),
        ("wParamH", ctypes.c_ushort),
    ]


class _MouseInput(ctypes.Structure):
    _fields_ = [
        ("dx", ctypes.c_long),
        ("dy", ctypes.c_long),
        ("mouseData", ctypes.c_ulong),
        ("dwFlags", ctypes.c_ulong),
        ("time", ctypes.c_ulong),
        ("dwExtraInfo", _PUL),
    ]


class _InputUnion(ctypes.Union):
    _fields_ = [("ki", _KeyBdInput), ("mi", _MouseInput), ("hi", _HardwareInput)]


class _Input(ctypes.Structure):
    _fields_ = [("type", ctypes.c_ulong), ("ii", _InputUnion)]


_INPUT_KEYBOARD = 1
_KEYEVENTF_SCANCODE = 0x0008
_KEYEVENTF_KEYUP = 0x0002
_KEYEVENTF_EXTENDEDKEY = 0x0001

# מפת קודי סריקה (PC/AT set 1) למקשים "רגילים" - אותיות, ספרות, מודיפיירים.
SCAN_CODES = {
    "esc": 0x01, "1": 0x02, "2": 0x03, "3": 0x04, "4": 0x05, "5": 0x06,
    "6": 0x07, "7": 0x08, "8": 0x09, "9": 0x0A, "0": 0x0B,
    "tab": 0x0F, "q": 0x10, "w": 0x11, "e": 0x12, "r": 0x13, "t": 0x14,
    "y": 0x15, "u": 0x16, "i": 0x17, "o": 0x18, "p": 0x19,
    "ctrl": 0x1D, "a": 0x1E, "s": 0x1F, "d": 0x20, "f": 0x21, "g": 0x22,
    "h": 0x23, "j": 0x24, "k": 0x25, "l": 0x26, "enter": 0x1C,
    "shift": 0x2A, "z": 0x2C, "x": 0x2D, "c": 0x2E, "v": 0x2F, "b": 0x30,
    "n": 0x31, "m": 0x32,
    "alt": 0x38, "space": 0x39,
}

# קודי סריקה *מורחבים* (extended, קידומת E0 בחומרה אמיתית - כאן
# מיוצג על ידי דגל KEYEVENTF_EXTENDEDKEY) עבור מקשי מדיה מיוחדים.
_MEDIA_SCAN_CODES = {
    "play_pause": 0x22,
    "stop": 0x24,
    "next": 0x19,
    "prev": 0x10,
    "vol_mute": 0x20,
    "vol_down": 0x2E,
    "vol_up": 0x30,
}


def _send_scan(scan_code: int, key_up: bool = False, extended: bool = False):
    extra = ctypes.c_ulong(0)
    ii_ = _InputUnion()
    flags = _KEYEVENTF_SCANCODE
    if extended:
        flags |= _KEYEVENTF_EXTENDEDKEY
    if key_up:
        flags |= _KEYEVENTF_KEYUP
    ii_.ki = _KeyBdInput(0, scan_code, flags, 0, ctypes.pointer(extra))
    inp = _Input(_INPUT_KEYBOARD, ii_)
    ctypes.windll.user32.SendInput(1, ctypes.pointer(inp), ctypes.sizeof(inp))


def send_key_combo(keys, hold_delay: float = 0.05) -> bool:
    """שולח צירוף מקשים (למשל ["alt", "g"]) בעזרת קודי סריקה אמיתיים -
    לוחץ את כולם בסדר, מחזיק רגע, ומשחרר בסדר הפוך. מחזיר True אם כל
    המקשים זוהו ונשלחו, False אם אחד מהם לא מוכר ב-SCAN_CODES."""
    codes = []
    for key in keys:
        code = SCAN_CODES.get(key.strip().lower())
        if code is None:
            return False
        codes.append(code)

    for code in codes:
        _send_scan(code, key_up=False)
        time.sleep(hold_delay)
    time.sleep(hold_delay + 0.01)
    for code in reversed(codes):
        _send_scan(code, key_up=True)
        time.sleep(hold_delay)
    return True


def send_media_key(name: str, hold_delay: float = 0.05) -> bool:
    """שולח מקש מדיה מיוחד (play_pause/stop/next/prev/vol_mute/vol_down/
    vol_up) בעזרת קוד סריקה מורחב אמיתי - **לא** דרך keyboard.send()
    (מבוסס VK codes), שנבדק בפועל כלא אמין באופן עקבי למקשים האלה
    (הקריאה "מצליחה" ברמת ה-API בלי שגיאה, גם כשהמערכת לא הגיבה
    בפועל). מחזיר True אם השם מוכר ונשלח, False אם לא."""
    code = _MEDIA_SCAN_CODES.get(name.strip().lower())
    if code is None:
        return False
    _send_scan(code, key_up=False, extended=True)
    time.sleep(hold_delay)
    _send_scan(code, key_up=True, extended=True)
    return True
