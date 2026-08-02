"""
פותח את ה-AI Engine (Gemini, דרך התכונה המובנית "Ask Gemini" של Chrome
- קיצור Alt+G) ומסייע ללחוץ אוטומטית על כפתור המיקרופון שלו.

**גילוי מפתח, אחרי שכל הגרסאות הקודמות של הקובץ הזה טעו בהנחת היסוד**:
Alt+G לא פותח טאב/חלונית *בתוך* חלון כרום קיים בכלל - הוא פותח **חלון
צף עצמאי וייעודי** ("Ask Gemini"), נפרד לגמרי מכל חלון דפדפן, בדיוק
כמו שהמשתמש מקבל בלחיצה פיזית על alt+g - **בלי שום צורך שחלון כרום
כלשהו יהיה פתוח או בפוקוס קודם**. כל הגרסאות הקודמות כאן ניסו למקד/
לפתוח חלון כרום *לפני* שליחת הקיצור - וזו בדיוק הסיבה שזה "עבד לפעמים,
פתח דפדפן במקום לפעמים": ניהול הפוקוס לא היה שייך לבעיה בכלל, ולפעמים
אפילו פתח דפדפן מיותר כתופעת לוואי לא-רצויה.

**הסיבה האמיתית ש-alt+g "מהקוד" לא עבד**: חבילת Python `keyboard`
(וכל שיטת SendInput שמבוססת על Virtual-Key codes, לא Scan codes)
שולחת קלט סינתטי שסוג הרישום הגלובלי (RegisterHotKey) של כרום ל-Alt+G
לפעמים מתעלם ממנו. הפתרון שנבדק ואומת בפועל: שליחת הקשה בעזרת
SendInput עם **KEYEVENTF_SCANCODE** (קודי סריקה אמיתיים, בדיוק כמו
מקלדת פיזית) במקום קודי מקש וירטואליים - זה עובד באופן עקבי, בלי שום
ניהול פוקוס/חלונות נדרש בכלל (ראו _send_hotkey_scancode למטה).

הרצף המלא היום:
  1. ודאות שתהליך כרום קיים בכלל ברקע (בלי לגעת בפוקוס/חלונות!) - אם
     לא, פותחים אותו ברקע. נחוץ כי alt+g הוא קיצור גלובלי שנרשם
     (RegisterHotKey) על ידי תהליך כרום עצמו - אם התהליך לא רץ בכלל,
     אין מי שירשום את הקיצור.
  2. שליחת הקיצור (ברירת מחדל alt+g) בעזרת scan codes אמיתיים, **פעם
     אחת בלבד** - זהו כפתור toggle, שליחה נוספת "כדי לוודא" הייתה
     סוגרת בחזרה את מה שזה עתה נפתח. **בכוונה אין כאן שום fallback
     URL** - alt+g לבדו אמור לעבוד תמיד, בדיוק כמו לחיצה פיזית.
  3. אם "דיבור אוטומטי" מופעל בהגדרות - ניסיון לזהות ולללחוץ אוטומטית,
     באמצעות זיהוי תמונה (assets/icons/mic_*.png, ראו
     ai_engine/screen_actions.py::click_icon), על כפתור המיקרופון/
     השיחה בחלון ה-Ask Gemini. לחיצה כזו אינה אמינה ב-100% - אם היא
     נכשלת, זה לא שובר כלום.

יש כאן גם הגנת "cooldown": אם מזוהות שתי הפעלות קרובות מדי זו לזו
(ברירת מחדל: פחות מ-4 שניות ביניהן) - ההפעלה השנייה מתעלמת, כדי
שהקיצור alt+g לעולם לא יישלח פעמיים ברצף בטעות (מה שהיה סוגר את
החלון שזה עתה נפתח, בגלל שזה toggle).

כל שלב רושם ללוג בדיוק מה קרה, כדי שאם ה"קסם" לא עובד על המחשב שלך
תוכל לראות איפה זה נתקע.
"""

import logging
import os
import time

from utils.win_input import send_key_combo
from utils.win_focus import force_foreground_window

try:
    import win32gui
    import win32con
    import win32process
    _HAS_WIN32 = True
except ImportError:
    _HAS_WIN32 = False

try:
    import psutil
    _HAS_PSUTIL = True
except ImportError:
    _HAS_PSUTIL = False

_log = logging.getLogger("deus")

