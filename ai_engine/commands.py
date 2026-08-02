"""
טבלת הפקודות של דאוס, וביצוע בפועל של כל פקודה "חד-פעמית" (בניגוד
לתמלול, שהוא מצב מתמשך ומטופל ישירות ב-speech/wake_word.py - וכמו גם
לימוד/הפעלת מאקרו, שגם הם מצבים מתמשכים ומטופלים ישירות שם, כי הם
זקוקים לגישה למופעי MacroRecorder/MacroPlayer).

איך זה עובד:
  1. אחרי שזוהתה מילת ההפעלה ("דאוס"), שאר המשפט (כל המילים שאחריה)
     מועבר ל-parse_command().
  2. parse_command בודק, לפי סדר מהביטוי הארוך ביותר לקצר ביותר (כדי
     ש"פתח קלוד קוד" ייבדק לפני "פתח" הגנרי יותר), אם *תחילת* המשפט
     תואמת בקירוב לאחד הביטויים המוגדרים בקונפיג (config["commands"]).
  3. אם נמצאה התאמה - מה שנשאר אחרי הביטוי הוא "הארגומנט" (למשל שם
     האפליקציה לפתיחה, מחרוזת החיפוש בגוגל/ביוטיוב, או שם הסקריפט).
  4. אם לא נמצאה התאמה לאף פקודה - לא מתבצעת שום פעולה (בכוונה - זו
     בדיוק ההתנהגות המבוקשת: מילת ההפעלה לבד לא עושה כלום).

הוספת פקודה חדשה בעתיד: מוסיפים ערך ל-_COMMAND_PHRASE_KEYS (מיפוי
command_id -> שם המפתח בקונפיג), מוסיפים את הביטויים לקונפיג תחת
"commands", ומטפלים ב-command_id החדש בקריאה ל-execute_command
(או ב-wake_word.py אם זו פקודת מצב מתמשך כמו תמלול/מאקרו).
"""

import ctypes
import datetime
import logging
import os
import subprocess
import time
import urllib.parse
import webbrowser

from speech.fuzzy_match import match_prefix, similarity_loose

try:
    import keyboard
    _HAS_KEYBOARD = True
except ImportError:
    _HAS_KEYBOARD = False

try:
    import win32clipboard
    import win32con
    _HAS_WIN32_CLIPBOARD = True
except ImportError:
    _HAS_WIN32_CLIPBOARD = False

_log = logging.getLogger("deus")


def _open_url_and_focus(url: str, config: dict, log: logging.Logger) -> bool:
    """webbrowser.open(url) + ניסיון להביא את חלון הדפדפן לקדמה אחרי
    זה. **חשוב**: בלי הצעד השני, אם הדפדפן כבר פתוח ברקע (למשל נשאר
    פתוח מ"דאוס צאט" קודם) - webbrowser.open() עדיין פותח את הכתובת
    בהצלחה (כטאב חדש באותו חלון), אבל בלי להביא את החלון לקדמה - כך
    שבפועל האתר *כן* נפתח, רק שהמשתמש לא רואה את זה, ונראה כאילו
    "כלום לא קרה". זו הסיבה שפקודות כמו "פתח אתר" נראו כאילו לא
    עובדות למרות שהלוג הראה הצלחה - ai_engine/launcher.py::
    bring_browser_to_foreground עושה את ההבאה-לקדמה בפועל."""
    webbrowser.open(url)  # יכול לזרוק - הקריאה נשארת בתוך try/except של הקורא
    try:
        from ai_engine.launcher import bring_browser_to_foreground
        bring_browser_to_foreground(config, log)
    except Exception:
        log.debug("לא ניתן היה להביא את חלון הדפדפן לקדמה (לא קריטי)", exc_info=True)
    return True

