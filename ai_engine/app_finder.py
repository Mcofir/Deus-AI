"""
איתור אוטומטי של נתיבי התקנה עבור אפליקציות נפוצות (Chrome, Brave,
WhatsApp), כדי לייתר את הצורך שהמשתמש יחפש ידנית את קובץ ה-exe.

השיטה: לכל אפליקציה יש רשימת "מועמדים" - נתיבים אפשריים תחת תיקיות
ההתקנה הסטנדרטיות של Windows (Program Files, Program Files (x86),
AppData\\Local וכו'), עם משתני סביבה שמתרחבים אוטומטית לפי המשתמש
הנוכחי. בודקים לפי סדר איזה מהם קיים בפועל על הדיסק.

אפליקציות UWP/Microsoft Store (כמו WhatsApp): לאלה **אין** בכלל קובץ
exe נגיש ישירות - הן ארוזות תחת %PROGRAMFILES%\\WindowsApps, תיקייה
עם הרשאות שמורות ל-SYSTEM בלבד (גם אם מנחשים את הנתיב המדויק, הרצה
ישירה נכשלת עם "Access is denied"). הדרך הנתמכת רשמית להפעיל אפליקציית
UWP מותקנת היא דרך "shell:appsFolder\\<AppUserModelID>" (בדיוק מה
ש-Explorer עצמו עושה כשלוחצים על קיצור בתפריט התחל) - ה-AppUserModelID
מזוהה דינמית דרך PowerShell (Get-StartApps), כדי שלא נצטרך "לנחש"
מזהה קבוע (שכן משתנה בין גרסאות/מהדורות) - ראו find_uwp_app_shell_path
למטה.
"""

import os
import subprocess

# שם תצוגה בעברית -> רשימת נתיבים אפשריים (עם משתני סביבה של Windows)
_WELL_KNOWN_APPS = {
    "כרום": [
        r"%PROGRAMFILES%\Google\Chrome\Application\chrome.exe",
        r"%PROGRAMFILES(X86)%\Google\Chrome\Application\chrome.exe",
        r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe",
    ],
    "ברייב": [
        r"%PROGRAMFILES%\BraveSoftware\Brave-Browser\Application\brave.exe",
        r"%PROGRAMFILES(X86)%\BraveSoftware\Brave-Browser\Application\brave.exe",
        r"%LOCALAPPDATA%\BraveSoftware\Brave-Browser\Application\brave.exe",
    ],
    "וואטסאפ": [
        r"%LOCALAPPDATA%\WhatsApp\WhatsApp.exe",
        r"%PROGRAMFILES%\WindowsApps\WhatsApp.exe",
    ],
    # וורד (Microsoft Word) - Office רגיל (Click-to-Run) בגרסאות שונות.
    "וורד": [
        r"%PROGRAMFILES%\Microsoft Office\root\Office16\WINWORD.EXE",
        r"%PROGRAMFILES(X86)%\Microsoft Office\root\Office16\WINWORD.EXE",
        r"%PROGRAMFILES%\Microsoft Office\Office16\WINWORD.EXE",
        r"%PROGRAMFILES(X86)%\Microsoft Office\Office15\WINWORD.EXE",
    ],
    # OneNote - נקרא "מחברת" בפקודות הקוליות ("דאוס פתח מחברת"), לפי
    # בקשת המשתמש - כך "מחברת" הוא שם התצוגה שדרכו הפקודה תואמת.
    "מחברת": [
        r"%PROGRAMFILES%\Microsoft Office\root\Office16\ONENOTE.EXE",
        r"%PROGRAMFILES(X86)%\Microsoft Office\root\Office16\ONENOTE.EXE",
        r"%PROGRAMFILES%\Microsoft Office\Office16\ONENOTE.EXE",
    ],
    # Notepad - נקרא "כתבן" בפקודות הקוליות, לפי בקשת המשתמש. תמיד קיים
    # ב-Windows (System32), אז זו זיהוי כמעט מובטח.
    "כתבן": [
        r"%WINDIR%\System32\notepad.exe",
        r"%WINDIR%\notepad.exe",
    ],
}


# שם תצוגה בעברית -> מחרוזת חיפוש (חלקית) בשם התצוגה של Windows -
# עבור אפליקציות UWP/Store שאין להן קובץ exe נגיש (ראו find_uwp_app_shell_path).
# נבדקות רק כ"גיבוי" אם לא נמצא כלום ב-_WELL_KNOWN_APPS למעלה.
_UWP_APP_HINTS = {
    "וואטסאפ": "WhatsApp",
}


def _expand(path: str) -> str:
    return os.path.expandvars(path)


def find_first_existing(candidates: list[str]):
    """מחזיר את הנתיב הראשון מתוך הרשימה שקיים בפועל על הדיסק, או None."""
    for candidate in candidates:
        expanded = _expand(candidate)
        if "%" not in expanded and os.path.exists(expanded):
            return expanded
    return None


def find_uwp_app_shell_path(name_hint: str, logger=None):
    """מחפש אפליקציית UWP/Microsoft Store מותקנת (כמו WhatsApp) לפי
    חלק משם התצוגה שלה ב-Windows, דרך PowerShell Get-StartApps, ומחזיר
    נתיב "shell:appsFolder\\<AppUserModelID>" שאפשר לפתוח עם
    os.startfile - או None אם לא נמצאה אפליקציה תואמת (או שקרתה
    שגיאה, למשל PowerShell לא זמין)."""
    try:
        ps_script = (
            "$m = Get-StartApps | Where-Object { $_.Name -like '*%s*' } "
            "| Select-Object -First 1; if ($m) { $m.AppID }" % name_hint
        )
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", ps_script],
            capture_output=True, text=True, timeout=10, check=False,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        app_id = result.stdout.strip()
        if not app_id:
            if logger:
                logger.debug("לא נמצאה אפליקציית UWP תואמת ל-'%s'", name_hint)
            return None
        shell_path = "shell:appsFolder\\" + app_id
        if logger:
            logger.info("אותרה אפליקציית UWP/Store '%s' אוטומטית: %s", name_hint, shell_path)
        return shell_path
    except Exception:
        if logger:
            logger.debug("נכשל בחיפוש אפליקציית UWP '%s'", name_hint, exc_info=True)
        return None


def auto_detect_apps(logger=None) -> dict:
    """
    סורק את כל האפליקציות הידועות (_WELL_KNOWN_APPS) ומחזיר dict של
    {שם_בעברית: נתיב} רק עבור אלה שבאמת נמצאו על הדיסק. לאפליקציות
    שלא נמצאו כך אבל מוגדרות ב-_UWP_APP_HINTS (כמו WhatsApp) - מנסים
    גם איתור דרך Get-StartApps (ראו find_uwp_app_shell_path).
    """
    found = {}
    for name, candidates in _WELL_KNOWN_APPS.items():
        path = find_first_existing(candidates)
        if path:
            found[name] = path
            if logger:
                logger.info("אותרה אפליקציה '%s' אוטומטית: %s", name, path)
            continue

        uwp_hint = _UWP_APP_HINTS.get(name)
        if uwp_hint:
            shell_path = find_uwp_app_shell_path(uwp_hint, logger)
            if shell_path:
                found[name] = shell_path
                continue

        if logger:
            logger.debug("לא אותרה אפליקציה '%s' באף אחד מהנתיבים הידועים", name)
    return found


def known_app_names() -> list[str]:
    return list(_WELL_KNOWN_APPS.keys())