# זמן ההפעלה האחרונה (time.time()) - משמש למניעת הפעלה כפולה/שליחה כפולה
# של קיצור ההפעלה אם מתקבלות שתי פקודות "דאוס" קרובות מדי זו לזו.
_last_trigger_time = 0.0


def _find_ai_engine_hwnd(title_hint: str):
    if not _HAS_WIN32:
        return None

    matches = []

    def _enum_handler(hwnd, _ctx):
        if win32gui.IsWindowVisible(hwnd):
            title = win32gui.GetWindowText(hwnd)
            if title_hint.lower() in title.lower():
                matches.append(hwnd)

    win32gui.EnumWindows(_enum_handler, None)
    return matches[0] if matches else None


def _bring_to_foreground(hwnd):
    """מביא בכוח את hwnd לקדמה, ומוודא בפועל שזה הצליח - לא רק שלא
    נזרקה שגיאה. ראו utils/win_focus.py::force_foreground_window
    לתיעוד המלא (טכניקת AttachThreadInput) - הלוגיקה בפועל גרה שם
    עכשיו (שימוש משותף עם ai_engine/commands.py, שמשתמש באותה טכניקה
    כדי למקד חלון PowerShell לפני הדבקת 'claude')."""
    return force_foreground_window(hwnd, _log)


# דפדפנים נפוצים ב-Windows שיכולים להיות "דפדפן ברירת המחדל" בפועל
# (מה ש-webbrowser.open()/os.startfile() בפועל פותחים) - לא רק כרום.
# **נבדק בפועל**: אצל המשתמש שבשבילו זה נכתב, ברירת המחדל האמיתית
# היא Brave, לא Chrome! חיפוש חלון לפי "chrome.exe" קשיח היה נכשל
# *תמיד* בשקט (0 תהליכי chrome.exe נמצאים אף פעם, כי הם פשוט לא
# רצים) - לא בעיית תזמון, פשוט חיפוש אחרי התהליך הלא נכון לגמרי.
_COMMON_BROWSER_PROCESS_NAMES = [
    "chrome.exe", "brave.exe", "msedge.exe", "firefox.exe", "opera.exe", "vivaldi.exe",
]


def _find_any_browser_hwnd(config: dict):
    """מוצא חלון של דפדפן כלשהו שרץ בפועל - מנסה קודם את השם המוגדר
    בקונפיג (config["ai_engine"]["browser_process_name"]), ואם לא
    נמצא - עובר על רשימת דפדפנים נפוצים (ראו _COMMON_BROWSER_PROCESS_NAMES)
    ומחזיר את הראשון שבאמת נמצא לו חלון. כך זה עובד גם כשדפדפן ברירת
    המחדל האמיתי של המשתמש שונה מהניחוש הקשיח בקונפיג."""
    configured = config.get("ai_engine", {}).get("browser_process_name", "chrome.exe")
    names = [configured] + [n for n in _COMMON_BROWSER_PROCESS_NAMES if n != configured]
    for name in names:
        hwnd = _find_process_hwnd(name)
        if hwnd is not None:
            return hwnd, name
    return None, None