# --------------------------------------------------------------------- #
# ביטויים כברירת מחדל לכל פקודה
# --------------------------------------------------------------------- #
#
# אלה לא *חייבים* להיות מוגדרים בקובץ הקונפיג - parse_command (למטה)
# משתמש בהם כברירת מחדל בכל מקום שבו config["commands"][phrase_key]
# ריק/לא קיים, ומאחד (union) עם מה שכן מוגדר בקונפיג אם יש. המטרה:
# לכסות מראש כמה שיותר ניסוחים/נטיות טבעיות של אותה כוונה (לצד שכבת
# ה"התאמה לפי שורש" ב-speech/fuzzy_match.py), כדי שהמשתמש לא יצטרך
# לשנן ניסוח מדויק - אפשר גם להוסיף ניסוחים נוספים משלכם דרך הקונפיג
# בלי לאבד את אלה שכבר מוגדרים כאן.
_DEFAULT_PHRASES = {
    "claude_code_words": ["פתח קלוד קוד", "תפתח קלוד קוד", "קלוד קוד", "פתח את קלוד קוד", "קלוד"],
    "open_app_words": [
        "פתח את", "פתח", "תפתח את", "תפתח לי", "תפתח", "פתחי", "תפתחי",
        "תפתחו", "פתחו", "תפעיל את", "תפעיל", "הפעל את", "הפעל",
        "תריץ את", "תריץ", "תעלה את", "תעלה",
    ],
    "google_search_words": [
        "חפש בגוגל", "תחפש בגוגל", "חפש לי בגוגל", "תחפש לי בגוגל",
        "גוגל חיפוש", "חיפוש בגוגל", "תבדוק בגוגל", "בדוק בגוגל",
        "תחפש", "חפש",
    ],
    "open_chat_words": [
        "פתח צאט", "תפתח צאט", "פתח שיחה", "תפתח שיחה", "פתח את הבינה",
        "תפתח את הבינה", "דבר איתי", "תדבר איתי",
    ],
    "screenshot_words": [
        "צלם מסך", "תצלם מסך", "צילום מסך", "עשה צילום מסך",
        "תעשה צילום מסך", "תצלם", "צלם",
    ],
    "enter_words": ["אנטר", "לחץ אנטר", "תלחץ אנטר", "לחיצה על אנטר"],
    "media_words": [
        "מדיה", "נגן", "תנגן", "עצור נגינה", "השהה", "תשהה",
        "פליי פאוז", "המשך נגינה", "media", "Media",
    ],
    "dictation_start_words": [
        "תמלל", "תתמלל", "התחל תמלול", "תתחיל תמלול", "מצב הקלדה",
        "התחל הקלדה", "תתחיל הקלדה", "הקלד מה שאני אומר",
    ],
    "dictation_stop_words": [
        "עצור", "תעצור", "סטופ", "תפסיק", "עצירה", "די", "מספיק",
        "בטל", "תבטל",
    ],
    "youtube_search_words": [
        "חפש ביוטיוב", "תחפש ביוטיוב", "חפש לי ביוטיוב", "יוטיוב חיפוש",
        "נגן ביוטיוב", "תנגן ביוטיוב", "תשמיע ביוטיוב", "השמע ביוטיוב",
    ],
    "script_words": [
        "הפעל סקריפט", "תפעיל סקריפט", "הרץ סקריפט", "תריץ סקריפט",
        "תפעיל את הסקריפט", "הפעל את הסקריפט", "תריץ את הסקריפט",
    ],
    "macro_learn_words": [
        "תלמד מאקרו", "למד מאקרו", "תלמד מקרו", "תקליט מאקרו",
        "הקלט מאקרו", "התחל הקלטת מאקרו",
    ],
    "macro_play_words": [
        "תפעיל מקרו", "הפעל מקרו", "תפעיל מאקרו", "הפעל מאקרו",
        "הרץ מאקרו", "תריץ מאקרו",
    ],
    # --- פקודות חדשות ---
    "google_image_search_words": [
        "חפש תמונה בגוגל", "תחפש תמונה בגוגל", "חיפוש תמונה בגוגל",
        "חפש לפי תמונה", "תחפש לפי תמונה", "חפש את התמונה הזאת בגוגל",
        "חפש תמונה", "חיפוש הפוך",
    ],
    "translate_words": [
        "תרגם", "תתרגם", "תרגום", "תרגם לי", "תרגם לעברית",
        "תתרגם לעברית", "מה זה אומר", "תרגם את זה",
    ],
    "ram_clean_words": [
        "נקה זיכרון", "נקה זכרון", "נקה ראם", "תנקה זיכרון", "תנקה זכרון",
        "תנקה ראם", "סגור הכל", "תסגור הכל", "ניקוי זיכרון", "ניקוי ראם",
        "פנה זיכרון", "תפנה זיכרון",
    ],
    "delete_line_words": [
        "מחק שורה", "תמחק שורה", "מחיקת שורה", "נקה שורה", "תנקה שורה",
    ],
    "record_start_words": [
        # בכוונה *רק* צורות מבוססות "תקליט" (לא "הקלט"/"הקלטה") - כדי
        # שלא יתבלבל עם "הקלטת מסך" (screen_record_toggle), שגם היא
        # מתחילה במילה "הקלט" - ראו screen_record_toggle_words למטה.
        "תקליט", "תקליט קול", "תקליט שמע", "תקליט אודיו",
    ],
    "clicker_start_words": [
        "קליקר", "התחל קליקר", "תתחיל קליקר", "לחיצות אוטומטיות",
        "התחל ללחוץ", "תתחיל ללחוץ", "אוטו קליקר",
    ],
    "download_video_words": [
        "הורד סרטון", "תוריד סרטון", "הורדת סרטון", "הורד את הסרטון",
        "תוריד את הסרטון", "הורד וידאו", "תוריד וידאו",
    ],
    "lock_pc_words": [
        "נעל מחשב", "תנעל מחשב", "נעל את המחשב", "תנעל את המחשב",
        "נעילת מחשב", "נעל", "תנעל",
    ],
    "open_site_words": [
        "פתח אתר", "תפתח אתר", "פתח את האתר", "תפתח את האתר",
        "כנס לאתר", "תיכנס לאתר", "גלוש לאתר",
    ],
    "shutdown_app_words": [
        # ביטויים קצרים במיוחד (2-3 תווים, כמו "תצא") הוסרו בכוונה -
        # פקודה הרסנית (סוגרת את דאוס) לא צריכה להיות פגיעה במיוחד
        # להתאמות מקריות של מילים קצרות ולא-קשורות (ראו גם ההגנה
        # הכללית ב-speech/fuzzy_match.py: match_prefix דורש סף גבוה
        # יותר ככל שהביטוי קצר יותר).
        "כיבוי", "תכבה", "תכבה את עצמך", "כבה את עצמך", "סגור את עצמך",
        "תסגור את עצמך", "יציאה", "צא מהתוכנה", "תסגור את דאוס",
    ],
    "shutdown_computer_words": [
        # שונה בכוונה מ"shutdown_app" (שסוגר רק את דאוס) - זו כיבוי
        # המחשב *עצמו*. תמיד לפחות 2 מילים (כוללות "מחשב") כדי לא
        # להתבלבל עם shutdown_app הקצרה יותר.
        "כיבוי מחשב", "תכבה את המחשב", "כבה את המחשב", "תכבה מחשב",
        "כבה מחשב", "סגור את המחשב", "תסגור את המחשב",
    ],
    "economy_mode_on_words": [
        "מצב חסכון", "מצב חיסכון", "תעבור למצב חסכון", "עבור למצב חסכון",
        "חסכון במשאבים", "תחסוך במשאבים", "עבור למצב חיסכון",
    ],
    "economy_mode_off_words": [
        "תחזור", "חזור", "תחזיר", "חזרה", "צא ממצב חסכון",
        "בטל מצב חסכון", "תבטל מצב חסכון", "מצב רגיל", "חזרה למצב רגיל",
    ],
    "screen_record_toggle_words": [
        "הקלטת מסך", "תקליט מסך", "הקלט מסך", "הקלטה של המסך",
        "וידאו מסך", "תעשה הקלטת מסך", "צלם מסך בוידאו", "הקלט את המסך",
    ],
}

