"""
Deus - עוזר קולי שעוטף AI Engine מבוסס דפדפן (כרגע: Gemini) ומבצע פקודות.

זרימת העבודה:
  1. חלון שקוף עם GIF תמיד מוצג על המסך (idle).
  2. ברקע, WakeWordDetector מאזין למיקרופון ומתמלל מקומית עם Whisper.
  3. כשמזוהה "Deus" בתוך מה שנאמר - זו רק מילת מפתח. המילים אחריה
     נבדקות מול טבלת הפקודות (ai_engine/commands.py): פתיחת אפליקציה,
     חיפוש בגוגל/ביוטיוב, פתיחת ה-AI Engine (Gemini), צילום מסך, Enter,
     פתיחת Claude Code, הפעלת סקריפט, לימוד/הפעלת מאקרו, או מעבר למצב
     תמלול (הקלדה חופשית לשורה הפעילה). אם לא זוהתה פקודה מוכרת - לא
     קורה כלום.
  4. תוך כדי ביצוע פקודה - האנימציה עוברת ל-thinking. תוך כדי השתקה -
     עוברת ל-shut1 ואז shut2 (עד ביטול ההשתקה), ומודל התמלול משוחרר
     מהזיכרון (ונטען מחדש אוטומטית בביטול ההשתקה).

הרצה:
    python main.py             הרצה רגילה, החלון מוצג
    python main.py --minimized הרצה בשקט (למשל מהפעלה האוטומטית של Windows) -
                                 האפליקציה עולה למגש בלי להציג את החלון
"""

import argparse
import ctypes
import json
import logging
import os
import shutil
import subprocess
import sys

# חייב לרוץ *לפני* כל דבר אחר שעלול לשאול את Windows על גודל המסך/
# מיקום חלונות (win32gui, pyautogui, וגם Qt עצמו) - ראו הסבר מפורט:
# תהליך Python שלא "מודע" ל-DPI (ברירת המחדל) מקבל מ-Windows קואורדינטות
# *וירטואליות* (מוקטנות לפי אחוז ה-scaling של התצוגה, למשל 150% -> מסך
# 2560x1600 אמיתי נראה לתהליך כ-1707x1067 בלבד) מכל קריאה כמו
# win32gui.GetWindowRect/GetSystemMetrics - בעוד ש-mss (המשמש לצילומי
# מסך ולזיהוי אייקונים ב-ai_engine/screen_actions.py) *תמיד* מחזיר
# פיקסלים אמיתיים. אם קריאה ל-win32gui קורית לפני שמשהו (Qt/pyautogui)
# "הפך" את התהליך למודע-DPI - מתקבל אי-התאמה של פי 1.5 (או כל יחס
# scaling אחר) בין הקואורדינטות שמחושבות (win32) לאלה שבהן משתמשים
# בפועל לצילום/לחיצה (mss/pyautogui) - למשל אזור חיפוש שגוי לגמרי
# ב-ai_engine/launcher.py::_get_browser_window_rect. נבדק בפועל: לפני
# הקריאה הזו GetSystemMetrics מחזיר 1707, אחריה - 2560 (הרוחב האמיתי).
# קוראים לזה כאן, כבר בשורה הראשונה של הקובץ, כדי שזה תמיד יקרה מוקדם
# מספיק - בלי תלות בסדר imports אקראי במקום אחר בקוד.
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PROCESS_PER_MONITOR_DPI_AWARE
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()  # נפילה חזרה לגרסאות Windows ישנות יותר
    except Exception:
        pass

from PySide6.QtCore import QObject, Signal, Slot
from PySide6.QtWidgets import QApplication

from ui.overlay_window import OverlayWindow
from speech.wake_word import WakeWordDetector
from ai_engine.commands import execute_command
from utils.logging_setup import setup_logging, set_logging_enabled, get_log_path
from utils import autostart

# BASE_DIR הוא תיקיית הקוד/ה-assets: בזמן פיתוח - תיקיית main.py עצמה.
# ב-exe שנבנה עם PyInstaller --onefile - זו תיקייה *זמנית* (sys._MEIPASS,
# בתוך %TEMP%) שנוצרת מחדש בכל הפעלה ונמחקת ביציאה. בגלל זה אסור לשמור
# לתוכה שום דבר שצריך "לשרוד" בין הפעלות (כמו config.json) - זה בדיוק
# מה שקרה קודם: שינויי הגדרות (אפליקציות, קיצורים וכו') "נעלמו" בכל
# הפעלה מחדש של ה-exe, כי הם נשמרו לתיקייה שממילא נמחקת. assets/ (גיפים,
# אייקונים, צלילים) כן בסדר גמור להישאר ב-BASE_DIR - הם קבועים וזהים
# בכל הפעלה.
BASE_DIR = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
_DEFAULT_CONFIG_PATH = os.path.join(BASE_DIR, "config.json")