def bring_browser_to_foreground(config: dict, log: logging.Logger = None) -> bool:
    """מביא לקדמה חלון כלשהו של הדפדפן שבאמת רץ (ראו _find_any_browser_hwnd -
    לא מניח שזה בהכרח כרום) - נקרא אחרי webbrowser.open() לפתיחת אתרים
    (לא רק בזרימת ה-alt+g של ג'מיני). הסיבה שזה נחוץ: אם הדפדפן כבר
    פתוח ברקע (למשל נשאר פתוח מ"דאוס צאט" קודם), webbrowser.open()
    פותח את הכתובת החדשה כטאב חדש *באותו חלון* בלי להביא אותו לקדמה -
    בפועל האתר כן נפתח, אבל בלי שהמשתמש רואה את זה בכלל, מה שיוצר
    רושם מוטעה של "כלום לא קרה". ראו ai_engine/commands.py -
    _open_url_and_focus, שקוראת לפונקציה הזו אחרי כל webbrowser.open().

    **מנסה כמה פעמים אם צריך, לחלון זמן ארוך מספיק**: נבדק בפועל
    (בהקשר אחר - מיקוד חלון PowerShell) ש-SetForegroundWindow יכול
    להיכשל לגיטימית גם עם AttachThreadInput (Windows חוסם החלפת
    פוקוס במקרים מסוימים) - בניגוד לשליחת alt+g (toggle, שליחה כפולה
    מזיקה), *אין* בעיה לנסות שוב כאן - זו רק בקשת מיקוד, בלי תופעות
    לוואי אם היא "נשלחת" כמה פעמים.

    **חשוב - נבדק בפועל שגרסה קודמת (3 ניסיונות מהירים, כ-1 שנייה
    בסך הכל) לא הספיקה**: אם כרום סגור *לגמרי* (0 תהליכים) כשנשלחת
    פקודת חיפוש, פתיחת חלון ראשון "קר" יכולה לקחת כמה שניות טובות
    (נמדד בהקשר אחר באותו פרויקט - עד 5-6 שניות עם הרבה תוספים/
    סימניות) - חלון זמן קצר מדי גורם לפונקציה לוותר ולדווח "נכשל",
    למרות שבפועל כרום כן נפתח (רק מאוחר יותר, ולרוב עם פוקוס אוטומטי
    משלו כחלון חדש-לגמרי) - כך שההצלחה שנראית למשתמש לא קשורה בפועל
    לניסיון המיקוד כאן. חלון ההמתנה כאן הוארך משמעותית כדי שזה יהיה
    נכון גם באמת, לא רק "נראה שעבד במקרה"."""
    log = log or _log
    time.sleep(0.3)  # רגע קטן כדי שהדפדפן יספיק ליצור/לרשום את הטאב/החלון החדש

    max_attempts = 20  # עד כ-6 שניות (0.3 שניות המתנה ראשונית + 20*0.3)
    for attempt in range(1, max_attempts + 1):
        hwnd, found_name = _find_any_browser_hwnd(config)
        if hwnd is None:
            log.debug("לא נמצא חלון דפדפן להביא לקדמה (ניסיון %d/%d)", attempt, max_attempts)
        elif _bring_to_foreground(hwnd):
            log.debug(
                "הבאת חלון הדפדפן (%s) לקדמה אחרי פתיחת אתר: הצליח (ניסיון %d/%d)",
                found_name, attempt, max_attempts,
            )
            return True
        if attempt < max_attempts:
            time.sleep(0.3)

    log.debug("הבאת חלון הדפדפן לקדמה אחרי פתיחת אתר: נכשל אחרי 3 ניסיונות")
    return False


def open_ai_engine(config: dict) -> bool:
    """מנסה לפתוח את ה-AI Engine רק אם הוגדר protocol_uri מפורש בקונפיג
    (אופציונלי, ריק כברירת מחדל). בכוונה **אין** כאן פתיחת כתובת אתר
    כלשהי כ-fallback - הפתיחה/המיקוד בפועל נעשים על ידי הקיצור הגלובלי
    (ראו _send_trigger_hotkey), כדי לא לפתוח/לרענן טאב באופן לא רצוי."""
    cop_cfg = config.get("ai_engine", {})
    protocol_uri = cop_cfg.get("protocol_uri", "")

    if not protocol_uri:
        _log.debug("אין protocol_uri מוגדר - מדלג על שלב הפתיחה, "
                    "מסתמך על קיצור המקשים בלבד")
        return False

    try:
        os.startfile(protocol_uri)
        _log.info("ה-AI Engine נפתח דרך הפרוטוקול %s", protocol_uri)
        return True
    except Exception as e:
        _log.warning("לא ניתן היה לפתוח דרך %s (%s)", protocol_uri, e)
        return False


def _find_process_hwnd(process_name: str):
    """מוצא handle לחלון עליון גלוי כלשהו ששייך לתהליך עם השם הנתון
    (למשל chrome.exe). משמש להבאת חלון דפדפן לקדמה אחרי פתיחת אתר
    (bring_browser_to_foreground) ולזיהוי אזור מסך לחיפוש אייקונים
    (_get_browser_window_rect) - **לא** קשור יותר לשליחת alt+g עצמה
    (ראו תיעוד הקובץ למעלה - alt+g לא דורש פוקוס על שום חלון בכלל)."""
    if not _HAS_WIN32 or not _HAS_PSUTIL:
        return None

    process_name_lower = process_name.lower()
    matches = []

    def _enum_handler(hwnd, _ctx):
        if not win32gui.IsWindowVisible(hwnd) or not win32gui.GetWindowText(hwnd):
            return
        try:
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            if psutil.Process(pid).name().lower() == process_name_lower:
                matches.append(hwnd)
        except Exception:
            pass

    try:
        win32gui.EnumWindows(_enum_handler, None)
    except Exception:
        return None
    return matches[0] if matches else None


