"""
הבאת חלון בכוח לקדמה, ואיתור חלון לפי PID של תהליך.

**למה AttachThreadInput ולא רק win32gui.SetForegroundWindow**: Windows
חוסם תהליכים ברקע מ"לגנוב" פוקוס ("focus stealing prevention"), אבל
הקריאה הרגילה *לא זורקת שגיאה* גם כשהיא נכשלת בשקט - במקרה כזה
Windows רק מהבהב את סמל התוכנה בשורת המשימות, בלי להעביר פוקוס
בפועל. זו הסיבה המדויקת לתקלות "לפעמים עובד, לפעמים לא" שנצפו בכמה
הקשרים שונים בפרויקט הזה (מיקוד חלון Chrome/Gemini, והדבקת 'claude'
לתוך PowerShell חדש שנפתח). הפתרון (טכניקה מתועדת של מיקרוסופט):
"מצמידים" את תור הקלט (input queue) של ה-thread הנוכחי לזה של
ה-thread שמחזיק כרגע את הפוקוס - מה שגורם ל-Windows להתייחס לקריאה
כאילו היא מגיעה מאותו thread שכבר "מורשה" להחליף פוקוס.
"""

import logging

try:
    import win32gui
    import win32con
    import win32process
    import win32api
    _HAS_WIN32 = True
except ImportError:
    _HAS_WIN32 = False

_log = logging.getLogger("deus")


def find_window_by_pid(pid: int, require_visible: bool = True):
    """מוצא handle לחלון עליון (הראשון שנמצא) ששייך לתהליך עם ה-PID
    הנתון בדיוק - מדויק יותר מחיפוש לפי שם תהליך/כותרת כשיש כמה
    מופעים של אותה תוכנה פתוחים בו-זמנית."""
    if not _HAS_WIN32:
        return None
    matches = []

    def _enum_handler(hwnd, _ctx):
        if require_visible and not win32gui.IsWindowVisible(hwnd):
            return
        try:
            _, wpid = win32process.GetWindowThreadProcessId(hwnd)
            if wpid == pid:
                matches.append(hwnd)
        except Exception:
            pass

    try:
        win32gui.EnumWindows(_enum_handler, None)
    except Exception:
        return None
    return matches[0] if matches else None


def force_foreground_window(hwnd, log: logging.Logger = None) -> bool:
    """מביא בכוח את hwnd לקדמה, ומוודא בפועל שזה הצליח (לא רק שלא
    נזרקה שגיאה) - ראו הסבר מפורט בתיעוד הקובץ למעלה."""
    log = log or _log
    if not _HAS_WIN32 or hwnd is None:
        return False
    try:
        if win32gui.IsIconic(hwnd):
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)

        if win32gui.GetForegroundWindow() == hwnd:
            return True

        current_thread = win32api.GetCurrentThreadId()
        target_thread, _ = win32process.GetWindowThreadProcessId(hwnd)
        fg_hwnd = win32gui.GetForegroundWindow()
        fg_thread = None
        if fg_hwnd:
            fg_thread, _ = win32process.GetWindowThreadProcessId(fg_hwnd)

        attached_fg = False
        attached_target = False
        try:
            if fg_thread and fg_thread != current_thread:
                win32process.AttachThreadInput(current_thread, fg_thread, True)
                attached_fg = True
            if target_thread and target_thread != current_thread and target_thread != fg_thread:
                win32process.AttachThreadInput(current_thread, target_thread, True)
                attached_target = True

            win32gui.BringWindowToTop(hwnd)
            try:
                win32gui.SetForegroundWindow(hwnd)
            except Exception:
                # SetForegroundWindow יכול "לזרוק" (pywintypes.error) גם
                # במקרה תקין של Windows שחוסם החלפת פוקוס (focus
                # stealing prevention) - זו לא שגיאה אמיתית, רק סימן
                # שהניסיון לא הצליח הפעם. הבדיקה הסופית למטה
                # (GetForegroundWindow() == hwnd) כבר מטפלת בזה נכון -
                # אין צורך בלוג רועש (traceback מלא) על משהו צפוי.
                pass
        finally:
            if attached_fg:
                win32process.AttachThreadInput(current_thread, fg_thread, False)
            if attached_target:
                win32process.AttachThreadInput(current_thread, target_thread, False)

        return win32gui.GetForegroundWindow() == hwnd
    except Exception:
        log.exception("נכשל בהבאת חלון לקדמה בכוח")
        return False
