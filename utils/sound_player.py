"""
ניגון צלילים קצרים עבור אירועים שונים (מעברי מצב, פקודות וכו'), עם
שליטה בעוצמת הקול (config["sounds"]["volume"], 0-100).

כל צליל מזוהה על ידי "שם אירוע" (event name) - הפונקציה מחפשת קובץ
תואם בתיקיית הצלילים (ברירת מחדל: assets/sounds, ניתן לשינוי דרך
config["sounds"]["dir"]) בכמה סיומות נפוצות (.wav, .mp3), ומנגנת אותו
ברקע (thread נפרד, לא חוסם את זרימת התוכנית).

חשוב: אם לא נמצא קובץ מתאים - זה בסדר גמור, בשקט: לא נזרקת שגיאה,
ולא נכתבת אזהרה חוזרת ללוג בכל פעם (רק פעם אחת לכל אירוע, ברמת DEBUG).
ככה אפשר להוסיף צלילים בהדרגה, בלי שהתוכנה "תתלונן" על אלה שעוד לא
הוספתם.

מיפוי שם-אירוע -> שם-קובץ נקבע בקונפיג תחת config["sounds"]["files"]
(למשל {"dictation_start": "start_listening"}) - כברירת מחדל שם
הקובץ זהה לשם האירוע.

עוצמת קול: config["sounds"]["volume"] (0-100, ברירת מחדל 100) - נשלט
מתפריט המגש ("עוצמת קול"). 0 = שקט לגמרי (הצליל אפילו לא מנוגן, בלי
"לבזבז" ניגון בעוצמה אפסית).

חשוב: שליטה אמיתית בעוצמת קול (ולא רק "מנגן/לא מנגן") דורשת pygame:
    pip install pygame-ce
בלעדיו עדיין אפשר לנגן צלילים (עם playsound או winsound), אבל תמיד
בעוצמה המקורית של הקובץ - עוצמת הקול בקונפיג תשפיע רק על "מושתק
לגמרי" (0) מול "מנוגן" (מעל 0), לא על דירוג ביניים.
"""

import logging
import os
import threading

_log = logging.getLogger("deus")
_SUPPORTED_EXTS = (".wav", ".mp3")

# מכיל שמות אירועים שכבר דווח עבורם שאין קובץ - כדי לא להציף את הלוג
# עם אותה אזהרה בכל פעם שהאירוע קורה (למשל בכל צילום מסך).
_missing_logged = set()

# pygame.mixer הוא הדרך היחידה כאן לשלוט בעוצמת קול בפועל (set_volume).
# האתחול עלול להיכשל אם אין התקן שמע זמין - לא קריטי, פשוט נופלים
# חזרה לניגון בלי שליטת עוצמה (ראו _play_file).
try:
    import pygame

    pygame.mixer.init()
    _HAS_PYGAME = True
except Exception:
    _HAS_PYGAME = False
    _log.debug(
        "pygame לא זמין/נכשל באתחול - צלילים עדיין ינוגנו, אבל בלי "
        "שליטה אמיתית בעוצמת קול. כדי לאפשר זאת: pip install pygame-ce"
    )


def _resolve_sound_path(event_name: str, config: dict):
    sounds_cfg = config.get("sounds", {})
    if not sounds_cfg.get("enabled", True):
        return None

    sounds_dir = sounds_cfg.get("dir", "assets/sounds")
    base_dir = config.get("_base_dir", "")
    sounds_dir_abs = sounds_dir if os.path.isabs(sounds_dir) else os.path.join(base_dir, sounds_dir)

    filename = sounds_cfg.get("files", {}).get(event_name, event_name)

    root, ext = os.path.splitext(filename)
    if ext.lower() in _SUPPORTED_EXTS:
        candidates = [os.path.join(sounds_dir_abs, filename)]
    else:
        candidates = [os.path.join(sounds_dir_abs, filename + e) for e in _SUPPORTED_EXTS]

    for path in candidates:
        if os.path.exists(path):
            return path
    return None


def _play_file(path, volume_ratio):
    """מנגן את קובץ הצליל בפועל. נקראת כבר מתוך thread נפרד (ראו
    play_event_sound), כך שחסימה כאן היא בסדר גמור - היא לא חוסמת את
    שאר התוכנה."""
    if _HAS_PYGAME:
        try:
            sound = pygame.mixer.Sound(path)
            sound.set_volume(volume_ratio)
            sound.play()
            return
        except Exception:
            _log.exception("pygame נכשל בניגון %s", path)

    try:
        from playsound import playsound
        playsound(path)
        return
    except ImportError:
        pass
    except Exception:
        _log.exception("playsound נכשל בניגון %s", path)

    if path.lower().endswith(".wav"):
        try:
            import winsound
            winsound.PlaySound(path, winsound.SND_FILENAME)
        except Exception:
            _log.exception("winsound נכשל בניגון %s", path)
        return

    _log.debug(
        "אין נגן זמין להשמעת קובץ שאינו wav (%s) - כדי לנגן mp3 "
        "התקינו: pip install pygame-ce (מומלץ, כולל שליטת עוצמה) או "
        "pip install playsound", path
    )


def play_event_sound(event_name: str, config: dict, logger: logging.Logger = None):
    """מנגן ברקע (לא חוסם) את קובץ הצליל המתאים לאירוע event_name,
    בעוצמת הקול המוגדרת (config["sounds"]["volume"], 0-100), אם הוגדר
    קובץ כזה ונמצא בפועל בדיסק. שקט לגמרי אם לא נמצא קובץ, או אם
    עוצמת הקול היא 0."""
    log = logger or _log

    sounds_cfg = config.get("sounds", {})
    volume_percent = sounds_cfg.get("volume", 100)
    try:
        volume_percent = float(volume_percent)
    except (TypeError, ValueError):
        volume_percent = 100.0
    volume_ratio = max(0.0, min(100.0, volume_percent)) / 100.0

    if volume_ratio <= 0:
        log.debug("עוצמת הקול של דאוס מוגדרת ל-0 - מדלג על ניגון האירוע '%s'", event_name)
        return

    try:
        path = _resolve_sound_path(event_name, config)
    except Exception:
        log.exception("שגיאה באיתור קובץ צליל עבור האירוע '%s'", event_name)    
        return

    if path is None:
        if event_name not in _missing_logged:
            _missing_logged.add(event_name)
            log.debug(
                "לא נמצא קובץ צליל עבור האירוע '%s' - מדלג (הוסיפו קובץ "
                "assets/sounds/%s.wav או .mp3 אם תרצו צליל כאן)",
                event_name, event_name,
            )
        return

    threading.Thread(target=_play_file, args=(path, volume_ratio), daemon=True).start()


def get_event_sound_duration(event_name: str, config: dict, logger: logging.Logger = None):
    """מחזיר את אורך קובץ הצליל של event_name בשניות (float), או None
    אם לא ניתן לגלות (הקובץ לא נמצא, או pygame לא מותקן - זו הדרך
    היחידה כאן לקרוא אורך קובץ בלי לנגן אותו קודם). משמש למשל כדי
    לדעת כמה זמן להשאיר את אנימציית "מדבר" על המסך (ראו
    speech/wake_word.py:_announce)."""
    if not _HAS_PYGAME:
        return None
    try:
        path = _resolve_sound_path(event_name, config)
        if path is None:
            return None
        return pygame.mixer.Sound(path).get_length()
    except Exception:
        return None