def _find_gemini_popup_hwnd(process_name: str):
    """מוצא את חלון ה-Ask Gemini הייעודי - בניגוד ל-_find_process_hwnd,
    **לא** דורש כותרת (title) לא ריקה: נבדק בפועל שלחלון הזה אין בכלל
    כותרת ברמת ה-OS (הוא borderless, בלי caption) - GetWindowText
    מחזיר עבורו מחרוזת ריקה תמיד, אז _find_process_hwnd (שנועד למצוא
    חלון *דפדפן רגיל*, ומדלג בכוונה על חלונות בלי כותרת) אף פעם לא
    מוצא אותו. כשיש כמה חלונות chrome.exe גלויים תואמים - מעדיפים את
    הקטן ביותר, כי הפופאפ תמיד קטן משמעותית מחלון דפדפן מלא.

    זה לא רק עניין תקינות - זו גם הסיבה העיקרית ל"איטיות": בלי region
    ממוקד, כל חיפוש אייקון סורק את המסך *כולו* (נבדק בפועל: ~0.55
    שניות לחיפוש בודד על מסך 2560x1600!), ורצף המיקרופון/פאוז עושה
    כמה חיפושים ברצף - ביחד זה הצטבר לכמה שניות. עם region ממוקד
    לחלון הקטן הזה, כל חיפוש בודד הופך מהיר משמעותית."""
    if not _HAS_WIN32 or not _HAS_PSUTIL:
        return None
    process_name_lower = process_name.lower()
    # סף גודל סביר: החלון הייעודי שנצפה בפועל היה סביב 528x351 עד
    # 792x527 - חלון דפדפן רגיל (אפילו לא ממוקסם) בדרך כלל גדול הרבה
    # יותר. בלי הסף הזה, אם אין בכלל פופאפ פתוח אבל יש חלון כרום רגיל
    # אחד, הוא היה "הקטן ביותר" (כי הוא היחיד) ונבחר בטעות - מה שהיה
    # גורם לחשוב "הפופאפ כבר פתוח" ולדלג בטעות על הפתיחה האמיתית שלו.
    _MAX_POPUP_DIM = 1000
    candidates = []

    def _enum_handler(hwnd, _ctx):
        if not win32gui.IsWindowVisible(hwnd):
            return
        try:
            rect = win32gui.GetWindowRect(hwnd)
        except Exception:
            return
        width, height = rect[2] - rect[0], rect[3] - rect[1]
        if width <= 0 or height <= 0:
            return
        if width > _MAX_POPUP_DIM or height > _MAX_POPUP_DIM:
            return
        try:
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            if psutil.Process(pid).name().lower() == process_name_lower:
                candidates.append((width * height, hwnd))
        except Exception:
            pass

    try:
        win32gui.EnumWindows(_enum_handler, None)
    except Exception:
        return None
    if not candidates:
        return None
    candidates.sort(key=lambda c: c[0])
    return candidates[0][1]