DEFAULT_HOTKEY = "ctrl+alt+d"

# מסמן תהליך שכבר הופעל מחדש אוטומטית פעם אחת (ראו handle_critical_
# whisper_failure ב-main()) - נקבע כמשתנה סביבה על התהליך החדש שנוצר,
# כדי שלא ניכנס ללולאה אינסופית של הפעלות-מחדש אם התקלה אינה זמנית.
_AUTO_RESTART_ENV_VAR = "DEUS_AUTO_RESTARTED"


def _user_config_dir() -> str:
    """תיקייה יציבה וניתנת לכתיבה, שלא נמחקת בין הפעלות - בדיוק כמו
    שכבר עושים ב-utils/logging_setup.py לקובץ הלוג."""
    local_appdata = os.environ.get("LOCALAPPDATA")
    if local_appdata:
        config_dir = os.path.join(local_appdata, "Deus")
    else:
        # נפילה חזרה (למשל בזמן פיתוח על מערכת שאינה Windows) - עדיין
        # יציב יותר מ-BASE_DIR כשרצים כ-onefile exe.
        config_dir = os.path.join(
            os.path.dirname(os.path.abspath(sys.argv[0])), "config_data"
        )
    os.makedirs(config_dir, exist_ok=True)
    return config_dir


CONFIG_PATH = os.path.join(_user_config_dir(), "config.json")


def _merge_config_updates(persisted: dict, default: dict) -> bool:
    """
    ממזג לתוך persisted כל מפתח/ערך שקיים ב-default (קובץ ברירת המחדל
    שנשלח עם עדכון קוד) אבל עדיין לא קיים אצל המשתמש - **בלי לדרוס**
    שום ערך קיים. חשוב במיוחד כי CONFIG_PATH נכתב פעם אחת בהפעלה
    הראשונה ומאז לא נוגעים בו יותר מקובץ ברירת המחדל - כך שכל שיפור
    עתידי (ביטויי פקודות חדשים, הגדרות חדשות כמו סקריפטים/מאקרואים/
    צלילים) שנשלח בעדכון קוד היה "נעלם" אחרת, כי המשתמש כבר יש לו
    CONFIG_PATH ישן משלו.

    - dict בשני הצדדים -> מיזוג רקורסיבי (יורדים פנימה).
    - list בשני הצדדים (כמו רשימות ביטויים לפקודות) -> איחוד (union):
      כל פריט חדש בברירת המחדל שעדיין לא קיים אצל המשתמש מתווסף,
      בלי להסיר שום דבר שהמשתמש כבר הוסיף בעצמו.
    - מפתח שקיים רק בברירת המחדל -> מתווסף כמו שהוא.
    - מפתח/ערך שהמשתמש כבר שינה (למשל opacity, apps, hotkey) -> לא נוגעים בו.

    מחזיר True אם בוצע שינוי בפועל (כדי לדעת אם שווה לשמור בחזרה לדיסק).
    """
    changed = False
    for key, default_value in default.items():
        if key not in persisted:
            persisted[key] = default_value
            changed = True
            continue

        current_value = persisted[key]
        if isinstance(default_value, dict) and isinstance(current_value, dict):
            if _merge_config_updates(current_value, default_value):
                changed = True
        elif isinstance(default_value, list) and isinstance(current_value, list):
            for item in default_value:
                if item not in current_value:
                    current_value.append(item)
                    changed = True
        # אחרת (מחרוזת/מספר/בוליאני קיים) - זה כבר ערך של המשתמש, לא נוגעים בו.

    return changed


