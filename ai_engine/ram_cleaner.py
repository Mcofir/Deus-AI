"""
"דאוס נקה זיכרון" / "נקה ראם" / "סגור הכל" - סוגר תהליכים שצורכים
הרבה זיכרון ומנקה זיכרון פנוי, בהשראת הלוגיקה של סקריפט ניקוי-RAM
מבוסס batch/PowerShell (לא אותו סקריפט - רק אותו רעיון/שלבים):

  1. (אופציונלי, לפי תפריט המגש) השבתת מצלמה מובנית/USB.
  2. סגירה בכוח (Stop-Process) של רשימת תהליכים "כבדים" מוכרים מראש,
     תוך החרגה של תהליכים מוגנים (המערכת עצמה, דאוס עצמו, Python,
     PowerShell/CMD) וגם רשימת החרגה שהמשתמש הגדיר בתפריט המגש.
  3. סריקה כללית וסגירה של כל תהליך נוסף שצורך יותר מ-100MB (לפי אותה
     החרגה), כדי לתפוס גם תהליכים "כבדים" שלא ברשימה הקבועה.
  4. ניקוי working set (זיכרון פיזי שתפוס לריק) לתהליכים שנשארו, דרך
     SetProcessWorkingSetSize (WinAPI) - שקול ל"ניקוי RAM standby" בלי
     כלים חיצוניים.

חשוב - בכוונה **אסור** לפעולה הזו לגעת ברשת/Wi-Fi בשום צורה (לא
להשבית מתאמי רשת, לא לעצור שירותי רשת) - זו דרישה מפורשת. גם אם
בעתיד יתווספו עוד אופציות ניקוי, אל תוסיפו כאן שום קוד שמכבה
אינטרנט/Wi-Fi/Bluetooth.

תלות: psutil (pip install psutil). בלעדיה הפונקציה עדיין לא קורסת -
רק מדווחת בלוג שהיא לא זמינה ומדלגת.
"""

import ctypes
import logging
import os
import subprocess
import sys

try:
    import psutil
    _HAS_PSUTIL = True
except ImportError:
    _HAS_PSUTIL = False

_log = logging.getLogger("deus")

# תהליכים "כבדים" מוכרים מראש שכדאי לנסות לסגור קודם (בהשראת הרשימה
# המקורית שבה נעשה שימוש בסקריפט הניקוי) - ניתן להרחיב בקונפיג דרך
# config["ram_clean"]["extra_targets"].
_DEFAULT_TARGET_PROCESSES = [
    "Discord", "brave", "msedgewebview2", "ONENOTE", "ONENOTEM",
    "SystemSettings", "WindowsTerminal", "Notepad", "StartMenuExperienceHost",
    "ShareX", "SearchHost", "msedge", "chrome", "WhatsApp", "AnyDesk",
    "TeamViewer", "TeamViewer_Service", "TabTip", "TextInputHost",
    "ApplicationFrameHost", "backgroundTaskHost", "WidgetService",
    "Everything", "CrossDeviceService", "OneDrive", "Widgets", "Photos",
    "LockApp", "qbittorrent", "SecurityHealthSystray", "UserOOBEBroker",
    "DataExchangeHost", "CrossDeviceResume", "smartscreen",
    "ShellExperienceHost", "AppVShNotify", "RuntimeBroker", "ShellHost",
    "Steam", "steamwebhelper",
]