def _ensure_browser_process_running(config: dict) -> None:
    """מוודא שתהליך הדפדפן קיים בכלל ברקע - **בלי לגעת בפוקוס/חלונות
    בכלל**. נחוץ כי alt+g הוא קיצור גלובלי שנרשם (RegisterHotKey) על
    ידי תהליך כרום עצמו - אם התהליך לא רץ בכלל, אין מי שירשום את
    הקיצור, ושליחתו לא יכולה לעבוד בשום מקרה. בכוונה **לא** מביאים שום
    חלון לקדמה כאן - alt+g פותח חלון AI ייעודי משלו (לא טאב/חלון דפדפן
    רגיל), ולא צריך שום חלון דפדפן גלוי/בפוקוס בשביל זה - בדיוק כמו
    לחיצה פיזית. גרסה קודמת של הפונקציה הזו כן ניסתה למקד/להביא חלון
    דפדפן לקדמה - וזו בדיוק הסיבה שהיא גרמה לתופעת הלוואי הלא-רצויה של
    "פתיחת חלון דפדפן רגיל" שלא הייתה קשורה בכלל לחלון ה-AI המיוחד."""
    process_name = config.get("ai_engine", {}).get("browser_process_name", "chrome.exe")

    pids_before = set()
    if _HAS_PSUTIL:
        try:
            pids_before = {
                p.pid for p in psutil.process_iter(["name"])
                if p.info["name"] and p.info["name"].lower() == process_name.lower()
            }
        except Exception:
            pids_before = set()
        if pids_before:
            return

    _log.info("תהליך %s לא רץ בכלל - פותח אותו ברקע כדי שהקיצור הגלובלי יעבוד", process_name)
    try:
        # os.startfile ולא subprocess.Popen([process_name]): נבדק בפועל
        # ש-Popen(["chrome.exe"]) זורק FileNotFoundError כשכרום לא נמצא
        # פשוטו כמשמעו בתיקיות שב-PATH (המקרה השכיח - chrome.exe כמעט
        # אף פעם לא על ה-PATH). os.startfile עובר דרך ShellExecute, שכן
        # בודק את מפתח הרישום App Paths - בדיוק כמו לחיצה על קיצור.
        os.startfile(process_name)
    except Exception:
        _log.debug("נכשל בניסיון לפתוח את %s", process_name, exc_info=True)
        return

    if not _HAS_PSUTIL:
        time.sleep(2.5)  # גיבוי בלי psutil - אין דרך לבדוק בפועל מתי זה מוכן
        return

    # מאתרים את ה-PID החדש שהופיע (os.startfile לא מחזיר PID ישירות,
    # בניגוד ל-subprocess.Popen) - ואז ממתינים בפועל שהוא "יירגע"
    # (יסיים לעלות), במקום לנחש שינה קבועה שמשתנה בין מחשבים.
    new_pid = None
    deadline = time.time() + 5.0
    while time.time() < deadline and new_pid is None:
        try:
            current = {
                p.pid for p in psutil.process_iter(["name"])
                if p.info["name"] and p.info["name"].lower() == process_name.lower()
            }
        except Exception:
            current = set()
        new_pids = current - pids_before
        if new_pids:
            new_pid = next(iter(new_pids))
        else:
            time.sleep(0.15)

    if new_pid is None:
        time.sleep(2.5)  # לא הצלחנו לאתר את התהליך החדש - גיבוי
        return

    from utils.proc_wait import wait_for_process_idle
    if not wait_for_process_idle(new_pid, _log, max_wait_sec=8.0):
        time.sleep(1.0)  # שולי ביטחון קטן, ליתר ביטחון


def _send_hotkey_scancode(hotkey: str) -> bool:
    """שולח את hotkey (למשל "alt+g") בעזרת SendInput עם קודי סריקה
    אמיתיים (ראו utils/win_input.py והתיעוד למעלה) - **לא** קודי מקש
    וירטואליים (מה ש-keyboard.send()/press() בפייתון שולחים כברירת
    מחדל). זו בדיוק הסיבה שהקיצור "עבד לפעמים" קודם: קודי VK לפעמים
    לא מזוהים על ידי הרישום הגלובלי (RegisterHotKey) של כרום, בעוד
    שקודי סריקה (בדיוק כמו מקלדת פיזית) עובדים בעקביות. נבדק ואומת
    בפועל. מחזיר True אם כל המקשים בקיצור זוהו ונשלחו."""
    parts = [p.strip().lower() for p in hotkey.split("+") if p.strip()]
    sent = send_key_combo(parts)
    if not sent:
        _log.warning("אחד המקשים בקיצור '%s' לא מוכר ב-utils/win_input.SCAN_CODES", hotkey)
    return sent


def _send_trigger_hotkey(config: dict) -> bool:
    """מוודא שתהליך הדפדפן קיים (בלי לגעת בפוקוס/חלונות בכלל - ראו
    _ensure_browser_process_running) ואז שולח את קיצור ההפעלה (ברירת
    מחדל alt+g) בעזרת קודי סריקה אמיתיים, **פעם אחת בלבד** (זהו כפתור
    toggle - שליחה נוספת הייתה סוגרת בחזרה את מה שזה עתה נפתח).
    מחזיר True אם הקיצור נשלח בהצלחה."""
    cop_cfg = config.get("ai_engine", {})
    if not cop_cfg.get("try_voice_hotkey", True):
        _log.debug("try_voice_hotkey כבוי בקונפיג - מדלג")
        return False

    hotkey = cop_cfg.get("trigger_hotkey", "alt+g")

    _ensure_browser_process_running(config)

    try:
        sent = _send_hotkey_scancode(hotkey)
        if sent:
            _log.info("נשלח קיצור ההפעלה: %s (scan codes)", hotkey)
        return sent
    except Exception:
        _log.exception("שליחת קיצור ההפעלה %s נכשלה", hotkey)
        return False