def load_config() -> dict:
    if not os.path.exists(CONFIG_PATH):
        # ריצה ראשונה על המחשב הזה - "שותלים" עותק של קובץ ברירת המחדל
        # (שנצרב בתוך ה-exe, או נמצא לצד main.py בזמן פיתוח) לתוך
        # התיקייה היציבה. מכאן והלאה כל קריאה/כתיבה הן מה-CONFIG_PATH
        # היציב, ולא נוגעים שוב בעותק המקורי.
        try:
            shutil.copyfile(_DEFAULT_CONFIG_PATH, CONFIG_PATH)
        except Exception:
            logging.getLogger("deus").exception(
                "נכשל בהעתקת קובץ ההגדרות המקורי מ-%s אל %s",
                _DEFAULT_CONFIG_PATH, CONFIG_PATH,
            )

    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        config = json.load(f)

    # מיזוג אוטומטי: כל מפתח/ביטוי חדש שנוסף לקובץ ברירת המחדל בעדכון
    # קוד (למשל ביטוי פקודה נוסף, סף חדש) - מתווסף אוטומטית להגדרות
    # השמורות של המשתמש, בלי לדרוס שום דבר שהוא כבר שינה בעצמו.
    merged = False
    try:
        with open(_DEFAULT_CONFIG_PATH, "r", encoding="utf-8") as f:
            default_config = json.load(f)
        if _merge_config_updates(config, default_config):
            merged = True
            with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump(config, f, ensure_ascii=False, indent=2)
    except Exception:
        logging.getLogger("deus").exception("נכשל במיזוג עדכוני קונפיג")

    for state, path in config.get("gifs", {}).items():
        if path and not os.path.isabs(path):
            config["gifs"][state] = os.path.join(BASE_DIR, path)

    # נשמר כדי שמודולים אחרים (כמו זיהוי התמונה וניגון הצלילים) יוכלו
    # לפתור נתיבים יחסיים (תיקיית האייקונים, תיקיית הצלילים) אל מול
    # תיקיית הבסיס של האפליקציה (ה-assets, לא תיקיית ההגדרות היציבה).
    config["_base_dir"] = BASE_DIR
    # מוחזר בנפרד (לא כמפתח בתוך config) כדי ש-main() ידווח על זה
    # ללוג *אחרי* ש-setup_logging() כבר הגדיר קובץ לוג - load_config()
    # עצמו נקרא לפני שיש בכלל לוגר עם handler, אז לוג שהיה נכתב כאן
    # היה פשוט אובד בשקט.
    config["_config_was_merged"] = merged

    return config


def _ensure_config_file_exists():
    """אם config.json נעלם באמצע ריצה מסיבה כלשהי (התופעה השכיחה
    ביותר: תוכנת אנטי-וירוס ש"חושדת" בקובץ חדש שנכתב על ידי exe
    לא-חתום ומעבירה/מוחקת אותו - שכיח מאוד עם exe-ים שנבנים עצמאית
    ולא עברים code signing; ראו גם "פתרון תקלות נפוצות" ב-README) -
    בעבר כל ניסיון שמירה הבא היה נכשל בשקט עם FileNotFoundError בלוג,
    וההגדרות (מיקום חלון, אפליקציות וכו') פשוט לא נשמרות יותר בלי
    שהמשתמש ידע למה. עכשיו הקובץ פשוט נוצר מחדש מברירת המחדל, כדי
    שהשמירה הבאה תצליח - ולא נופלים חזרה להתנהגות השקטה הישנה."""
    if os.path.exists(CONFIG_PATH):
        return
    try:
        shutil.copyfile(_DEFAULT_CONFIG_PATH, CONFIG_PATH)
        logging.getLogger("deus").warning(
            "config.json חסר (%s) - כנראה נמחק על ידי תוכנה חיצונית "
            "(למשל אנטי-וירוס שחשד בקובץ חדש שנכתב על ידי exe לא-חתום). "
            "שוחזר מברירת המחדל - הגדרות מותאמות אישית שנשמרו קודם "
            "(אפליקציות/קיצורים/וכו') אבדו ויוגדרו מחדש אוטומטית "
            "(למשל סריקת אפליקציות תרוץ שוב בהפעלה הבאה). כדאי להוסיף "
            "חריג/exclusion לתיקיית Deus באנטי-וירוס כדי שזה לא יקרה שוב.",
            CONFIG_PATH,
        )
    except Exception:
        logging.getLogger("deus").exception("נכשל בשחזור config.json שנעלם")


def _persist_config_value(path_keys, value):
    """שומר ערך בודד חזרה לקובץ config.json על הדיסק, בלי לגעת בשאר
    הקובץ (וללא ה-BASE_DIR/נתיבים המוחלטים שנוספו בזיכרון בלבד)."""
    _ensure_config_file_exists()
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            raw = json.load(f)
        node = raw
        for key in path_keys[:-1]:
            node = node.setdefault(key, {})
        node[path_keys[-1]] = value
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(raw, f, ensure_ascii=False, indent=2)
    except Exception:
        logging.getLogger("deus").exception(
            "נכשל בשמירת ההגדרה %s לקובץ config.json", path_keys
        )