# command_id -> שם המפתח בקונפיג שמכיל את רשימת הביטויים המתאימים לו.
#
# שימו לב: "macro_learn" ו-"macro_play" מפורטים כאן רק כדי ש-parse_command
# (שמשותף לכל הפקודות) יזהה את הביטויים שלהם - אבל הם *לא* מבוצעים
# על ידי execute_command למטה. הם מטופלים ישירות ב-speech/wake_word.py,
# כי שם נמצאים מופעי MacroRecorder/MacroPlayer (המצב המתמשך של הקלטה/
# הפעלה). "dictation_stop" (הביטויים "סטופ"/"עצור"/"תפסיק") משמש גם
# כפקודת "עצור" גנרית - עוצרת תמלול, לימוד מאקרו, או הפעלת מאקרו,
# לפי מה שפעיל כרגע (הלוגיקה הזו נמצאת גם היא ב-wake_word.py).
_COMMAND_PHRASE_KEYS = {
    "claude_code": "claude_code_words",
    "open_app": "open_app_words",
    "google_search": "google_search_words",
    "open_chat": "open_chat_words",
    "screenshot": "screenshot_words",
    "enter": "enter_words",
    "media_toggle": "media_words",
    "dictation_start": "dictation_start_words",
    "dictation_stop": "dictation_stop_words",
    "youtube_search": "youtube_search_words",
    "run_script": "script_words",
    "macro_learn": "macro_learn_words",
    "macro_play": "macro_play_words",
    # --- פקודות חדשות ---
    "google_image_search": "google_image_search_words",
    "translate": "translate_words",
    "ram_clean": "ram_clean_words",
    "delete_line": "delete_line_words",
    # record_start/clicker_start מפורטים כאן רק כדי ש-parse_command יזהה
    # אותם - כמו macro_learn/macro_play, הם *לא* מבוצעים דרך execute_command
    # למטה אלא ישירות ב-speech/wake_word.py (הם מצבים מתמשכים, עד "דאוס עצור").
    "record_start": "record_start_words",
    "clicker_start": "clicker_start_words",
    "download_video": "download_video_words",
    "lock_pc": "lock_pc_words",
    "open_site": "open_site_words",
    # shutdown_app - כמו record_start/clicker_start וכו' - מזוהה כאן
    # כדי ש-parse_command יתפוס אותה, אבל *לא* מבוצעת דרך execute_command
    # למטה: מטופלת ישירות ב-speech/wake_word.py, כדי להבטיח שהצליל
    # שלה מספיק להתנגן *לפני* שדאוס נסגר בפועל (ראו set_economy_mode
    # וההערה ליד command_id == "shutdown_app" שם).
    "shutdown_app": "shutdown_app_words",
    "shutdown_computer": "shutdown_computer_words",
    # economy_mode_on/off ו-screen_record_toggle, כמו record_start/
    # clicker_start - מזוהות כאן כדי ש-parse_command יתפוס אותן, אבל
    # *לא* מבוצעות דרך execute_command למטה - הן מטופלות ישירות
    # ב-speech/wake_word.py (צריכות גישה למופע ה-WakeWordDetector עצמו).
    "economy_mode_on": "economy_mode_on_words",
    "economy_mode_off": "economy_mode_off_words",
    "screen_record_toggle": "screen_record_toggle_words",
}

# פקודות "חד-פעמיות" - כאלה שמבוצעות ומיד חוזרים ל-idle (בניגוד
# לתמלול/מאקרו/הקלטה/קליקר/הקלטת מסך/מצב חסכון/כיבוי-דאוס, שהם מצבים
# מתמשכים/דורשים גישה למופע הדטקטור, ומטופלים בנפרד ב-wake_word.py)
ONE_SHOT_COMMANDS = {
    "claude_code", "open_app", "google_search",
    "open_chat", "screenshot", "enter", "media_toggle",
    "youtube_search", "run_script",
    "google_image_search", "translate", "ram_clean", "delete_line",
    "download_video", "lock_pc", "open_site", "shutdown_computer",
}

# פקודות שדורשות ארגומנט (מילות חיפוש, שם אפליקציה/סקריפט/מאקרו/אתר) כדי
# להיות שימושיות בכלל. אם אחת מהן זוהתה אבל בלי ארגומנט מיד אחריה
# (למשל "דאוס חפש בגוגל" ואז שתיקה קצרה לפני שנאמרות מילות החיפוש
# בפועל, שמגיעות רק בצ'אנק השמע הבא) - wake_word.py לא מבצע אותה מיד
# עם ארגומנט ריק, אלא ממתין לצ'אנקים הבאים (ראו _pending_argument_command_id
# ב-speech/wake_word.py) - כדי לא "לירות" חיפוש ריק כל פעם שיש הפסקה
# קצרה בדיבור אחרי הפקודה. שימו לב: "translate" *לא* ברשימה הזו בכוונה -
# בלי ארגומנט הוא פשוט מתרגם את הבחירה/ה-clipboard הנוכחיים (ראו _translate).
ARGUMENT_REQUIRED_COMMANDS = {
    "google_search", "youtube_search", "open_app", "run_script",
    "macro_learn", "macro_play", "open_site",
}

# ראו ההערה המפורטת בתוך parse_command (חיפוש "_LENGTH_PREFERENCE_MARGIN")
# - כמה נמוך יותר מהציון הכי טוב שנמצא בכלל ביטוי ארוך יותר עדיין
# מותר לו "לנצח" (כדי להעדיף פקודה ספציפית על פני גנרית), לפני
# שנחשב יותר מדי חלש כדי לסמוך עליו ומתייחסים אליו כהתאמה מקרית.
_LENGTH_PREFERENCE_MARGIN = 0.15