def _get_browser_window_rect(config: dict):
    """מחזיר את גבולות חלון ה-Ask Gemini הייעודי (left, top, right, bottom)
    על המסך, או None אם לא נמצא/לא זמין. משמש כדי להגביל את חיפוש
    האייקונים (ראו _try_auto_speech_click) לתוך החלון הזה בלבד - גם
    לדיוק (ראו ההערה המפורטת ב-ai_engine/screen_actions.py::click_icon
    על false positive מול פקדי מדיה של Windows) וגם למהירות (חיפוש על
    המסך כולו לוקח בפועל ~0.55 שניות; על החלון הקטן הזה - הרבה פחות).
    ראו _find_gemini_popup_hwnd: לחלון הזה אין כותרת OS בכלל."""
    if not _HAS_WIN32:
        return None
    process_name = config.get("ai_engine", {}).get("browser_process_name", "chrome.exe")
    hint = config.get("ai_engine", {}).get("window_title_hint", "Gemini")
    hwnd = _find_ai_engine_hwnd(hint) or _find_gemini_popup_hwnd(process_name)
    if hwnd is None:
        return None
    try:
        return win32gui.GetWindowRect(hwnd)
    except Exception:
        return None


def _click_icon_with_retry(icon_name: str, config: dict, attempts: int = 5, delay: float = 0.5) -> bool:
    """כמו click_icon, אבל מנסה כמה פעמים במקום ניסיון בודד - קריטי כי
    חלון ה-Ask Gemini הייעודי עדיין יכול להיות באמצע רינדור/טעינה
    ברגע שהחיפוש הראשון קורה (במיוחד מיד אחרי שליחת הקיצור). מחשבים
    את region מחדש בכל ניסיון (לא פעם אחת מראש) - כדי לתפוס את החלון
    גם אם הוא נהיה גלוי/מוצא רק בניסיון מאוחר יותר, לא רק בראשון."""
    from ai_engine.screen_actions import click_icon
    for attempt in range(1, attempts + 1):
        region = _get_browser_window_rect(config)
        if click_icon(icon_name, config, _log, region=region):
            return True
        if attempt < attempts:
            time.sleep(delay)
    return False


def _try_auto_speech_click(config: dict):
    """אם 'דיבור אוטומטי' מופעל - מנסה לזהות ולללחוץ על כפתור המיקרופון/
    השיחה בחלון של ג'מיני, בעזרת זיהוי תמונה. לא קריטי אם זה נכשל.

    נצפה בפועל: לפעמים ג'מיני לא מתחיל להאזין בפועל - במקום כפתור
    מיקרופון מוצג אייקון "פאוז" (assets/icons/pause*.png), **גם מיד
    בפתיחת החלון** (לא רק אחרי לחיצה על מיקרופון). הרצף שמתקן את זה
    (תועד ידנית): לוחצים על הפאוז, ואז שוב על המיקרופון - ורק אז
    ג'מיני באמת מתחיל להקשיב. **חשוב**: בודקים פאוז תמיד - גם אם
    המיקרופון *לא* נמצא בכלל (זה בדיוק המקרה שתואר: "פאוז" הופיע
    ישר, בלי שהמיקרופון היה מוצג קודם) - לא רק כתלות בהצלחת לחיצת
    המיקרופון הראשונה."""
    cop_cfg = config.get("ai_engine", {})
    if not cop_cfg.get("auto_speech", True):
        _log.debug("דיבור אוטומטי כבוי בהגדרות - מדלג על לחיצה אוטומטית")
        return

    # שינה קצרה בכוונה - polling מהיר קורה בלולאה למטה, אין טעם גם
    # לחכות הרבה מראש.
    wait_sec = cop_cfg.get("icon_search_wait_sec", 0.15)
    time.sleep(wait_sec)

    try:
        from ai_engine.screen_actions import click_icon

        # **מהירות**: במקום לנסות מיקרופון N פעמים *במלואן* ורק אחר כך
        # להתחיל לבדוק פאוז בכלל - בודקים את שניהם *באותו סבב*, לסירוגין.
        # נבדק בפועל שהחלון לעיתים קרובות נפתח ישר במצב "פאוז" (בלי
        # מיקרופון בכלל) - לחכות למיצוי כל ניסיונות המיקרופון לפני
        # שבודקים פאוז בכלל היה מוסיף כמעט שנייה שלמה של המתנה מיותרת
        # בדיוק במקרה הזה, שהוא לא נדיר.
        found_state = None
        max_rounds = 6
        for round_num in range(max_rounds):
            region = _get_browser_window_rect(config)
            if click_icon("mic", config, _log, region=region):
                found_state = "mic"
                break
            if click_icon("pause", config, _log, region=region):
                found_state = "pause"
                break
            if round_num < max_rounds - 1:
                time.sleep(0.12)

        if found_state == "pause":
            _log.info("זוהה מצב 'פאוז' - לוחץ שוב על המיקרופון כדי שג'מיני יתחיל להקשיב")
            time.sleep(0.15)
            _click_icon_with_retry("mic", config, attempts=4, delay=0.12)
        elif found_state is None:
            _log.info("לא נמצא לא מיקרופון ולא פאוז (ייתכן שהחלון עדיין נטען, או שכבר במצב שיחה)")
    except Exception:
        _log.exception("ניסיון הלחיצה האוטומטית על כפתור השיחה נכשל")