# תהליכים שלעולם לא ייסגרו, גם אם המשתמש לא הוסיף אותם להחרגה שלו -
# אלה קריטיים למערכת/לתוכנה עצמה. השוואה לא תלוית-רישיות, על ידי
# "מכיל" (like), בדיוק כמו בסקריפט המקורי.
#
# חשוב במיוחד: "omen" ו-"hp" מוגנים כאן *כתת-מחרוזת רחבה* בכוונה
# (בדיוק כמו ב-clean.bat המקורי: `-notlike '*Omen*' -and -notlike '*HP*'`) -
# כל תוכנת/שירות שקשור ל-HP Omen Hub ולשליטה במאווררים (fan control)
# נמצא בסיכון לקרוס/להיסגר ולא לחזור עד ריסטארט אם נופל - חשוב מאוד
# שהם לא ייסגרו בניקוי. גם "services" (Service Control Manager, שממנו
# רצים שירותי Windows רבים כולל שירותי חומרה) מוגן במפורש - סגירה שלו
# יכולה לגרום לקריסת מערכת מלאה.
_ALWAYS_PROTECTED = [
    "explorer", "dwm", "csrss", "winlogon", "services", "lsass", "smss",
    "svchost", "conhost", "audiodg", "system", "registry",
    "secure system", "memory compression", "python", "pythonw",
    "powershell", "pwsh", "cmd",
    # "deus" מוגן כאן מהלולאה הרגילה בכוונה - לא כי דאוס לא ייסגר
    # בכלל, אלא כי הוא נסגר *בנפרד ובעדינות* (app.quit(), לא kill()
    # גס) בסוף הפונקציה, אחרי שכל שאר הניקוי כבר בוצע - ראו close_self
    # למטה. סגירה גסה של דאוס עצמו באמצע הלולאה הייתה עוצרת את כל
    # תהליך הניקוי בפתאומיות (כי דאוס עצמו הוא התהליך שמריץ את הלולאה).
    "deus",
    # HP Omen / HP - שליטה במאווררים ותוכנת החומרה של המחשב. אם אלה
    # נסגרים, השליטה במאווררים עלולה להיתקע/להיעצר עד ריסטארט מלא -
    # לכן מוגנים באופן רחב (תת-מחרוזת), לא רק שמות מדויקים.
    "omen", "hp",
]

# רשימת שמות מדויקים ידועים של שירותי HP Omen / חומרת HP - נוספת
# כתיעוד/הגנת-כפל מעבר להגנה הרחבה ("omen"/"hp") למעלה, לאותם תהליכים
# שהוזכרו כ"מוגנים" במפורש בסקריפט הניקוי המקורי (clean.bat) ששימש
# כהשראה לפונקציה הזו.
_KNOWN_HP_OMEN_SERVICES = [
    "OmenCommandCenterBackground", "OmenCap", "HPAppHelperCap",
    "HPDiagsCap", "HPNetworkCap", "HPOmenCap", "HPSysInfoCap",
    "HpTouchpointAnalyticsService", "HPSystemEventUtility",
    "HPOMENHsaService", "HPNetworkBoosterService", "OmenCommandCenterSDK",
    "OmenGamingHub", "OMENBackgroundService",
]

_HIGH_RAM_THRESHOLD_MB = 100


def _is_protected(proc_name: str, exclude_list) -> bool:
    name_lower = proc_name.lower()
    for protected in _ALWAYS_PROTECTED:
        if protected in name_lower:
            return True
    for excluded in exclude_list:
        if excluded and excluded.lower() in name_lower:
            return True
    return False


def _set_camera_enabled(enabled: bool, log: logging.Logger):
    """מפעיל/משבית מצלמה מובנית/USB דרך PowerShell (Get-PnpDevice) -
    בדיוק כמו באפשרות "D/E" של סקריפט הניקוי המקורי. לעולם לא נוגע
    ברשת/Wi-Fi/Bluetooth - זה מטופל בפונקציה נפרדת ומכוונת בלבד."""
    verb = "Enable-PnpDevice" if enabled else "Disable-PnpDevice"
    ps_command = (
        "Get-PnpDevice | Where-Object {$_.Class -eq 'Camera' -or "
        "$_.FriendlyName -like '*camera*' -or $_.FriendlyName -like '*webcam*'} "
        f"| {verb} -Confirm:$false"
    )
    try:
        subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps_command],
            capture_output=True, timeout=15, check=False,
        )
        log.info("מצלמה %s", "הופעלה" if enabled else "הושבתה")
    except Exception:
        log.exception("נכשל ב%s המצלמה", "הפעלת" if enabled else "השבתת")