def parse_command(words: list[str], config: dict, logger: logging.Logger = None):
    """
    מנסה להתאים את *תחילת* words (המילים שאחרי מילת ההפעלה) לאחת
    הפקודות המוגדרות בקונפיג. מחזיר (command_id, argument) או
    (None, None) אם לא נמצאה שום התאמה - ואז לא מבוצעת פעולה.
    """
    log = logger or _log
    # סף נפרד ומקל יותר לפקודות: fuzzy_threshold הראשי מכוון להיות
    # מחמיר כדי למנוע זיהוי שווא של מילת ההפעלה עצמה בתוך שיחה חופשית.
    # פקודה, לעומת זאת, נבדקת רק בהקשר מוגבל (מיד אחרי שכבר זוהתה מילת
    # ההפעלה, או בתוך חלון ההמתנה הקצר שאחריה) - אז אפשר להיות הרבה
    # יותר סלחניים לטעויות תמלול בלי לסכן false-positive מיותר.
    threshold = config.get("commands", {}).get(
        "command_fuzzy_threshold", config.get("fuzzy_threshold", 0.72)
    )
    cmd_cfg = config.get("commands", {})

    if not words:
        return None, None

    candidates = []
    for command_id, phrase_key in _COMMAND_PHRASE_KEYS.items():
        # איחוד (union) בין הביטויים שהוגדרו בקונפיג לבין ברירות המחדל
        # העשירות (_DEFAULT_PHRASES) - כדי שהתאמת פקודות תעבוד "מהקופסה"
        # במגוון ניסוחים טבעיים, גם בלי שהמשתמש הגדיר כלום בעצמו, ובלי
        # לאבד שום ביטוי מותאם-אישית שהוא כן הוסיף.
        phrases = list(cmd_cfg.get(phrase_key, []))
        for default_phrase in _DEFAULT_PHRASES.get(phrase_key, []):
            if default_phrase not in phrases:
                phrases.append(default_phrase)
        for phrase in phrases:
            candidates.append((command_id, phrase))

    # אוספים את *כל* הביטויים שמתאימים בכלל (מכל אורך), כדי לבחור
    # ביניהם בצורה מושכלת למטה - לא "המנצח הראשון שנתקלים בו".
    matched = []  # (command_id, score, remainder, phrase_len)
    for command_id, phrase in candidates:
        found, score, remainder = match_prefix(words, phrase, threshold)
        if found:
            matched.append((command_id, score, remainder, len(phrase.split())))

    if not matched:
        log.debug("לא נמצאה התאמה לאף פקודה בתוך: %s", words)
        return None, None

    # **תיקון באג כפול, בשני כיוונים מנוגדים**:
    #
    # כיוון 1 (התיקון המקורי): ביטוי גנרי קצר ("פתח") לא אמור לנצח
    # ביטוי ספציפי וארוך יותר ("פתח קלוד קוד") רק כי שיבוש תמלול הוריד
    # קצת את הציון של הארוך - למשל "פתח קלוד קוב" עדיין אמור לפתוח את
    # Claude Code, לא לנסות לפתוח אפליקציה בשם "קלוד קוב".
    #
    # כיוון 2 (באג חדש שנחשף על ידי התיקון הראשון): "העדפה מוחלטת
    # לביטוי הארוך ביותר שעובר את הסף" שברה מקרה אחר - "דאוס תחפש
    # בגוגל איך עושים עוגה" קיבל התאמה *מקרית* וחלשה יחסית (0.74, בקושי
    # מעל סף 0.72) לביטוי "תחפש לי בגוגל" (3 מילים) - וזו "ניצחה" את
    # ההתאמה המושלמת (1.00!) ל"תחפש בגוגל" (2 מילים) רק בגלל האורך,
    # מה שבלע את המילה "איך" לתוך הביטוי במקום לתוך הארגומנט.
    #
    # הפתרון הנכון: מעדיפים ביטוי ארוך יותר *רק* כשהציון שלו קרוב
    # מספיק לציון הכי טוב שנמצא בכלל (בתוך _LENGTH_PREFERENCE_MARGIN) -
    # כלומר "כמעט אותה רמת ביטחון, אז עדיף להיות ספציפי". אם ביטוי ארוך
    # יותר "התאים" רק בקושי ובציון נמוך משמעותית מהכי טוב שנמצא - זו
    # כנראה התאמה מקרית, לא הכוונה האמיתית, ולא נותנים לו לנצח.
    best_score = max(m[1] for m in matched)
    close_enough = [m for m in matched if best_score - m[1] <= _LENGTH_PREFERENCE_MARGIN]
    close_enough.sort(key=lambda m: (m[3], m[1]), reverse=True)  # אורך קודם, אז ציון
    command_id, score, remainder, _ = close_enough[0]
    argument = " ".join(remainder).strip()
    log.debug("פקודה מזוהה: %s (ציון=%.2f, ארגומנט='%s')", command_id, score, argument)
    return command_id, argument


def execute_command(command_id: str, argument: str, config: dict,
                     logger: logging.Logger = None) -> bool:
    """מבצע פקודה חד-פעמית בפועל. מחזיר True/False להצלחה. תמיד רץ
    ב-thread הראשי (מחובר דרך Bridge ב-main.py), כי חלק מהפעולות
    (פתיחת אפליקציות, לחיצות מקלדת) עדיפות לא לרוץ מת'רד הרקע."""
    log = logger or _log

    handlers = {
        "claude_code": _open_claude_code,
        "open_app": _open_app,
        "google_search": _google_search,
        "open_chat": _open_chat,
        "screenshot": _screenshot,
        "enter": _press_enter,
        "media_toggle": _toggle_media,
        "youtube_search": _youtube_search,
        "run_script": _run_script,
        "google_image_search": _google_image_search,
        "translate": _translate,
        "ram_clean": _ram_clean,
        "delete_line": _delete_line,
        "download_video": _download_video,
        "lock_pc": _lock_pc,
        "open_site": _open_site,
        "shutdown_computer": _shutdown_computer,
        # "shutdown_app" כן רשומה כאן (בניגוד להערה הישנה) - היא לא
        # מגיעה לכאן דרך הנתיב הרגיל (לא ב-ONE_SHOT_COMMANDS), אבל
        # speech/wake_word.py קורא ל-self.on_command("shutdown_app", ...)
        # אחרי שהצליל מסתיים (ראו שם), כדי לחצות בבטחה ל-thread הראשי
        # דרך אותו מנגנון Bridge/Signals קיים - צריך handler רשום כאן
        # כדי שזה לא ייכשל עם "no handler".
        "shutdown_app": shutdown_app,
    }
    handler = handlers.get(command_id)
    if handler is None:
        log.warning("אין handler לפקודה לא מוכרת: %s", command_id)
        return False

    try:
        return handler(argument, config, log)
    except Exception:
        log.exception("שגיאה בביצוע הפקודה '%s'", command_id)
        return False