def trigger_type_mode(config: dict):
    """נקרא כשמזוהה הפקודה הקולית 'הקלדה' - מנסה ללחוץ אוטומטית על כפתור
    המעבר להקלדה בחלון של ג'מיני, בעזרת זיהוי תמונה."""
    try:
        from ai_engine.screen_actions import click_icon
        clicked = click_icon("keyboard", config, _log)
        if not clicked:
            _log.info("לא נמצא כפתור מעבר להקלדה על המסך")
    except Exception:
        _log.exception("ניסיון הלחיצה האוטומטית על כפתור ההקלדה נכשל")


def launch_and_activate(config: dict):
    global _last_trigger_time

    cop_cfg = config.get("ai_engine", {})
    cooldown = cop_cfg.get("trigger_cooldown_sec", 4)

    now = time.time()
    if now - _last_trigger_time < cooldown:
        _log.info(
            "מתעלם מהפעלה כפולה של ה-AI Engine (פחות מ-%.1f שניות מההפעלה "
            "הקודמת) - כדי שהקיצור לא יישלח פעמיים", cooldown
        )
        return
    _last_trigger_time = now

    _log.info("--- מפעיל את ה-AI Engine (Alt+G -> חלון Ask Gemini הייעודי) ---")

    # פתיחה דרך protocol_uri מותאם אישית, רק אם הוגדר כזה בקונפיג
    open_ai_engine(config)

    # alt+g הוא toggle - אם החלון כבר פתוח *עכשיו*, השליחה הזו תסגור
    # אותו, לא תפתח כלום. במקרה כזה אין שום טעם לרוץ אחרי זה את כל
    # רצף חיפוש/לחיצת המיקרופון (ai_engine/screen_actions.py) - אין מה
    # למצוא, כי החלון בדרך להיעלם. זו גם הסיבה העיקרית לאיטיות שנצפתה
    # בפועל: בלי הבדיקה הזו, כל "סגירה" (בערך חצי מהפעלות בפועל, כי
    # זה toggle) עדיין ניסתה לחפש אייקון על המסך כולו כמה פעמים לחינם.
    was_open_before = _get_browser_window_rect(config) is not None

    # שליחת קיצור המקשים (ברירת מחדל alt+g) - זהו המנגנון היחיד לפתיחה
    # בכוונה: **בלי** שום ניהול פוקוס/חלונות דפדפן (ראו תיעוד הקובץ
    # למעלה - alt+g פותח חלון AI ייעודי משלו, בדיוק כמו לחיצה פיזית,
    # ולא דורש חלון דפדפן פתוח/בפוקוס בכלל) ובלי שום fallback URL.
    _send_trigger_hotkey(config)

    if was_open_before:
        _log.info("החלון כבר היה פתוח - הקיצור סגר אותו; מדלג על לחיצת מיקרופון אוטומטית")
    else:
        # שינה קצרה בכוונה - _try_auto_speech_click עושה polling מהיר
        # בפני עצמו (ראו שם), אין טעם גם לחכות הרבה כאן מראש.
        wait_sec = cop_cfg.get("focus_wait_sec", 0.15)
        time.sleep(wait_sec)
        _try_auto_speech_click(config)

    _log.info("--- סיום רצף הפעלת ה-AI Engine ---")