class Bridge(QObject):
    """
    מגשר בין ת'רד הרקע של זיהוי הדיבור לבין ה-UI. אסור לגעת ב-widgets
    ישירות מת'רד אחר - לכן משתמשים ב-Signals שמגיעים בבטחה ל-thread הראשי.
    """
    state_changed = Signal(str)
    command_detected = Signal(str, str)  # (command_id, argument)
    error_occurred = Signal(str)
    macro_saved = Signal(str, dict)  # (macro_name, {"events": [...], "repeat": 1})
    critical_whisper_failure = Signal()


def _register_mute_hotkey(hotkey: str, callback, logger):
    """רושם קיצור מקשים גלובלי להשתקה/הפעלה של ההאזנה (עובד גם כשהחלון
    לא ממוקד). לא דורש הרשאות מנהל ברוב המקרים על Windows."""
    try:
        import keyboard
        keyboard.add_hotkey(hotkey, callback)
        logger.info("קיצור מקשים להשתקה נרשם בהצלחה: %s", hotkey)
        return True
    except Exception:
        logger.exception("נכשל ברישום קיצור המקשים %s (ייתכן שצריך הרשאות מנהל)", hotkey)
        return False


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--minimized", action="store_true",
                         help="הפעלה בשקט בלי להציג את החלון (למשל מהפעלה אוטומטית)")
    args = parser.parse_args()

    config = load_config()
    logger = setup_logging(
        enabled=config.get("enable_logging", False),
        level=config.get("log_level", "INFO"),
    )
    logger.info("מריץ עם --minimized=%s", args.minimized)
    if config.pop("_config_was_merged", False):
        logger.info("נוספו הגדרות/ביטויים חדשים מעדכון הקוד אל %s", CONFIG_PATH)

    # --- סריקת אפליקציות אוטומטית בהפעלה הראשונה בלבד ---
    # במקום לדרוש מהמשתמש להוסיף ידנית נתיבים לאפליקציות נפוצות (וורד,
    # כרום, כתבן וכו') - בפעם הראשונה שדאוס רץ על המחשב הזה, סורקים
    # אוטומטית (ai_engine/app_finder.py) ומוסיפים כל מה שנמצא. לא דורס
    # שום דבר שהמשתמש כבר הגדיר בעצמו (רק ממלא מפתחות/שמות שעדיין לא
    # קיימים), ורץ פעם אחת בלבד (מסומן ב-config["_apps_auto_scanned"]).
    if not config.get("_apps_auto_scanned", False):
        try:
            from ai_engine.app_finder import auto_detect_apps
            found_apps = auto_detect_apps(logger)
            apps_cfg = config.setdefault("apps", {})
            for name, path in found_apps.items():
                apps_cfg.setdefault(name, path)
            config["_apps_auto_scanned"] = True
            _persist_config_value(["apps"], apps_cfg)
            _persist_config_value(["_apps_auto_scanned"], True)
            logger.info("סריקת אפליקציות אוטומטית (הפעלה ראשונה) הושלמה: %s",
                        list(found_apps.keys()))
        except Exception:
            logger.exception("נכשלה סריקת האפליקציות האוטומטית בהפעלה הראשונה")

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)  # החלון יכול להיסגר/auto-hide בלי לסגור את האפליקציה

    window = OverlayWindow(config, logger)
    if args.minimized:
        window.hide()
    else:
        window.show()

    # סנכרון מצב תיבות הסימון בתפריט המגש עם המצב האמיתי
    window.set_autostart_checked(autostart.is_autostart_enabled())
    window.set_logging_checked(config.get("enable_logging", False))
    window.set_auto_speech_checked(config.get("ai_engine", {}).get("auto_speech", True))

    bridge = Bridge()
    bridge.state_changed.connect(window.set_state)
    bridge.error_occurred.connect(lambda msg: logger.error("שגיאה: %s", msg))

    @Slot(str, str)
    def on_command_main_thread(command_id: str, argument: str):
        logger.info("מבצע פקודה '%s' (ארגומנט='%s')", command_id, argument)
        execute_command(command_id, argument, config, logger)

    bridge.command_detected.connect(on_command_main_thread)

    @Slot(str, dict)
    def handle_macro_saved(name: str, macro: dict):
        config.setdefault("macros", {})[name] = macro
        _persist_config_value(["macros"], config["macros"])
        logger.info("מאקרו חדש נשמר: '%s' (%d אירועים)", name, len(macro.get("events", [])))

    bridge.macro_saved.connect(handle_macro_saved)

    detector = WakeWordDetector(
        config,
        logger,
        on_command=lambda command_id, argument: bridge.command_detected.emit(command_id, argument),
        on_state_change=lambda state: bridge.state_changed.emit(state),
        on_error=lambda msg: bridge.error_occurred.emit(msg),
        on_macro_saved=lambda name, macro: bridge.macro_saved.emit(name, macro),
        on_critical_whisper_failure=lambda: bridge.critical_whisper_failure.emit(),
    )

    # מסנכרן את תיבת הסימון "מצב חסכון (עכשיו)" עם המצב **האמיתי**
    # (לא רק הקונפיג) - כי הוא עשוי להיקבע אוטומטית בזיהוי GPU חלש
    # (ראו speech/wake_word.py::_has_strong_gpu) גם בלי שהמשתמש ביקש.
    window.set_economy_mode_now_checked(detector.is_economy_mode())

    # --- תפריט מגש: השתקה ---
    def handle_mute_toggle():
        detector.toggle_mute()
        window.set_mute_checked(detector.is_muted())

    window.mute_toggle_requested.connect(handle_mute_toggle)

    # --- קיצור מקשים גלובלי להשתקה ---
    hotkey = config.get("hotkeys", {}).get("toggle_mute", DEFAULT_HOTKEY)
    _register_mute_hotkey(hotkey, handle_mute_toggle, logger)

    # --- תפריט מגש: הפעלה אוטומטית עם Windows ---
    def handle_autostart_toggle(checked: bool):
        ok = autostart.enable_autostart() if checked else autostart.disable_autostart()
        if not ok:
            logger.error("שינוי הפעלה אוטומטית נכשל (checked=%s)", checked)
        window.set_autostart_checked(autostart.is_autostart_enabled())

    window.autostart_toggle_requested.connect(handle_autostart_toggle)

    # --- תפריט מגש: הפעלת/כיבוי לוגים בזמן ריצה ---
    def handle_logging_toggle(checked: bool):
        set_logging_enabled(logger, checked)
        config["enable_logging"] = checked
        _persist_config_value(["enable_logging"], checked)

    window.logging_toggle_requested.connect(handle_logging_toggle)

    # --- תפריט מגש: פתיחת קובץ הלוג ---
    def handle_open_log():
        log_path = get_log_path()
        try:
            os.startfile(log_path)
        except Exception:
            logger.exception("לא ניתן לפתוח את קובץ הלוג")

    window.open_log_requested.connect(handle_open_log)

    # --- תפריט מגש: דיבור אוטומטי ---
    def handle_auto_speech_toggle(checked: bool):
        config.setdefault("ai_engine", {})["auto_speech"] = checked
        _persist_config_value(["ai_engine", "auto_speech"], checked)
        logger.info("דיבור אוטומטי: %s", "הופעל" if checked else "כובה")

    window.auto_speech_toggle_requested.connect(handle_auto_speech_toggle)

    # --- תפריט מגש: שינוי קיצור ההפעלה של ה-AI ---
    def handle_hotkey_change(new_hotkey: str):
        config.setdefault("ai_engine", {})["trigger_hotkey"] = new_hotkey
        _persist_config_value(["ai_engine", "trigger_hotkey"], new_hotkey)
        logger.info("קיצור ההפעלה של ה-AI Engine עודכן ל: %s", new_hotkey)

    window.hotkey_change_requested.connect(handle_hotkey_change)

    # --- תפריט מגש: ניהול אפליקציות (עבור פקודת "פתח את X") ---
    def handle_apps_changed(apps: dict):
        config["apps"] = apps
        _persist_config_value(["apps"], apps)
        logger.info("רשימת האפליקציות עודכנה: %s", list(apps.keys()))

    window.apps_changed.connect(handle_apps_changed)

    # --- תפריט מגש: ניהול סקריפטים (עבור פקודת "הפעל סקריפט X") ---
    def handle_scripts_changed(scripts: dict):
        config["scripts"] = scripts
        _persist_config_value(["scripts"], scripts)
        logger.info("רשימת הסקריפטים עודכנה: %s", list(scripts.keys()))

    window.scripts_changed.connect(handle_scripts_changed)

    # --- תפריט מגש: ניהול מאקרואים (מחיקה / קביעת מספר חזרות) ---
    def handle_macros_changed(macros: dict):
        config["macros"] = macros
        _persist_config_value(["macros"], macros)
        logger.info("רשימת המאקרואים עודכנה (מהדיאלוג): %s", list(macros.keys()))

    window.macros_changed.connect(handle_macros_changed)

    # --- תפריט מגש: ניהול אתרים (עבור פקודת "פתח אתר" ותפריט פתיחה מהירה) ---
    def handle_sites_changed(sites: dict):
        config["sites"] = sites
        _persist_config_value(["sites"], sites)
        logger.info("רשימת האתרים עודכנה: %s", list(sites.keys()))

    window.sites_changed.connect(handle_sites_changed)

    # --- תפריט מגש: הגדרות ניקוי זיכרון (סגירת מצלמה / החרגות) ---
    def handle_ram_clean_settings_changed(ram_cfg: dict):
        config["ram_clean"] = ram_cfg
        _persist_config_value(["ram_clean"], ram_cfg)
        logger.info("הגדרות ניקוי הזיכרון עודכנו: %s", ram_cfg)

    window.ram_clean_settings_changed.connect(handle_ram_clean_settings_changed)

    # --- תפריט מגש: הקראת תרגומים בקול (הפעלה/כיבוי) ---
    def handle_translate_speak_toggle(checked: bool):
        config.setdefault("translate", {})["speak"] = checked
        _persist_config_value(["translate", "speak"], checked)
        logger.info("הקראת תרגומים בקול: %s", "הופעלה" if checked else "כובתה")

    window.translate_speak_toggle_requested.connect(handle_translate_speak_toggle)

    # --- תפריט מגש: טיימר כיבוי מחשב ---
    # שימו לב: זה נפרד מהפקודה הקולית "דאוס כיבוי מחשב" (ai_engine/
    # commands.py::_shutdown_computer, עם ברירת המחדל שלה בדקות) - אבל
    # שתיהן "מכריזות" עם אותו קובץ צליל (shutdown_timer_set), דרך
    # detector.announce_external, כדי שההתנהגות תהיה עקבית בין תפריט
    # לקול (ראו speech/wake_word.py::announce_external).
    def handle_shutdown_timer_requested(minutes: int):
        seconds = max(1, int(minutes)) * 60
        try:
            subprocess.run(["shutdown", "/s", "/t", str(seconds)], check=False)
            logger.info("נקבע טיימר כיבוי מחשב בעוד %d דקות", minutes)
            detector.announce_external("shutdown_timer_set")
        except Exception:
            logger.exception("נכשל בקביעת טיימר כיבוי מחשב")

    window.shutdown_timer_requested.connect(handle_shutdown_timer_requested)

    def handle_shutdown_timer_cancel():
        try:
            subprocess.run(["shutdown", "/a"], check=False)
            logger.info("טיימר כיבוי המחשב בוטל")
            detector.announce_external("shutdown_timer_cancel")
        except Exception:
            logger.exception("נכשל בביטול טיימר כיבוי מחשב")

    window.shutdown_timer_cancel_requested.connect(handle_shutdown_timer_cancel)

    # --- תפריט מגש: גודל / שקיפות / לחיצה-דרך (נשמר לפעם הבאה) ---
    def handle_size_change(size: int):
        config.setdefault("window", {})["start_width"] = size
        config["window"]["start_height"] = size
        _persist_config_value(["window", "start_width"], size)
        _persist_config_value(["window", "start_height"], size)

    window.size_change_requested.connect(handle_size_change)

    def handle_opacity_change(value: float):
        config.setdefault("window", {})["opacity"] = value
        _persist_config_value(["window", "opacity"], value)

    window.opacity_change_requested.connect(handle_opacity_change)

    def handle_auto_hide_near_cursor_toggle(checked: bool):
        config.setdefault("window", {})["auto_hide_near_cursor"] = checked
        _persist_config_value(["window", "auto_hide_near_cursor"], checked)
        logger.info("'העלם את דאוס אוטומטית' %s", "הופעל" if checked else "כובה")

    window.auto_hide_near_cursor_toggle_requested.connect(handle_auto_hide_near_cursor_toggle)

    # --- זכירת מיקום החלון בין הפעלות (נשמר רק כשגרירה מסתיימת) ---
    def handle_position_changed(x: int, y: int):
        config.setdefault("window", {})["start_x"] = x
        config["window"]["start_y"] = y
        _persist_config_value(["window", "start_x"], x)
        _persist_config_value(["window", "start_y"], y)

    window.position_changed.connect(handle_position_changed)

    # --- תפריט מגש: מיקרופון בהקלטת מסך (ברירת מחדל: מופעל) ---
    def handle_screen_record_mic_toggle(checked: bool):
        config.setdefault("screen_record", {})["include_mic"] = checked
        _persist_config_value(["screen_record", "include_mic"], checked)
        logger.info("מיקרופון בהקלטת מסך: %s", "מופעל" if checked else "כבוי")

    window.screen_record_mic_toggle_requested.connect(handle_screen_record_mic_toggle)

    # --- תפריט מגש: ברירת מחדל (דקות) לפקודה הקולית "דאוס כיבוי מחשב" ---
    def handle_shutdown_voice_default(minutes: int):
        config.setdefault("shutdown_timer", {})["voice_default_minutes"] = minutes
        _persist_config_value(["shutdown_timer", "voice_default_minutes"], minutes)
        logger.info("ברירת המחדל לכיבוי מחשב בקול עודכנה ל-%d דקות", minutes)

    window.shutdown_voice_default_requested.connect(handle_shutdown_voice_default)

    # --- תפריט מגש: הפעל תמיד במצב חסכון (נשמר להפעלה הבאה) ---
    def handle_economy_mode_start_toggle(checked: bool):
        config["whisper_start_in_economy_mode"] = checked
        _persist_config_value(["whisper_start_in_economy_mode"], checked)
        logger.info("'הפעל תמיד במצב חסכון' %s (ישפיע מההפעלה הבאה)",
                     "הופעל" if checked else "כובה")

    window.economy_mode_start_toggle_requested.connect(handle_economy_mode_start_toggle)

    # --- תפריט מגש: מצב חסכון (עכשיו) - שינוי מיידי, לא רק להפעלה הבאה ---
    def handle_economy_mode_now_toggle(checked: bool):
        detector.set_economy_mode(checked)
        detector.announce_external(
            "economy_mode_on" if checked else "economy_mode_off",
            active_state="thinking",
        )

    window.economy_mode_now_toggle_requested.connect(handle_economy_mode_now_toggle)

    # --- קיצור מקשים גלובלי למעבר מיידי בין מצב חסכון למצב רגיל ---
    # קיים בנוסף לפקודות הקוליות ("דאוס מצב חסכון" / "דאוס תחזור") כדי
    # שיהיה אפשר לחזור למצב הרגיל גם אם התמלול במצב חסכון (Whisper
    # small) לא מספיק מדויק כדי לזהות בכלל את הפקודה הקולית "תחזור".
    def handle_economy_mode_hotkey_toggle():
        new_state = not detector.is_economy_mode()
        detector.set_economy_mode(new_state)
        # בלי זה, הקיצור היה מחליף מצב "בשקט" - בלי שום דרך לדעת אם
        # המעבר הצליח או לאיזה מצב הוא עבר, בניגוד לפקודות הקוליות
        # המקבילות ("דאוס מצב חסכון"/"תחזור") שכן מכריזות.
        detector.announce_external(
            "economy_mode_on" if new_state else "economy_mode_off",
            active_state="thinking",
        )
        # מסנכרן גם את תיבת הסימון בתפריט - אחרת היא הייתה נשארת
        # "תקועה" על המצב הישן עד סגירה/פתיחה מחדש של דאוס, כי תפריט
        # המגש נבנה פעם אחת בהפעלה ולא נבנה מחדש בכל פתיחה.
        window.set_economy_mode_now_checked(new_state)

    economy_hotkey = config.get("hotkeys", {}).get("toggle_economy_mode", "ctrl+alt+p")
    _register_mute_hotkey(economy_hotkey, handle_economy_mode_hotkey_toggle, logger)

    # --- תפריט מגש: עוצמת קול של צלילי דאוס (0-100) ---
    def handle_volume_change(percent: int):
        config.setdefault("sounds", {})["volume"] = percent
        _persist_config_value(["sounds", "volume"], percent)
        logger.info("עוצמת הקול של דאוס עודכנה ל-%d%%", percent)

    window.volume_change_requested.connect(handle_volume_change)

    def handle_quit():
        detector.stop()
        app.quit()

    window.quit_requested.connect(handle_quit)

    # --- הפעלה מחדש אוטומטית (פעם אחת) אם טעינת Whisper נכשלת סופית ---
    # נצפה אמפירית (במיוחד בהפעלה הראשונה אחרי התקנה טרייה): תהליך
    # אחד יכול להיכשל שוב ושוב לפתוח model.bin (גם עם ניסיונות חוזרים
    # והורדה מחדש - ראו _load_whisper_with_recovery), בעוד שתהליך *חדש*
    # לגמרי טוען את *אותו* קובץ בהצלחה תוך שניות. במקום לדרוש מהמשתמש
    # לסגור ולפתוח את דאוס ידנית בפעם הראשונה - עושים את זה אוטומטית,
    # פעם אחת בלבד (מסומן ב-_AUTO_RESTART_ENV_VAR כדי לא ליפול ללולאה
    # אינסופית אם התקלה בכל זאת אינה זמנית - למשל אין חיבור אינטרנט
    # בכלל). אם גם ההפעלה השנייה נכשלת - דאוס ממשיך לרוץ כרגיל (בלי
    # זיהוי קולי, כמו קודם), במקום לנסות שוב.
    def handle_critical_whisper_failure():
        if os.environ.get(_AUTO_RESTART_ENV_VAR) == "1":
            logger.warning(
                "טעינת מודל Whisper נכשלה גם אחרי הפעלה מחדש אוטומטית - "
                "לא מנסים שוב (כדי למנוע לולאה אינסופית). דאוס ימשיך לרוץ בלי זיהוי קולי."
            )
            return

        logger.warning(
            "טעינת מודל Whisper נכשלה סופית בהפעלה זו - כנראה תקלה זמנית "
            "שקשורה לתהליך/לרגע הזה (למשל מיד אחרי התקנה טרייה). מפעיל "
            "את דאוס מחדש אוטומטית (פעם אחת בלבד) כדי להתגבר על כך..."
        )
        try:
            env = os.environ.copy()
            env[_AUTO_RESTART_ENV_VAR] = "1"
            if getattr(sys, "frozen", False):
                # לא מריצים את sys.executable המקורי כמו שהוא: ב-PyInstaller
                # --onefile (6.x) שם תיקיית החילוץ הזמנית (_MEIxxxxxx) נגזרת
                # מנתיב ה-exe עצמו, ולכן שתי הרצות של *אותו* exe.exe מקבלות
                # את *אותה* תיקייה. כשהתהליך הישן (זה) יוצא מיד אחרי שהחדש
                # מופעל (Popen לא ממתין), ה-bootloader של התהליך הישן מתחיל
                # לנקות את התיקייה המשותפת בדיוק כשהתהליך החדש עדיין באמצע
                # קריאת קבצים ממנה - מירוץ (race) שגורם לתהליך החדש למצוא
                # קבצים חסרים (GIF-ים, אפילו certifi/cacert.pem - מה שגרם
                # לכשל SSL בהורדת המודל בהפעלה השנייה). הפתרון: מריצים
                # מ*עותק* זמני של ה-exe (בנתיב שונה) - נתיב שונה => hash/שם
                # תיקייה שונה => אין התנגשות, גם אם התהליך הישן מנקה את שלו.
                restart_dir = os.path.join(
                    os.environ.get("TEMP", os.path.expanduser("~")), "DeusRestart"
                )
                os.makedirs(restart_dir, exist_ok=True)
                # ניקוי עותקים ישנים מהפעלות-מחדש קודמות (best-effort - אם
                # עותק ישן עדיין נעול כי התהליך שלו עוד רץ, פשוט מדלגים עליו).
                for old_name in os.listdir(restart_dir):
                    try:
                        os.remove(os.path.join(restart_dir, old_name))
                    except OSError:
                        pass
                exe_copy_path = os.path.join(restart_dir, f"Deus_{os.getpid()}.exe")
                shutil.copy2(sys.executable, exe_copy_path)
                cmd = [exe_copy_path] + sys.argv[1:]
            else:
                cmd = [sys.executable, os.path.abspath(__file__)] + sys.argv[1:]
            subprocess.Popen(cmd, env=env, close_fds=True)
        except Exception:
            logger.exception("ניסיון ההפעלה מחדש האוטומטית נכשל - דאוס ימשיך לרוץ כרגיל")
            return

        handle_quit()

    bridge.critical_whisper_failure.connect(handle_critical_whisper_failure)

    detector.start()

    exit_code = app.exec()
    detector.stop()
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