# ---------------------------------------------------------------------- #
# מימוש הפקודות
# ---------------------------------------------------------------------- #

def _open_app(argument: str, config: dict, log: logging.Logger) -> bool:
    """"דאוס פתח <שם>" - פותח אפליקציה מקומית מוגדרת (config["apps"]),
    ואם אין התאמה טובה שם - נופל אוטומטית לחיפוש בין האתרים המוגדרים
    (get_sites, אותה רשימה ששרתת "דאוס פתח אתר") ופותח אותם בדפדפן.
    זה מאחד את "פתח X" ל"פתח כל דבר" אחד, כך שגם "דאוס פתח פייסבוק"
    (בלי המילה "אתר") עובד - לא רק "דאוס פתח אתר פייסבוק" המפורש.

    שימו לב: פתיחת אתר משתמשת ב-webbrowser.open() בדיוק כמו
    _google_search - הפעולה הפשוטה והאמינה ביותר בקוד הזה - ולא
    בלוגיקה נפרדת/שברירית יותר."""
    threshold = config.get("commands", {}).get(
        "command_fuzzy_threshold", config.get("fuzzy_threshold", 0.72)
    )

    apps = config.get("apps", {})
    best_app_name, best_app_score = None, 0.0
    for name in apps:
        score = similarity_loose(argument, name)
        if score > best_app_score:
            best_app_name, best_app_score = name, score

    sites = get_sites(config)
    best_site_name, best_site_score = None, 0.0
    for name in sites:
        score = similarity_loose(argument, name)
        if score > best_site_score:
            best_site_name, best_site_score = name, score

    # בוחרים בין ההתאמה הכי טובה מבין אפליקציות מקומיות לבין אתרים -
    # מי שקיבל ציון גבוה יותר (מעל הסף) מנצח. אם שתיהן מתחת לסף - נכשל.
    if best_app_score >= threshold and best_app_score >= best_site_score:
        path = apps[best_app_name]
        try:
            os.startfile(path)
            log.info("נפתחה אפליקציה '%s' (%s)", best_app_name, path)
            return True
        except Exception:
            log.exception("נכשל בפתיחת האפליקציה '%s' בנתיב %s", best_app_name, path)
            return False

    if best_site_score >= threshold:
        url = sites[best_site_name]
        try:
            _open_url_and_focus(url, config, log)
            log.info("נפתח אתר '%s' (%s) - לא נמצאה אפליקציה מקומית תואמת יותר", best_site_name, url)
            return True
        except Exception:
            log.exception("נכשל בפתיחת האתר '%s' בכתובת %s", best_site_name, url)
            return False

    log.warning(
        "לא נמצאה אפליקציה או אתר תואמים ל-'%s' (ציון אפליקציה=%.2f, ציון אתר=%.2f)",
        argument, best_app_score, best_site_score,
    )
    return False


def _google_search(argument: str, config: dict, log: logging.Logger) -> bool:
    query = argument.strip()
    if not query:
        log.warning("פקודת חיפוש בגוגל בלי מילות חיפוש - מדלג")
        return False
    url = "https://www.google.com/search?q=" + urllib.parse.quote(query)
    try:
        _open_url_and_focus(url, config, log)
        log.info("נפתח חיפוש גוגל: '%s'", query)
        return True
    except Exception:
        log.exception("נכשל בפתיחת חיפוש גוגל")
        return False


def _youtube_search(argument: str, config: dict, log: logging.Logger) -> bool:
    """פקודת "דאוס חפש ביוטיוב <שם>" - מחפש ביוטיוב ומפעיל את התוצאה
    הראשונה. המימוש עצמו נמצא ב-ai_engine/youtube_search.py."""
    from ai_engine.youtube_search import search_and_play_first
    return search_and_play_first(argument, config, log)


def _open_chat(argument: str, config: dict, log: logging.Logger) -> bool:
    from ai_engine.launcher import launch_and_activate
    launch_and_activate(config)
    return True


def _screenshot(argument: str, config: dict, log: logging.Logger) -> bool:
    if not _HAS_KEYBOARD:
        log.warning("חבילת keyboard לא מותקנת - לא ניתן לצלם מסך")
        return False
    try:
        # Win+PrintScreen - שומר צילום מסך אוטומטית לתיקיית
        # Pictures\Screenshots ב-Windows, בלי להזדקק לכלי חיצוני.
        keyboard.send("windows+print screen")
        log.info("בוצע צילום מסך (Win+PrintScreen)")
        return True
    except Exception:
        log.exception("נכשל בצילום מסך")
        return False


def _press_enter(argument: str, config: dict, log: logging.Logger) -> bool:
    if not _HAS_KEYBOARD:
        log.warning("חבילת keyboard לא מותקנת - לא ניתן ללחוץ Enter")
        return False
    try:
        keyboard.send("enter")
        log.info("נלחץ Enter")
        return True
    except Exception:
        log.exception("נכשל בלחיצה על Enter")
        return False


def _toggle_media(argument: str, config: dict, log: logging.Logger) -> bool:
    """פקודת "דאוס מדיה" - שולח את מקש המדיה הגלובלי Play/Pause (בדיוק
    כמו כפתור play/pause פיזי במקלדת) - מפעיל או עוצר שיר/וידאו שמנגן
    כרגע באפליקציה כלשהי (ספוטיפיי, יוטיוב, כל נגן שתומך במקשי מדיה
    של Windows), בלי צורך למקד את החלון שלה קודם.

    **נבדק בפועל למה זה נכשל אצל משתמש**: הלוג הראה "נשלחה פקודת מדיה"
    (הצלחה!) אבל שום דבר לא קרה בפועל. הסיבה: keyboard.send("play/pause
    media") שולח קלט מבוסס Virtual-Key code - בדיוק אותה בעיה שכבר
    אותרה ותוקנה עבור alt+g (ראו ai_engine/launcher.py) - הקריאה
    "מצליחה" ברמת ה-API (לא זורקת שגיאה) גם כשהמערכת לא מגיבה בפועל.
    התיקון: שליחה דרך utils/win_input.send_media_key, עם קוד סריקה
    מורחב אמיתי (Scan Code, לא VK) - בדיוק כמו שמקלדת פיזית שולחת."""
    try:
        from utils.win_input import send_media_key
        if send_media_key("play_pause"):
            log.info("נשלחה פקודת מדיה (Play/Pause, scan code)")
            return True
        log.warning("נכשל בשליחת פקודת המדיה - קוד סריקה לא נמצא")
        return False
    except Exception:
        log.exception("נכשל בשליחת פקודת המדיה")
        return False