def _empty_working_set(pid: int):
    """מנקה את ה-working set (הזיכרון הפיזי התפוס) של תהליך, בלי לסגור
    אותו - שקול לשלב "Emptying working sets" בסקריפט המקורי."""
    try:
        PROCESS_QUERY_INFORMATION = 0x0400
        PROCESS_SET_QUOTA = 0x0100
        handle = ctypes.windll.kernel32.OpenProcess(
            PROCESS_QUERY_INFORMATION | PROCESS_SET_QUOTA, False, pid
        )
        if not handle:
            return
        try:
            ctypes.windll.psapi.EmptyWorkingSet(handle)
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)
    except Exception:
        pass  # לא קריטי - ממשיכים לתהליך הבא


def clean_ram(config: dict, log: logging.Logger = None) -> bool:
    log = log or _log
    ram_cfg = config.get("ram_clean", {})

    if not _HAS_PSUTIL:
        log.warning(
            "ניקוי הזיכרון לא זמין (חבילת psutil לא מותקנת) - "
            "אפשר להתקין עם: pip install psutil"
        )
        return False

    if sys.platform != "win32":
        log.warning("ניקוי הזיכרון נתמך רק ב-Windows - מדלג")
        return False

    exclude_list = list(ram_cfg.get("exclude_processes", []))
    targets = list(_DEFAULT_TARGET_PROCESSES) + list(ram_cfg.get("extra_targets", []))
    my_pid = os.getpid()

    log.info("--- מתחיל ניקוי זיכרון ---")

    if ram_cfg.get("close_camera", False):
        _set_camera_enabled(False, log)

    killed = 0
    trimmed = 0

    target_names_lower = {t.lower() for t in targets}

    for proc in psutil.process_iter(["pid", "name", "memory_info"]):
        try:
            if proc.pid == my_pid:
                continue
            name = proc.info.get("name") or ""
            if not name:
                continue
            name_no_ext = os.path.splitext(name)[0]

            if _is_protected(name_no_ext, exclude_list):
                continue

            mem_info = proc.info.get("memory_info")
            mem_mb = (mem_info.rss / (1024 * 1024)) if mem_info else 0

            is_target = name_no_ext.lower() in target_names_lower
            is_high_ram = mem_mb > _HIGH_RAM_THRESHOLD_MB

            if is_target or is_high_ram:
                try:
                    proc.kill()
                    killed += 1
                    log.info("נסגר תהליך '%s' (PID=%d, %.1fMB)", name, proc.pid, mem_mb)
                except Exception:
                    log.debug("לא ניתן היה לסגור את '%s' (PID=%d)", name, proc.pid)
            else:
                _empty_working_set(proc.pid)
                trimmed += 1
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
        except Exception:
            log.debug("שגיאה לא צפויה בטיפול בתהליך - ממשיך הלאה", exc_info=True)
            continue

    log.info("--- ניקוי זיכרון הסתיים: %d תהליכים נסגרו, %d נוקו (working set) ---",
              killed, trimmed)

    # סגירת דאוס עצמו כחלק מהניקוי - **ברירת מחדל: כן** (אפשר לבטל
    # דרך תפריט המגש: "ניקוי זיכרון > כלול את דאוס עצמו בניקוי").
    # בכוונה קורה כאן, בסוף הפונקציה - אחרי שכל שאר הסגירות/הניקוי
    # כבר בוצעו במלואן - כדי שדאוס באמת ייסגר *אחרון*.
    if ram_cfg.get("close_self", True):
        log.info("סוגר את דאוס עצמו (אחרון, אחרי שכל שאר הניקוי הושלם)")
        try:
            from PySide6.QtWidgets import QApplication
            app = QApplication.instance()
            if app is not None:
                app.quit()
        except Exception:
            log.exception("נכשל בסגירת דאוס עצמו בסוף ניקוי הזיכרון")

    return True