def _find_window_with_title_containing(substring: str):
    """מוצא חלון גלוי כלשהו שהכותרת שלו מכילה את substring (לא תלוי-
    רישיות). משמש לוודא *בפועל* ש-Claude Code עלה בהצלחה - נבדק
    בפועל שהכותרת/הטאב של החלון שמריץ אותו משתנים ל-"Claude Code" ברגע
    שהוא עולה, אז זו ראיה אמיתית להצלחה - לא רק "לא נזרקה שגיאה"."""
    try:
        import win32gui
    except ImportError:
        return None
    needle = substring.lower()
    matches = []

    def _enum_handler(hwnd, _ctx):
        if win32gui.IsWindowVisible(hwnd) and needle in win32gui.GetWindowText(hwnd).lower():
            matches.append(hwnd)

    try:
        win32gui.EnumWindows(_enum_handler, None)
    except Exception:
        return None
    return matches[0] if matches else None


def _open_claude_code(argument: str, config: dict, log: logging.Logger) -> bool:
    """פותח PowerShell חדש, מקליד 'claude' ולוחץ Enter כדי להפעיל
    את Claude Code.

    **נבדק בפועל למה זה נכשל אצל משתמש**: הלוג של PowerShell עצמו הראה
    "Loading personal and system profiles took 1543ms" - טעינת הפרופיל
    (כשיש למשל סביבת conda שנטענת אוטומטית, "(base)" בפרומפט) לוקחת
    יותר מ-1.5 שניות. גם: keyboard.write() מקליד תו-אחר-תו, שזורק
    תווים כשהחלון עדיין באמצע אתחול - זו הסיבה שבפועל הוקלד "de" במקום
    "claude". תוקן עם הדבקה אטומית דרך clipboard+Ctrl+V, ועם המתנה
    אדפטיבית (utils/proc_wait.wait_for_process_idle) במקום שינה קבועה.

    **ועדיין המשתמש דיווח על כשל מלא (בכלל לא נכתב) לפעמים** - ניסיון
    קודם למקד במפורש את "חלון ה-PowerShell" לפני ההדבקה התברר כמבוסס
    על הנחה שגויה: **ל-powershell.exe אין בכלל חלון משלו** (נבדק
    בפועל - MainWindowTitle תמיד ריק) - כשמותקן Windows Terminal
    כמארח ברירת המחדל (המצב הנפוץ ב-Windows 11), *הוא* הבעלים של
    החלון הנראה, לא powershell.exe - כך שחיפוש חלון לפי ה-PID של
    powershell.exe פשוט לא מוצא כלום, ואי אפשר לדעת מראש אם Windows
    Terminal פותח חלון חדש או רק טאב בחלון קיים.

    **הפתרון בגרסה הזו**: במקום לנחש/למקד מראש, מאמתים *בדיעבד* -
    אחרי שליחת ההדבקה, בודקים אם באמת הופיע חלון עם "Claude Code"
    בכותרת (סימן אמין שההפעלה הצליחה, נצפה בפועל). אם לא - שולחים שוב
    (ייתכן שהפעם הראשונה הגיעה לחלון הלא נכון/לפני שהיה מוכן), עם
    המתנה קצרה בין הניסיונות. זה עובד גם בלי לדעת בכלל איזה תהליך/
    חלון בפועל מארח את ה-PowerShell."""
    if not _HAS_KEYBOARD:
        log.warning("חבילת keyboard לא מותקנת - לא ניתן להקליד לתוך PowerShell")
        return False

    try:
        proc = subprocess.Popen(
            ["powershell.exe"],
            creationflags=subprocess.CREATE_NEW_CONSOLE,
        )
    except Exception:
        log.exception("נכשל בפתיחת PowerShell")
        return False

    from utils.proc_wait import wait_for_process_idle
    ready = wait_for_process_idle(proc.pid, log)
    if not ready:
        wait_sec = config.get("commands", {}).get("claude_code_wait_sec", 3.0)
        log.info(
            "לא הצלחתי לזהות בבירור מתי PowerShell סיים לטעון - נופל "
            "להמתנה קבועה של %.1f שניות", wait_sec,
        )
        time.sleep(wait_sec)

    def _send_claude():
        if _HAS_WIN32_CLIPBOARD:
            _paste_text_via_clipboard("claude")
        else:
            keyboard.write("claude")
        keyboard.send("enter")

    verify_wait_sec = 1.3
    max_attempts = 3
    try:
        for attempt in range(1, max_attempts + 1):
            _send_claude()
            log.info("נשלח 'claude' ל-PowerShell (ניסיון %d/%d)", attempt, max_attempts)
            time.sleep(verify_wait_sec)
            if _find_window_with_title_containing("Claude Code") is not None:
                log.info("אומת בפועל: חלון 'Claude Code' עלה בהצלחה")
                return True
            if attempt < max_attempts:
                log.warning(
                    "לא זוהה חלון 'Claude Code' אחרי ניסיון %d - שולח שוב "
                    "אחרי %.1f שניות", attempt, verify_wait_sec,
                )

        log.warning(
            "Claude Code לא זוהה אחרי %d ניסיונות - ייתכן שההקלדה לא "
            "הגיעה ליעד הנכון בכל זאת", max_attempts,
        )
        return False
    except Exception:
        log.exception("PowerShell נפתח אבל נכשל בהקלדת 'claude'")
        return False


def _paste_text_via_clipboard(text: str):
    """מעתיק את text ל-clipboard, שולח Ctrl+V, ואז משחזר את התוכן
    הקודם של ה-clipboard - הדבקה היא פעולה אטומית, ולכן עמידה הרבה
    יותר טוב מהקלדה תו-אחר-תו (keyboard.write) לחלון שעדיין באמצע
    אתחול/טעינה. ראו גם speech/wake_word.py::_paste_via_clipboard
    (אותה טכניקה בדיוק, לשימוש אחר - תמלול קולי)."""
    previous_text = None
    win32clipboard.OpenClipboard()
    try:
        try:
            previous_text = win32clipboard.GetClipboardData(win32con.CF_UNICODETEXT)
        except Exception:
            previous_text = None
        win32clipboard.EmptyClipboard()
        win32clipboard.SetClipboardData(win32con.CF_UNICODETEXT, text)
    finally:
        win32clipboard.CloseClipboard()

    keyboard.send("ctrl+v")
    time.sleep(0.05)

    win32clipboard.OpenClipboard()
    try:
        win32clipboard.EmptyClipboard()
        if previous_text is not None:
            win32clipboard.SetClipboardData(win32con.CF_UNICODETEXT, previous_text)
    finally:
        win32clipboard.CloseClipboard()


def _run_script(argument: str, config: dict, log: logging.Logger) -> bool:
    """פקודת "דאוס הפעל סקריפט <שם>" - מריץ סקריפט מוגדר מראש
    (config["scripts"], נערך דרך תפריט המגש "ניהול סקריפטים")."""
    scripts = config.get("scripts", {})
    if not scripts:
        log.warning("לא הוגדרו סקריפטים בקונפיג (config['scripts'] ריק) - "
                     "אין מה להפעיל עבור '%s'", argument)
        return False

    threshold = config.get("commands", {}).get(
        "command_fuzzy_threshold", config.get("fuzzy_threshold", 0.72)
    )
    best_name, best_score = None, 0.0
    for name in scripts:
        score = similarity_loose(argument, name)
        if score > best_score:
            best_name, best_score = name, score

    if best_name is None or best_score < threshold:
        log.warning("לא נמצא סקריפט תואם ל-'%s' (ציון הכי טוב=%.2f)",
                     argument, best_score)
        return False

    path = scripts[best_name]
    ext = os.path.splitext(path)[1].lower()
    try:
        if ext == ".py":
            subprocess.Popen(["python", path], creationflags=subprocess.CREATE_NEW_CONSOLE)
        elif ext == ".ps1":
            subprocess.Popen(
                ["powershell.exe", "-ExecutionPolicy", "Bypass", "-File", path],
                creationflags=subprocess.CREATE_NEW_CONSOLE,
            )
        else:
            # .exe / .bat / .cmd וכו' - הפעלה ישירה דרך המערכת
            os.startfile(path)
        log.info("הופעל סקריפט '%s' (%s)", best_name, path)
        return True
    except Exception:
        log.exception("נכשל בהפעלת הסקריפט '%s' בנתיב %s", best_name, path)
        return False


# ---------------------------------------------------------------------- #
# חיפוש תמונה בגוגל (צילום מסך + חיפוש הפוך)
# ---------------------------------------------------------------------- #

def _google_image_search(argument: str, config: dict, log: logging.Logger) -> bool:
    """"דאוס חפש תמונה בגוגל" - מצלם את המסך הראשי ושולח אותו לחיפוש
    תמונה הפוך (reverse image search) של גוגל, ופותח את התוצאות
    בדפדפן. לא דורש מפתח API (לגוגל אין API חינמי רשמי לזה) - מדובר
    באותו endpoint שגוגל עצמו משתמש בו לצורך העלאת תמונה דרך הדפדפן
    (google.com/searchbyimage/upload)."""
    from ai_engine.image_search import screenshot_and_search_google
    return screenshot_and_search_google(config, log)


# ---------------------------------------------------------------------- #
# תרגום לעברית + הקראה
# ---------------------------------------------------------------------- #

def _translate(argument: str, config: dict, log: logging.Logger) -> bool:
    """"דאוס תרגם <טקסט>" - מתרגם ארגומנט אם ניתן; אחרת מתרגם את הטקסט
    שמסומן כרגע במסך (העתקה אוטומטית), ואם אין בחירה - את מה שנמצא
    אחרון ב-clipboard. מציג את התרגום בבועה קטנה ליד סמן העכבר,
    ואם מוגדר - גם מקריא אותו בקול."""
    from ai_engine.translate import translate_and_show
    return translate_and_show(argument, config, log)


# ---------------------------------------------------------------------- #
# ניקוי זיכרון / RAM
# ---------------------------------------------------------------------- #

def _ram_clean(argument: str, config: dict, log: logging.Logger) -> bool:
    """"דאוס נקה זיכרון" / "נקה ראם" / "סגור הכל" - סוגר תהליכים כבדים
    ומנקה זיכרון, לפי ההגדרות שבתפריט המגש (החרגות/סגירת מצלמה).
    לעולם לא נוגע ברשת/Wi-Fi."""
    from ai_engine.ram_cleaner import clean_ram
    return clean_ram(config, log)


# ---------------------------------------------------------------------- #
# מחיקת שורה
# ---------------------------------------------------------------------- #

def _delete_line(argument: str, config: dict, log: logging.Logger) -> bool:
    """"דאוס מחק שורה" - מסמן מתחילת השורה עד מיקום הסמן (Shift+Home)
    ומוחק את הבחירה (Delete). פועל בכל שדה טקסט רגיל שבו המוקד נמצא."""
    if not _HAS_KEYBOARD:
        log.warning("חבילת keyboard לא מותקנת - לא ניתן למחוק שורה")
        return False
    try:
        keyboard.send("shift+home")
        time.sleep(0.03)
        keyboard.send("delete")
        log.info("נמחקה השורה (shift+home ואז delete)")
        return True
    except Exception:
        log.exception("נכשל במחיקת השורה")
        return False


# ---------------------------------------------------------------------- #
# הורדת סרטון יוטיוב מה-clipboard
# ---------------------------------------------------------------------- #

def _download_video(argument: str, config: dict, log: logging.Logger) -> bool:
    """"דאוס הורד סרטון" - מוריד את סרטון היוטיוב שהכתובת שלו נמצאת
    כרגע ב-clipboard, באיכות הטובה ביותר הזמינה, לתוך תיקיית Downloads."""
    from ai_engine.video_downloader import download_from_clipboard
    return download_from_clipboard(config, log)


# ---------------------------------------------------------------------- #
# נעילת מחשב
# ---------------------------------------------------------------------- #

def _lock_pc(argument: str, config: dict, log: logging.Logger) -> bool:
    """"דאוס נעל מחשב" / "תנעל את המחשב" - נועל את המסך (כמו Win+L)."""
    try:
        ctypes.windll.user32.LockWorkStation()
        log.info("המחשב ננעל")
        return True
    except Exception:
        log.exception("נכשל בנעילת המחשב")
        return False


# ---------------------------------------------------------------------- #
# פתיחת אתר (ברירת מחדל + אתרים מותאמים אישית)
# ---------------------------------------------------------------------- #

# אתרי ברירת מחדל: שם (כפי שנאמר בפקודה הקולית, וגם מוצג בתפריט המגש)
# -> כתובת. ניתן להוסיף/להסיר אתרים דרך תפריט המגש ("ניהול אתרים") -
# שינויים שם נשמרים לקונפיג (config["sites"]) ומאוחדים (union) עם אלה
# כאן, בדיוק כמו עם ביטויי הפקודות.
DEFAULT_SITES = {
    "שאזאם": "https://www.shazam.com/",
    "pdf": "https://www.ilovepdf.com/",
    "פייסבוק": "https://www.facebook.com/",
    "אינסטגרם": "https://www.instagram.com/",
    "טיקטוק": "https://www.tiktok.com/",
    "לינקדין": "https://www.linkedin.com/",
    "אימייל": "https://mail.google.com/",
}


def get_sites(config: dict) -> dict:
    """מחזיר את מילון האתרים המלא: ברירות המחדל (DEFAULT_SITES) ממוזגות
    עם מה שהמשתמש הגדיר/שינה דרך תפריט המגש (config["sites"]) - ערך
    של המשתמש לשם קיים תמיד גובר על ברירת המחדל."""
    merged = dict(DEFAULT_SITES)
    merged.update(config.get("sites", {}))
    return merged


def _open_site(argument: str, config: dict, log: logging.Logger) -> bool:
    """"דאוס פתח אתר <שם>" - פותח את האתר התואם ביותר מתוך get_sites()
    בדפדפן ברירת המחדל."""
    query = argument.strip()
    if not query:
        log.warning("פקודת 'פתח אתר' בלי שם אתר - מדלג")
        return False

    sites = get_sites(config)
    if not sites:
        log.warning("לא הוגדרו אתרים - אין מה לפתוח עבור '%s'", query)
        return False

    threshold = config.get("commands", {}).get(
        "command_fuzzy_threshold", config.get("fuzzy_threshold", 0.72)
    )
    best_name, best_score = None, 0.0
    for name in sites:
        score = similarity_loose(query, name)
        if score > best_score:
            best_name, best_score = name, score

    if best_name is None or best_score < threshold:
        log.warning("לא נמצא אתר תואם ל-'%s' (ציון הכי טוב=%.2f)", query, best_score)
        return False

    url = sites[best_name]
    try:
        _open_url_and_focus(url, config, log)
        log.info("נפתח אתר '%s' (%s)", best_name, url)
        return True
    except Exception:
        log.exception("נכשל בפתיחת האתר '%s' בכתובת %s", best_name, url)
        return False


# ---------------------------------------------------------------------- #
# כיבוי דאוס
# ---------------------------------------------------------------------- #

def shutdown_app(argument: str, config: dict, log: logging.Logger) -> bool:
    """"דאוס כיבוי" - סוגר את דאוס עצמו (יוצא מהתוכנה לגמרי, לא רק
    השתקה). קורא ל-QApplication.quit() - הניקוי בפועל (עצירת ה-thread
    שמאזין למיקרופון, שחרור מודל Whisper) קורה ב-main.py מיד אחרי
    ש-app.exec() חוזר, בדיוק כמו ביציאה דרך תפריט המגש.

    שימו לב: זו פונקציה ציבורית (בלי קו תחתון) בכוונה - speech/
    wake_word.py קורא לה *ישירות* (לא דרך execute_command/handlers
    למעלה), כדי להבטיח שהצליל "shutdown_app" יתנגן במלואו לפני
    שהיא נקראת (ראו command_id == "shutdown_app" ב-_handle_command)."""
    try:
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance()
        if app is None:
            log.warning("אין QApplication פעיל - לא ניתן לבצע כיבוי")
            return False
        log.info("פקודת כיבוי - דאוס נסגר")
        app.quit()
        return True
    except Exception:
        log.exception("נכשל בכיבוי דאוס")
        return False


# ---------------------------------------------------------------------- #
# כיבוי המחשב (עם טיימר) - שונה מ"כיבוי" (שסוגר רק את דאוס)
# ---------------------------------------------------------------------- #

def _shutdown_computer(argument: str, config: dict, log: logging.Logger) -> bool:
    """"דאוס כיבוי מחשב" - מכבה את **המחשב עצמו** (לא רק את דאוס) בעוד
    N דקות (ברירת מחדל: 10, ניתן לכיוונון דרך תפריט המגש - "טיימר
    כיבוי מחשב > קבע ברירת מחדל לכיבוי בקול..." -
    config["shutdown_timer"]["voice_default_minutes"])."""
    minutes = config.get("shutdown_timer", {}).get("voice_default_minutes", 10)
    try:
        minutes = max(1, int(minutes))
    except (TypeError, ValueError):
        minutes = 10

    seconds = minutes * 60
    try:
        subprocess.run(["shutdown", "/s", "/t", str(seconds)], check=False)
        log.info("נקבע כיבוי מחשב בעוד %d דקות (לפי פקודה קולית)", minutes)
        return True
    except Exception:
        log.exception("נכשל בקביעת כיבוי מחשב")
        return False
