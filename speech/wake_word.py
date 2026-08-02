"""
מאזין רקע שמקליט מהמיקרופון בצ'אנקים קצרים, מתמלל אותם עם faster-whisper
(רץ על CPU או GPU), ובודק אם נאמרה מילת ההפעלה "Deus".

הארכיטקטורה: "דאוס" הוא רק *מילת מפתח*. הוא לא מבצע שום פעולה בעצמו -
הוא רק אומר לתוכנה "תסתכל על המילים אחרי זה ותבדוק אם זו פקודה מוכרת".
אם המילים אחרי "דאוס" תואמות אחת הפקודות המוגדרות ב-config["commands"]
(למשל "פתח את X", "חפש בגוגל X", "תמלל", "צלם מסך", "חפש ביוטיוב X",
"הפעל סקריפט X", "תלמד מאקרו X", "תפעיל מקרו X" וכו') - הפקודה מבוצעת
(דרך ai_engine/commands.py, או ישירות כאן עבור מצבים מתמשכים כמו
תמלול/מאקרו). אם לא - לא קורה כלום, בכוונה.

מצבים מתמשכים (לא "חד-פעמיים"):
  - תמלול (dictation): "דאוס תמלל" מתחיל הקלדה אוטומטית של כל מה
    שנאמר, עד "דאוס עצור".
  - לימוד מאקרו (macro learn): "דאוס תלמד מאקרו <שם>" מתחיל להקליט
    אירועי מקלדת/עכבר אמיתיים (ai_engine/macros.py), עד "דאוס עצור" -
    ואז נשמר תחת <שם> ואפשר להפעיל אותו עם "דאוס תפעיל מקרו <שם>".
    "דאוס עצור" הוא כפתור "עצור" גנרי: עוצר תמלול אם הוא פעיל, אחרת
    עוצר לימוד מאקרו אם הוא פעיל, אחרת עוצר הפעלת מאקרו אם היא רצה.

שחרור/טעינת מודל Whisper מה-GPU בהשתקה: כדי לפנות VRAM לתוכנות אחרות
כשלא צריך להאזין (config["whisper_unload_on_mute"]), המודל משוחרר
מהזיכרון בכל השתקה (Ctrl+Alt+D או מתפריט המגש), ונטען מחדש אוטומטית
בביטול ההשתקה. הטעינה/שחרור מתבצעים ב-thread נפרד כדי לא לחסום.

צלילים: על כל מעבר מצב משמעותי (תחילת/סיום תמלול, השתקה/ביטול השתקה,
צילום מסך, טעינת/שחרור מודל, לימוד/הפעלת מאקרו, חיפוש יוטיוב, הרצת
סקריפט) מנוגן צליל מתאים אם קיים קובץ כזה בתיקיית assets/sounds
(ראו utils/sound_player.py) - ואם לא, לא קורה כלום, בלי שגיאה.

כוונון בין דיוק למהירות:
  - chunk_duration_sec קצר יותר (למשל 2) => תשובה מהירה יותר אחרי הפקודה
    הקולית, אבל פחות הקשר קולי לכל תמלול בודד.
  - whisper_model_size גדול יותר ("base" ולא "tiny") => פחות false-positive
    (פחות מקרים שמילה אחרת מתומללת בטעות כ"דאוס"), במחיר זמן תמלול קצת
    יותר ארוך - עדיין ריצה סבירה על CPU עם compute_type="int8".
  - fuzzy_threshold גבוה יותר (למשל 0.78 ולא 0.72) => פחות false-positive,
    במחיר סיכוי מעט גבוה יותר לפספס הפעלה אמיתית אם המבטא לא ברור.
  ההגנה מפני "לחיצה כפולה" על קיצור ההפעלה נמצאת ב-ai_engine/launcher.py
  (cooldown), ולא כאן - כדי לא להוסיף השהיה מיותרת לפני שהזיהוי עצמו קורה.

יציבות ויזואלית (idle/listening "מהבהב"):
  סף האנרגיה (RMS) שמחליט אם צ'אנק "יש בו דיבור" הוא רגיש יחסית, ורעש
  רקע רגיל (מאוורר, מזגן, רמקולים) יכול לחצות אותו כל 2 שניות ולגרום
  למעברי מצב מיותרים idle<->listening (וכל מעבר מצב מפעיל מחדש את
  האנימציה מהפריים הראשון + מקפיץ את החלון קדימה) - מה שנראה כמו
  "הבהוב"/"ריסטארט", למרות שהתוכנה יציבה לגמרי. כדי לצמצם את זה:
    1. אפשר לכוון את סף הרעש עצמו דרך config["silence_rms_threshold"].
    2. יש כאן "השהיה" (hysteresis): חוזרים ל-idle רק אחרי כמה צ'אנקים
       רצופים של שקט ולא מיד אחרי הצ'אנק הראשון - כדי שרעש רגעי בודד
       לא יגרום להבהוב events.

שימוש (מתוך main.py):

    detector = WakeWordDetector(config, logger, on_command=...,
                                 on_state_change=..., on_error=...,
                                 on_macro_saved=...)
    detector.start()
    ...
    detector.toggle_mute()   # השתקה/הפעלה של ההאזנה בלי לסגור את התהליך
    ...
    detector.stop()
"""

import logging
import os

# תיקון באג נפוץ ב-Windows: huggingface_hub (שאיתו faster-whisper מוריד
# מודלים) שומר הורדות כ"בלוקים" (blobs) בתיקיית המטמון, ויוצר קובץ
# בשם "model.bin" בתוך snapshots/<revision>/ שהוא בעצם **סימלינק**
# (symlink) לאותו בלוב - לא עותק אמיתי. יצירת סימלינק ב-Windows
# דורשת הרשאות מנהל או "מצב מפתחים" (Developer Mode) מופעל
# (הגדרות > עדכון ואבטחה > למפתחים) - בלי זה, יצירת הסימלינק נכשלת
# בשקט, וההורדה "מצליחה" (הבלוב עצמו כן יורד) אבל "model.bin" בפועל
# חסר/שבור - מה שגורם בדיוק לשגיאה
# "RuntimeError: Unable to open file 'model.bin' in model '...'" (גם
# למודל הרגיל וגם לנפילה חזרה ל-CPU, כי שניהם משתמשים באותו מנגנון).
# הפתרון: HF_HUB_DISABLE_SYMLINKS=1 גורם ל-huggingface_hub פשוט
# *להעתיק* את הקובץ במקום לנסות ליצור סימלינק - עובד תמיד, בלי
# צורך בהרשאות מיוחדות או שינוי הגדרות Windows. חייב להיות מוגדר
# *לפני* שההורדה בפועל קורית (בתוך _load_model למטה) - ולכן מוגדר
# כאן, ממש בתחילת הקובץ, לפני ה-import של faster_whisper.
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS", "1")
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")

import subprocess
import queue
import threading
import time

import numpy as np
import sounddevice as sd
from faster_whisper import WhisperModel

from speech.fuzzy_match import locate_wake_word, similarity
from ai_engine.commands import parse_command, ONE_SHOT_COMMANDS, ARGUMENT_REQUIRED_COMMANDS
from ai_engine.macros import MacroRecorder, MacroPlayer
from ai_engine.audio_recorder import AudioRecorder
from ai_engine.clicker import ClickerLoop
from ai_engine.screen_recorder import ScreenRecorder
from utils.sound_player import play_event_sound, get_event_sound_duration

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

# סף אנרגיה פשוט לזיהוי אם יש בכלל דיבור בצ'אנק (מונע להריץ את המודל
# הכבד יותר על שקט מוחלט - חוסך משאבים). ניתן לשינוי דרך הקונפיג
# ("silence_rms_threshold") בלי לגעת בקוד.
_SILENCE_RMS_THRESHOLD = 0.01

# כמה צ'אנקים רצופים של "שקט" צריך לפני שחוזרים ויזואלית ל-idle -
# מונע הבהוב מיותר של האנימציה מרעש רקע רגעי.
_SILENCE_CHUNKS_BEFORE_IDLE = 2

# משך ברירת מחדל (שניות) שבו מוצגת אנימציית "talking" (deus_talking.gif)
# כשלא הצלחנו לגלות את האורך האמיתי של קובץ הצליל (pygame לא מותקן,
# או שהקובץ לא נמצא) - ראו _announce() ו-utils/sound_player.get_event_sound_duration.
_DEFAULT_TALKING_ANIMATION_SEC = 1.2

# שם אירוע הצליל שמתנגן בכל זיהוי בפועל של מילת ההפעלה "דאוס" (ה-"blip") -
# זה יוצא דופן: הוא לא מפעיל את אנימציית "talking" כמו שאר הצלילים
# (ראו _announce), הוא רק "פינג" קצר שמאשר שהמילה נקלטה.
_WAKE_WORD_SOUND_EVENT = "wake_word"

# מיפוי command_id -> שם אירוע צליל (= שם קובץ ברירת מחדל תחת
# assets/sounds/, ראו utils/sound_player.py), עבור *כל* הפקודות
# "חד-פעמיות" - כדי שלכל פקודה תהיה אופציה לקובץ שמע/הקראה משלה
# (למשל דאוס "אומר" בקול "מנקה זיכרון" כשמריצים "דאוס נקה זיכרון").
# אם אין קובץ בשם המתאים - שקט לגמרי, בלי שגיאה (ראו
# utils/sound_player.py). אפשר לשנות את שם הקובץ הנדרש דרך
# config["sounds"]["files"][event_name] בלי לגעת בקוד.
_COMMAND_SOUND_EVENTS = {
    "claude_code": "claude_code",
    "open_app": "open_app",
    "google_search": "google_search",
    "open_chat": "open_chat",
    "screenshot": "screenshot",
    "enter": "enter",
    "media_toggle": "media_toggle",
    "youtube_search": "youtube_search",
    "run_script": "script_run",
    "google_image_search": "image_search",
    "translate": "translate",
    "ram_clean": "ram_clean",
    "delete_line": "delete_line",
    "download_video": "download_video",
    "lock_pc": "lock_pc",
    "open_site": "open_site",
    "shutdown_app": "shutdown_app",
    # שם אירוע משותף עם ה"הכרזה" שמתפריט המגש (ראו main.py:
    # handle_shutdown_timer_requested / announce_external) - כך שאותו
    # קובץ צליל ("טיימר כיבוי מופעל") מתנגן בין אם הטיימר נקבע בקול
    # ("דאוס כיבוי מחשב") ובין אם דרך התפריט.
    "shutdown_computer": "shutdown_timer_set",
}

# פקודות שבהן הצליל המלווה הוא "רעש פעולה/עיבוד" ולא "דיבור" של דאוס
# (למשל צליל צילום מסך, או צליל שרק מלווה הרצת סקריפט/הורדה/תרגום/
# ניקוי זיכרון) - אלה משאירות את deus_thinking.gif מוצג לאורך כל
# הפעולה, במקום לעבור ל-deus_talking.gif כמו שאר ההכרזות (ראו
# _announce/active_state).
_THINKING_GIF_COMMANDS = {
    "screenshot", "run_script", "translate", "ram_clean", "download_video",
}


def _resample_audio(audio: np.ndarray, orig_rate: int, target_rate: int) -> np.ndarray:
    """
    ממיר את קצב הדגימה של אודיו מונו (1D float32) מ-orig_rate ל-target_rate,
    בעזרת אינטרפולציה ליניארית פשוטה (ללא תלות בחבילות נוספות כמו scipy).

    למה זה נחוץ: במצב WASAPI Shared חייבים להקליט בקצב הדגימה ה"טבעי"
    של ההתקן (לרוב 48000Hz) - בקשת קצב שרירותי כמו 16000Hz ישירות
    מהדרייבר יכולה להיכשל, או לגרום ל-PortAudio ליפול חזרה למצב/host API
    אחר שנועל את המיקרופון בבלעדיות (בדיוק הבעיה שבגללה אפליקציות אחרות
    כמו Chrome לא הצליחו לשמוע במקביל). לכן מקליטים בקצב הטבעי, וממירים
    בתוכנה ל-16000Hz רק לפני שמעבירים ל-Whisper (שדורש דווקא 16kHz).

    האינטרפולציה הליניארית לא באיכות אולטימטיבית (כמו resampler עם
    פילטר anti-aliasing מלא), אבל מספיקה בהחלט לצורך זיהוי דיבור/מילת
    הפעלה - וזה משתלם בהרבה על פני התלות בחבילה נוספת.
    """
    if orig_rate == target_rate or len(audio) == 0:
        return audio

    duration = len(audio) / orig_rate
    target_len = max(1, int(round(duration * target_rate)))
    orig_indices = np.linspace(0, len(audio) - 1, num=len(audio))
    target_indices = np.linspace(0, len(audio) - 1, num=target_len)
    return np.interp(target_indices, orig_indices, audio).astype(np.float32)


_MIN_STRONG_GPU_VRAM_MB = 4096


def _has_strong_gpu(log: logging.Logger = None) -> bool:
    """בודק אם יש כרטיס מסך NVIDIA עם לפחות _MIN_STRONG_GPU_VRAM_MB
    (4GB) VRAM, כדי להחליט אם להתחיל אוטומטית במצב חסכון (ראו __init__).
    משתמש ב-nvidia-smi (מגיע עם כל דרייבר NVIDIA - לא דורש שום תלות
    פייתונית נוספת כמו torch/pynvml). אם nvidia-smi לא נמצא (אין
    NVIDIA/אין דרייבר) - מניחים 'לא, אין GPU חזק' ומתחילים במצב חסכון,
    בלי לקרוס בשום מקרה."""
    log = log or logging.getLogger("deus")
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.total", "--format=csv,noheader,nounits"],
            capture_output=True, timeout=8, check=False, text=True,
        )
        if result.returncode != 0 or not result.stdout.strip():
            return False

        # אם יש כמה כרטיסים - מספיק שאחד מהם חזק מספיק.
        vram_values = []
        for line in result.stdout.strip().splitlines():
            try:
                vram_values.append(int(line.strip()))
            except ValueError:
                continue

        return any(v >= _MIN_STRONG_GPU_VRAM_MB for v in vram_values)
    except FileNotFoundError:
        return False  # nvidia-smi לא קיים בכלל - כנראה אין NVIDIA GPU
    except Exception:
        log.debug("בדיקת GPU/VRAM נכשלה - מניח שאין GPU חזק", exc_info=True)
        return False


def _replace_symlinks_with_copies(model_folder: str, log: logging.Logger) -> int:
    """סורק את snapshots/ של תיקיית מודל *שכבר קיימת מקומית* ומחליף כל
    סימלינק (תקין או שבור) בעותק אמיתי של הבלוב שהוא מצביע עליו. לא
    מורידה שום דבר מהרשת - פועלת רק על מה שכבר על הדיסק, ולכן זולה
    ומהירה (בניגוד למחיקה + הורדה מחדש). ראו את התיעוד המפורט על שני
    מצבי הכשל ב-_fix_broken_symlinks_in_cache למטה. מחזירה כמה קבצים
    תוקנו."""
    import shutil

    hub_dir = os.path.join(os.path.expanduser("~"), ".cache", "huggingface", "hub")
    snapshots_dir = os.path.join(hub_dir, model_folder, "snapshots")
    blobs_dir = os.path.join(hub_dir, model_folder, "blobs")

    if not os.path.isdir(snapshots_dir) or not os.path.isdir(blobs_dir):
        return 0

    fixed = 0
    for snapshot_hash in os.listdir(snapshots_dir):
        snap_path = os.path.join(snapshots_dir, snapshot_hash)
        if not os.path.isdir(snap_path):
            continue

        for fname in os.listdir(snap_path):
            fpath = os.path.join(snap_path, fname)

            # מקרה 1: סימלינק אמיתי ותקין (reparse point) - מחליפים
            # תמיד בעותק אמיתי של היעד, גם אם הוא "תקין" מבחינת
            # Windows/Python, כי ctranslate2 לא בהכרח מסוגל לפתוח אותו.
            #
            # os.readlink() (לא os.path.realpath()) בכוונה: realpath
            # צריך בפועל "לעבור דרך" (traverse) את הסימלינק כדי לפתור
            # אותו, וזה נכשל עם OSError [WinError 448] "untrusted mount
            # point" אם הסימלינק נוצר ע"י תהליך עם רמת הרשאות שונה
            # (למשל תהליך מורם/UAC) מזו של התהליך הנוכחי - מדיניות
            # אבטחה של Windows חוסמת מעבר דרך reparse point "לא מהימן"
            # כזה. readlink לעומת זאת רק *קורא* את מחרוזת היעד השמורה
            # במטא-דאטה של הסימלינק עצמו (בלי לגעת ביעד בכלל), ולכן לא
            # נחסם באותו אופן - ואז פשוט בונים את נתיב הבלוב בעצמנו.
            if os.path.islink(fpath):
                try:
                    try:
                        target = os.readlink(fpath)
                    except OSError:
                        target = None
                    if target and not os.path.isabs(target):
                        blob_path = os.path.normpath(
                            os.path.join(os.path.dirname(fpath), target)
                        )
                    elif target:
                        blob_path = target
                    else:
                        blob_path = os.path.realpath(fpath)
                    if os.path.isfile(blob_path):
                        os.unlink(fpath)
                        shutil.copy2(blob_path, fpath)
                        fixed += 1
                    else:
                        log.debug(
                            "סימלינק %s מצביע על בלוב שלא קיים: %s", fname, blob_path
                        )
                except Exception:
                    log.debug(
                        "נכשל להחליף סימלינק %s בעותק אמיתי", fname, exc_info=True
                    )
                continue

            if not os.path.isfile(fpath):
                continue

            try:
                fsize = os.path.getsize(fpath)
            except OSError:
                fsize = 0

            if fsize >= 1024:
                continue  # קובץ אמיתי (לא סימלינק ולא placeholder שבור)

            # קובץ קטן - מנסים לקרוא את תוכן הסימלינק
            blob_hash = None
            try:
                with open(fpath, "r", encoding="utf-8", errors="replace") as f:
                    content = f.read(500).strip()
                # התוכן הוא נתיב יחסי: ../../blobs/<sha256>
                # מחלצים את ה-sha256 (64 תווי hex)
                parts = content.replace("\\", "/").rstrip("/").split("/")
                for part in reversed(parts):
                    part = part.strip()
                    if len(part) == 64 and all(c in "0123456789abcdef" for c in part.lower()):
                        blob_hash = part
                        break
            except Exception:
                pass

            # מנסים למצוא ולהעתיק את הבלוב
            replaced = False
            if blob_hash:
                blob_path = os.path.join(blobs_dir, blob_hash)
                if os.path.isfile(blob_path):
                    try:
                        os.unlink(fpath)
                    except Exception:
                        pass
                    try:
                        shutil.copy2(blob_path, fpath)
                        fixed += 1
                        replaced = True
                    except Exception:
                        pass

            if not replaced:
                log.debug("לא נמצא בלוב מתאים לקובץ %s (hash=%s)", fname, blob_hash)

    return fixed


def _fix_broken_symlinks_in_cache(size: str, log: logging.Logger) -> str:
    """מוריד מודל מ-HuggingFace Hub (אם עדיין לא קיים מקומית) ואז מחליף
    כל סימלינק שנוצר תחתיו בעותק אמיתי, דרך _replace_symlinks_with_copies.

    שני מצבי כשל אפשריים ב-Windows, ושניהם מטופלים ב-
    _replace_symlinks_with_copies:

    1. יצירת הסימלינק נכשלת בשקט (בלי Developer Mode/הרשאות) - נשאר
       קובץ טקסט זעיר (המכיל נתיב יחסי לבלוב) במקום.
    2. יצירת הסימלינק *מצליחה* (reparse point אמיתי ותקין, ש-Python
       פותח ללא בעיה) - אבל ctranslate2 (C++) בכל זאת נכשל לפתוח אותו
       (RuntimeError: Unable to open file 'model.bin'). זו הייתה בדיוק
       הבעיה שלא טופלה קודם: המודל הורד בהצלחה, הסימלינק נוצר בהצלחה
       ומצביע על בלוב שלם, ובכל זאת ctranslate2 קרס עם
       "Unable to open file"."""
    from faster_whisper.utils import download_model

    hub_dir = os.path.join(os.path.expanduser("~"), ".cache", "huggingface", "hub")
    model_folder = "models--" + size.replace("/", "--")

    # שימוש ב-download_model של faster-whisper (לא ב-snapshot_download הגולמי
    # של huggingface_hub) מטעמי חיוניות: הוא (1) מתרגם כינויים כמו "medium"
    # ל-repo_id המלא (Systran/faster-whisper-medium) - snapshot_download הגולמי
    # שולח את "medium" כמו שהוא ומקבל 404 Repository Not Found; (2) מכבה את
    # פרוגרס-בר ה-tqdm (tqdm_class=disabled_tqdm) - בבנייה ללא קונסולה
    # (windowed exe) sys.stdout הוא None ו-tqdm הרגיל קורס עם
    # AttributeError: 'NoneType' object has no attribute 'write'.
    local_path = download_model(size, cache_dir=hub_dir)

    fixed = _replace_symlinks_with_copies(model_folder, log)
    if fixed > 0:
        log.info("תוקנו %d סימלינקים במודל '%s'", fixed, size)
    else:
        log.debug("לא נמצאו סימלינקים לתיקון במודל '%s'", size)

    return local_path


def _load_whisper_with_recovery(size: str, device: str, compute_type: str, log: logging.Logger):
    """טוען מודל Whisper עם auto-recovery לסימלינקים/מטמון פגום.

    זורם: מנסה לטעון רגיל (עם כמה ניסיונות חוזרים לפני שמוותרים - כדי
    לא לבלבל כשל פתיחה זמני עם מטמון באמת פגום). אם עדיין נכשל - קודם
    מנסה תיקון זול (בלי הורדה): להחליף סימלינקים קיימים בעותקים
    אמיתיים במקום, ולנסות לטעון שוב מיד (ראו _replace_symlinks_with_copies) -
    זה כבר מתקן את המקרה הנפוץ (סימלינק תקין שרק ctranslate2 לא מצליח
    לפתוח), בלי לבזבז דקות על מחיקה + הורדה חוזרת של קובץ מודל של
    ג'יגה-בייטים. רק אם זה לא הספיק - מנקה את התיקייה כליל, מוריד
    מחדש, מתקן, ואז טוען."""
    import time

    err_msg = ""
    for attempt in range(3):
        try:
            return WhisperModel(size, device=device, compute_type=compute_type)
        except RuntimeError as e:
            err_msg = str(e)
            if "Unable to open file" not in err_msg and "model.bin" not in err_msg:
                raise
            # לא בהכרח מטמון פגום - יכול להיות כשל פתיחה זמני (למשל
            # אנטי-וירוס שסורק exe שהותקן זה עתה, או GPU/driver שעוד
            # לא התאתחלו לגמרי מיד אחרי התקנה טרייה). מוחקים את כל
            # התיקייה (3GB+ להורדה מחדש) רק אחרי שגם ניסיונות חוזרים
            # עם השהיה נכשלו - לא על כשל בודד.
            if attempt < 2:
                log.debug(
                    "טעינת מודל Whisper '%s' נכשלה (ניסיון %d/3): %s - מנסה שוב בעוד 2 שניות לפני שמניחים שהמטמון פגום...",
                    size, attempt + 1, err_msg[:150],
                )
                time.sleep(2)

    # לפני שמוותרים על המטמון הקיים לגמרי (מחיקה + הורדה מחדש של
    # ג'יגה-בייטים) - מנסים תיקון זול: אולי הבלוב כבר שלם על הדיסק,
    # וכל הבעיה היא שהוא מוצג דרך סימלינק ש-ctranslate2 לא מצליח לפתוח.
    # החלפת הסימלינק בעותק אמיתי לא דורשת רשת בכלל, ולוקחת שניות.
    model_folder = "models--" + size.replace("/", "--")
    try:
        quick_fixed = _replace_symlinks_with_copies(model_folder, log)
    except Exception:
        quick_fixed = 0
    if quick_fixed > 0:
        log.info(
            "הוחלפו %d סימלינקים בעותקים אמיתיים (בלי הורדה מחדש) - מנסה לטעון שוב...",
            quick_fixed,
        )
        try:
            return WhisperModel(size, device=device, compute_type=compute_type)
        except RuntimeError as e2:
            err_msg = str(e2)
            log.warning(
                "עדיין נכשל אחרי תיקון סימלינקים מקומי (%s) - עובר למחיקה + הורדה מחדש מלאה...",
                err_msg[:150],
            )

    log.warning(
        "מודל Whisper '%s' פגום במטמון (%s). מנקה, מוריד מחדש ומתקן...",
        size, err_msg[:150],
    )

    import gc
    import shutil

    gc.collect()

    hub_dir = os.path.join(os.path.expanduser("~"), ".cache", "huggingface", "hub")
    model_cache_path = os.path.join(hub_dir, model_folder)

    if os.path.isdir(model_cache_path):
        try:
            shutil.rmtree(model_cache_path)
            log.info("נמחקה תיקיית מטמון פגומה: %s", model_cache_path)
        except Exception:
            backup = model_cache_path + ".corrupted_backup"
            try:
                if os.path.isdir(backup):
                    shutil.rmtree(backup)
            except Exception:
                pass
            try:
                os.rename(model_cache_path, backup)
                log.info("תיקיית מטמון פגומה הוזזה ל-%s", backup)
            except Exception:
                log.warning("לא הצליח לנקות תיקיית מטמון פגומה")

    try:
        local_path = _fix_broken_symlinks_in_cache(size, log)
    except Exception as dl_err:
        log.warning("תיקון סימלינקים נכשל (%s) - מנסה דרך faster-whisper...", dl_err)
        return WhisperModel(size, device=device, compute_type=compute_type)

    # מיד אחרי הורדה טרייה של קובץ מודל ענק (בייחוד model.bin - כמה
    # ג'יגה-בייט), ניסיון פתיחה ראשון עלול להיכשל זמנית - למשל אנטי-
    # וירוס שסורק קובץ חדש שרק נכתב, או handle של תהליך ההורדה שעוד
    # לא שוחרר לגמרי - למרות שהקובץ באמת תקין ושלם. לכן מנסים כמה
    # פעמים עם השהיה קצרה לפני שנכשלים סופית.
    last_err = None
    for attempt in range(3):
        try:
            return WhisperModel(local_path, device=device, compute_type=compute_type)
        except RuntimeError as retry_err:
            last_err = retry_err
            if "Unable to open file" not in str(retry_err) and "model.bin" not in str(retry_err):
                raise
            log.warning(
                "טעינת המודל שהורד כרגע נכשלה זמנית (ניסיון %d/3): %s - מנסה שוב בעוד 2 שניות...",
                attempt + 1, str(retry_err)[:150],
            )
            time.sleep(2)
    raise last_err


class WakeWordDetector:
    def __init__(self, config: dict, logger: logging.Logger = None,
                 on_command=None,
                 on_state_change=None, on_error=None,
                 on_macro_saved=None, on_critical_whisper_failure=None):
        self.config = config
        self.log = logger or logging.getLogger("deus")
        self.on_command = on_command or (lambda command_id, argument: None)
        self.on_state_change = on_state_change or (lambda state: None)
        self.on_error = on_error or (lambda msg: None)
        # נקרא כש"דאוס עצור" סוגר לימוד מאקרו: on_macro_saved(name, macro_dict)
        # - main.py אחראי לשמור את זה בקונפיג ולדיסק (ראו Bridge.macro_saved).
        self.on_macro_saved = on_macro_saved or (lambda name, macro: None)
        # נקרא רק כשהטעינה ה*ראשונית* (critical=True) של מודל Whisper
        # נכשלת סופית, אחרי כל הניסיונות החוזרים והנפילה ל-CPU - main.py
        # משתמש בזה כדי להפעיל מחדש את כל התוכנה פעם אחת (ראו שם), כי
        # נצפה אמפירית שתהליך *חדש* לגמרי מצליח לטעון את אותו קובץ מודל
        # בדיוק שתהליך זה נכשל בו שוב ושוב - כנראה נעילה/תנאי שקשור
        # לתהליך הנוכחי (או לרגע ההתקנה) ולא לקובץ עצמו.
        self.on_critical_whisper_failure = on_critical_whisper_failure or (lambda: None)

        self._running = False
        self._muted = False
        self._thread = None
        self._audio_q = queue.Queue()
        self._model = None
        self._model_lock = threading.RLock()
        self._consecutive_silence = 0
        self._dictation_active = False

        # מאקרו: הקלטה/הפעלה (ai_engine/macros.py)
        self._macro_recorder = MacroRecorder(self.log)
        self._macro_player = MacroPlayer(self.log)
        self._pending_macro_name = None

        # הקלטת שמע חופשית (ai_engine/audio_recorder.py) וקליקר
        # (ai_engine/clicker.py) - עוד שני "מצבים מתמשכים" שנפתחים
        # בפקודה קולית ונסגרים עם "דאוס עצור" הגנרי, בדיוק כמו מאקרו.
        self._audio_recorder = AudioRecorder(self.log)
        self._clicker = ClickerLoop(self.log)

        # הקלטת מסך (ai_engine/screen_recorder.py) - בכוונה **לא** נסגרת
        # עם "דאוס עצור" הגנרי (בשונה מהקלטת שמע/קליקר/מאקרו/תמלול) -
        # היא "טוגל" עצמאי (אותה פקודה שוב = עצירה), כדי שאפשר יהיה
        # להקליט מסך תוך כדי שימוש בפקודות אחרות שמשתמשות ב"עצור"
        # (למשל תמלול) בלי שההקלטה תיפסק בטעות.
        self._screen_recorder = ScreenRecorder(self.log)

        # מצב חסכון (Whisper small, CPU בלבד) - ראו set_economy_mode().
        # אם המשתמש סימן בתפריט המגש "הפעל במצב חסכון" - מתחילים ישר
        # במצב הזה גם בהפעלה החדשה (whisper_start_in_economy_mode).
        # בנוסף - **גם בלי שהמשתמש ביקש**: אם לא מזוהה כרטיס מסך NVIDIA
        # עם לפחות 4GB VRAM, מתחילים אוטומטית במצב חסכון - כי אין טעם
        # לנסות לטעון את המודל העברי הכבד (large-v3) על מחשב שממילא לא
        # יוכל להריץ אותו בצורה סבירה (ייתקע/יהיה איטי מאוד). אפשר
        # תמיד לצאת ידנית עם "דאוס תחזור" או Ctrl+Alt+P אם רוצים לנסות
        # בכל זאת (למשל אם ה-CPU בכל זאת מספיק חזק).
        self._economy_mode = self.config.get("whisper_start_in_economy_mode", False)
        if not self._economy_mode and not _has_strong_gpu(self.log):
            self.log.info(
                "לא זוהה כרטיס מסך NVIDIA עם לפחות 4GB VRAM - "
                "מתחיל אוטומטית במצב חסכון (Whisper קל יותר, CPU בלבד)"
            )
            self._economy_mode = True

        # "חלון המתנה לפקודה": כשנאמרה מילת ההפעלה בלי פקודה מוכרת
        # מיד אחריה באותו צ'אנק (למשל כי המשפט "דיוס ... פתח קלוד קוד"
        # נחתך בין שני צ'אנקים) - לא מוותרים מיד. בודקים גם את הצ'אנקים
        # הבאים (גם בלי חזרה על "דיוס") כמועמד לפקודה.
        #
        # שימו לב: זה נספר ב*מספר צ'אנקים*, לא בזמן שעון! זמן תמלול על
        # CPU יכול לקחת כמעט כמו אורך הצ'אנק עצמו (חוויתית: ~2 שניות
        # לתמלל 2 שניות אודיו) - חלון מבוסס-זמן (למשל "4 שניות") נאכל
        # ברובו על ידי זמן העיבוד עצמו ולא באמת נותן הזדמנות לצ'אנק
        # הבא. ספירת צ'אנקים חסינה לגמרי למהירות המחשב.
        self._awaiting_command_chunks_left = 0

        # "חלון המתנה לארגומנט": בשונה מ-_awaiting_command_chunks_left
        # (שממתין לזיהוי *איזו* פקודה נאמרה), זה משמש אחרי שכבר יודעים
        # בוודאות איזו פקודה נאמרה (למשל "google_search") אבל היא הגיעה
        # בלי ארגומנט (מילות החיפוש) - למשל כי המשתמש עצר לנשום בין
        # "דאוס חפש בגוגל" למילות החיפוש עצמן, שנחתכות לצ'אנק הבא.
        # כל עוד זה פעיל, כל צ'אנק דיבור הבא (גם בלי "דאוס") נלקח
        # במלואו כארגומנט לפקודה הממתינה - ראו ARGUMENT_REQUIRED_COMMANDS
        # ב-ai_engine/commands.py.
        self._pending_argument_command_id = None
        self._pending_argument_chunks_left = 0

        # תור הכרזות (ראו _announce): כשמגיעה הכרזה שנייה בזמן שהראשונה
        # עדיין "מדברת" (הצליל שלה עדיין מתנגן) - היא לא מתנגנת מיד
        # (מה שהיה גורם לשני צלילים לרוץ אחד על השני, ולפעמים לגרום
        # לאנימציה לחזור ל-idle לפי משך הצליל *הקצר* מבין השניים, תוך
        # כדי שהצליל הארוך יותר עדיין מתנגן בפועל) - אלא נכנסת לתור,
        # ומתנגנת רק אחרי שההכרזה הקודמת מסיימת (לפי המשך המוערך שלה).
        # כך "model_loading" ואז "model_loaded" מיד אחריו נשמעים ברצף
        # מלא, ואנימציית "talking" נשארת רציפה (בלי לאתחל את הגיף מחדש)
        # עד שכל התור מתרוקן.
        self._announce_queue = []
        self._announce_lock = threading.Lock()
        self._announce_busy = False
        self._talking_revert_timer = None

        self.sample_rate = config.get("sample_rate", 16000)
        self.chunk_duration = config.get("chunk_duration_sec", 3)
        self.wake_words = config.get("wake_words", ["דאוס", "deus"])
        self.threshold = config.get("fuzzy_threshold", 0.72)
        self.mic_index = config.get("mic_device_index")
        self.initial_prompt = config.get("whisper_initial_prompt") or None
        self.silence_rms_threshold = config.get(
            "silence_rms_threshold", _SILENCE_RMS_THRESHOLD
        )
        self.command_window_chunks = config.get("commands", {}).get(
            "command_window_chunks", 3
        )
        # חלון המתנה (בצ'אנקים) לארגומנט של פקודה שכבר זוהתה בלי אחד -
        # ברירת המחדל קצת יותר סלחנית מ-command_window_chunks כי כאן
        # המשתמש כבר באמצע משפט (למשל בין "חפש בגוגל" למילות החיפוש),
        # לא מתחיל פקודה חדשה מאפס.
        self.argument_window_chunks = config.get("commands", {}).get(
            "argument_window_chunks", max(self.command_window_chunks, 4)
        )

    # ------------------------------------------------------------------ #
    # ניהול חיים
    # ------------------------------------------------------------------ #

    def start(self):
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        self.log.info("WakeWordDetector: ת'רד הרקע הופעל")

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=2)
        self.log.info("WakeWordDetector: הופסק")

    def is_muted(self) -> bool:
        return self._muted

    def set_muted(self, muted: bool):
        self._muted = muted
        self._consecutive_silence = 0
        self._dictation_active = False
        self._awaiting_command_chunks_left = 0
        self._pending_argument_command_id = None
        self._pending_argument_chunks_left = 0
        if muted:
            # השתקה עוצרת גם מצבים מתמשכים פתוחים (הקלטה/קליקר) - עדיף
            # לשמור את מה שכבר הוקלט מאשר להשאיר הקלטה "תקועה" ברקע
            # בזמן שההאזנה כבויה ואין דרך לומר "דאוס עצור".
            if self._audio_recorder.is_recording():
                self._audio_recorder.stop()
            if self._clicker.is_running():
                self._clicker.stop()
            if self._screen_recorder.is_recording():
                self._screen_recorder.stop()
        if self._talking_revert_timer is not None:
            self._talking_revert_timer.cancel()
            self._talking_revert_timer = None
        with self._announce_lock:
            self._announce_queue.clear()
            self._announce_busy = False
        self.log.info("WakeWordDetector: %s", "מושתק (לא מאזין)" if muted else "מאזין שוב")
        # "shut1" הוא אנימציית מעבר חד-פעמית שרצה פעם אחת ואז עוברת
        # אוטומטית ל-"shut2" (נשאר קפוא שם עד ההשתקה מתבטלת) - הלוגיקה
        # של השרשור נמצאת ב-OverlayWindow (config["state_transitions"]).
        self.on_state_change("shut1" if muted else "idle")
        play_event_sound("mute" if muted else "unmute", self.config, self.log)

        # שחרור/טעינה מחדש של מודל Whisper (GPU/CPU) - כדי לפנות משאבים
        # (בעיקר VRAM) כשלא מאזינים בפועל. אפשר לכבות דרך הקונפיג
        # ("whisper_unload_on_mute": false) אם מעדיפים שהמודל ישאר טעון.
        if self.config.get("whisper_unload_on_mute", True):
            if muted:
                threading.Thread(target=self._unload_model, daemon=True).start()
            else:
                threading.Thread(target=self._reload_model_if_needed, daemon=True).start()

    def toggle_mute(self):
        self.set_muted(not self._muted)

    def _type_text(self, text: str):
        """מקליד טקסט בפועל בשורה שהסמן נמצא עליה (בכל אפליקציה שיש
        לה כרגע פוקוס) - בעזרת העתקה ל-clipboard + Ctrl+V (הדבקה
        אטומית), לא בעזרת keyboard.write() (הקלדה תו-אחר-תו). זו לא
        פעולת Qt ולכן בטוח לקרוא לה ישירות מת'רד הרקע בלי לעבור דרך
        ה-Bridge.

        למה לא keyboard.write(): התברר בפועל שההקלדה הסינתטית
        תו-אחר-תו "מפילה"/מכפילה תווים (בעיקר רווחים) כשהיא רצה מהר
        יותר משהאפליקציה שבפוקוס מספיקה לעבד keystroke events ברצף -
        זה בולט במיוחד עם טקסט עברי (דורש הזרקת Unicode, לא מיפוי מקש
        רגיל). זו הסיבה שהתמלול *המוקלד בפועל* נראה "לא מדויק" עם
        המון רווחים מיותרים, בעוד שהמחרוזת שהתקבלה מוויספר (ונרשמת
        ללוג) הייתה תקינה לגמרי מלכתחילה - שני ה"מקומות" שהמשתמש ראה
        בהם תמלול הם בעצם אותה מחרוזת בדיוק, רק שאחד מהם (ההקלדה)
        התעוות בדרך. הדבקה היא פעולה אטומית אחת - האפליקציה מקבלת את
        כל הטקסט בבת אחת, בלי תלות בקצב עיבוד ה-keystroke-ים."""
        if not text:
            return
        if _HAS_WIN32_CLIPBOARD and _HAS_KEYBOARD:
            try:
                self._paste_via_clipboard(text)
                return
            except Exception:
                self.log.debug(
                    "הדבקה דרך clipboard נכשלה - נופל להקלדה תו-אחר-תו",
                    exc_info=True,
                )
        if not _HAS_KEYBOARD:
            self.log.warning("חבילת keyboard לא מותקנת - לא ניתן להקליד את התמלול")
            return
        try:
            keyboard.write(text)
        except Exception:
            self.log.exception("נכשל בהקלדת הטקסט המתומלל")

    def _paste_via_clipboard(self, text: str):
        """שומר את תוכן ה-clipboard הנוכחי (אם הוא טקסט), מחליף אותו
        בטקסט המתומלל, שולח Ctrl+V, וממתין רגע קצר לפני שמשחזר את
        התוכן הקודם - כדי לא "לאבד" למשתמש העתקה קודמת שלו."""
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
        time.sleep(0.05)  # רגע קטן כדי שההדבקה תספיק "להיקלט" לפני שמשחזרים

        win32clipboard.OpenClipboard()
        try:
            win32clipboard.EmptyClipboard()
            if previous_text is not None:
                win32clipboard.SetClipboardData(win32con.CF_UNICODETEXT, previous_text)
        finally:
            win32clipboard.CloseClipboard()

    # ------------------------------------------------------------------ #
    # טעינה/שחרור מודל Whisper
    # ------------------------------------------------------------------ #

    def _unload_model(self):
        """משחרר את מודל Whisper מהזיכרון (בעיקר משמעותי ל-GPU/VRAM),
        כדי לפנות משאבים בזמן שממילא לא מאזינים (השתקה)."""
        with self._model_lock:
            if self._model is None:
                return
            self.log.info("משחרר את מודל Whisper מהזיכרון (השתקה)")
            play_event_sound("model_unloading", self.config, self.log)
            self._model = None
            import gc
            gc.collect()
            self.log.info("מודל Whisper שוחרר בהצלחה")

    def _reload_model_if_needed(self):
        """נקרא בביטול השתקה - טוען מחדש את המודל רק אם הוא באמת לא
        טעון כרגע, ורק אם עדיין לא הושתק שוב בינתיים (אם המשתמש הספיק
        להשתיק שוב לפני שהטעינה החלה - אין טעם לטעון רק כדי לשחרר שוב)."""
        if self._muted:
            return
        with self._model_lock:
            if self._model is not None:
                return
            self._load_model(critical=False)

    def _load_model(self, critical: bool = True):
        """טוען את מודל Whisper. critical=True (ברירת המחדל, משמש
        בטעינה הראשונית) עוצר את כל ה-WakeWordDetector אם הטעינה נכשלת
        לגמרי. critical=False (משמש בטעינה מחדש אחרי השתקה) רק מתעד
        שגיאה בלוג בלי לעצור את ההאזנה - כך שאפשר לנסות שוב בהשתקה/
        ביטול השתקה הבאים בלי שהתוכנה "תקרוס" בשקט."""
        with self._model_lock:
            if self._economy_mode:
                # מצב חסכון: מודל קטן, CPU בלבד תמיד (בכוונה מתעלם מ-
                # whisper_device/whisper_model_size הרגילים) - למקרה
                # שהמודל הרגיל (GPU/מודל גדול) גורם לעומס/חום/רעש מוגזם.
                size = self.config.get("whisper_economy_model_size", "small")
                device = "cpu"
                compute_type = self.config.get("whisper_economy_compute_type", "int8")
                self.log.info("טוען מודל Whisper במצב חסכון: size=%s (CPU בלבד)", size)
                self._announce("model_loading", next_state="idle")
                try:
                    self._model = _load_whisper_with_recovery(size, device, compute_type, self.log)
                    self.log.info("מודל Whisper (מצב חסכון) נטען בהצלחה")
                    self._announce("model_loaded", next_state="idle")
                except Exception as e:  # noqa: BLE001
                    self.log.exception("שגיאה בטעינת מודל Whisper במצב חסכון")
                    self.on_error(f"שגיאה בטעינת מודל Whisper (מצב חסכון): {e}")
                    if critical:
                        self._running = False
                        self.on_critical_whisper_failure()
                return

            size = self.config.get("whisper_model_size", "tiny")
            device = self.config.get("whisper_device", "cpu")
            compute_type = self.config.get("whisper_compute_type", "int8")

            # מודל ה-CPU לנפילה חזרה (בד"כ קטן/מהיר יותר, למשל "medium") -
            # שונה בכוונה מהמודל הראשי (שיכול להיות large-v3 כבד), כי מודל
            # ענק על CPU יהיה כמעט בלתי שמיש (איטי מדי לשימוש קולי בזמן אמת).
            fallback_size = self.config.get("whisper_cpu_fallback_model_size", "medium")
            fallback_compute = self.config.get("whisper_cpu_fallback_compute_type", "int8")

            if device == "cuda":
                # בודקים בפועל שיש GPU תואם CUDA *לפני* שמנסים לטעון עליו -
                # כדי לתת הודעת לוג ברורה, ולא סתם לחכות לחריגה מתוך
                # CTranslate2/cuBLAS שקשה יותר לפענח למשתמש שאין לו GPU בכלל.
                try:
                    import ctranslate2
                    has_gpu = ctranslate2.get_cuda_device_count() > 0
                except Exception:
                    has_gpu = False

                if not has_gpu:
                    self.log.warning(
                        "לא זוהה GPU תואם CUDA (או שחסרות ספריות cuBLAS/cuDNN) - "
                        "נופל חזרה למודל CPU '%s'", fallback_size
                    )
                    device, compute_type, size = "cpu", fallback_compute, fallback_size

            self.log.info("טוען מודל Whisper: size=%s device=%s compute_type=%s",
                           size, device, compute_type)
            self._announce("model_loading", next_state="idle")
            try:
                self._model = _load_whisper_with_recovery(size, device, compute_type, self.log)
                self.log.info("מודל Whisper נטען בהצלחה")
                self._announce("model_loaded", next_state="idle")
                return
            except Exception as e:  # noqa: BLE001
                if device == "cuda":
                    # ה-GPU "נראה" זמין (get_cuda_device_count הצליח) אבל
                    # הטעינה בפועל נכשלה - למשל cuBLAS/cuDNN לא נמצאו בזמן
                    # ריצה, driver ישן מדי, או VRAM לא מספיק למודל large-v3.
                    # נופלים חזרה למודל CPU קטן יותר, במקום לקרוס לגמרי.
                    self.log.exception(
                        "טעינת מודל Whisper על GPU נכשלה - מנסה נפילה חזרה ל-CPU עם '%s'",
                        fallback_size,
                    )
                    try:
                        self._model = _load_whisper_with_recovery(
                            fallback_size, "cpu", fallback_compute, self.log,
                        )
                        self.log.info("מודל Whisper נטען בהצלחה (נפילת CPU לאחר כשל GPU)")
                        self._announce("model_loaded", next_state="idle")
                        return
                    except Exception:
                        self.log.exception("גם נפילת ה-CPU החוזרת נכשלה")

                self.log.exception("שגיאה בטעינת מודל Whisper")
                self.on_error(f"שגיאה בטעינת מודל Whisper: {e}")
                if critical:
                    self._running = False
                    self.on_critical_whisper_failure()

    def is_economy_mode(self) -> bool:
        return self._economy_mode

    def announce_external(self, event_name: str, active_state: str = "talking",
                           next_state: str = "idle"):
        """גרסה ציבורית של _announce, לשימוש מ-main.py - פעולות
        שמופעלות מתפריט המגש (לא מפקודה קולית, כמו קביעת/ביטול טיימר
        כיבוי מחשב) עדיין רוצות "להכריז" בקול/גיף בדיוק כמו שפקודה
        קולית מקבילה הייתה עושה, כדי שההתנהגות תהיה עקבית בין תפריט
        לקול. ראו main.py: handle_shutdown_timer_requested/_cancel."""
        self._announce(event_name, next_state=next_state, active_state=active_state)

    def set_economy_mode(self, enabled: bool):
        """"דאוס מצב חסכון" / "דאוס תחזור", וגם Ctrl+Alt+P (ראו main.py) -
        עובר בין המודל הרגיל (לפי whisper_model_size/whisper_device
        בקונפיג - יכול להיות GPU ומודל גדול) לבין מודל "small" קבוע על
        CPU בלבד, ולהפך. שימושי אם המודל הרגיל גורם לעומס/חום/רעש
        מוגזם, ורוצים לרדת זמנית לאיכות תמלול נמוכה יותר אבל קלה
        משמעותית על המחשב. הטעינה מחדש קורית ברקע (לא חוסמת)."""
        if self._economy_mode == enabled:
            self.log.info("כבר במצב %s - אין צורך בשינוי",
                           "חסכון" if enabled else "רגיל")
            return

        self._economy_mode = enabled
        self.log.info("עובר למצב %s",
                       "חסכון (Whisper small, CPU בלבד)" if enabled else "רגיל")

        def _reload():
            with self._model_lock:
                self._model = None
            self._load_model(critical=False)

        threading.Thread(target=_reload, daemon=True).start()

    def _audio_callback(self, indata, frames, time_info, status):
        if status:
            self.log.warning("סטטוס חריג מהמיקרופון: %s", status)
        self._audio_q.put(indata.copy())

    def _resolve_wasapi_input(self):
        """
        מנסה לאתר את ה-host API של WASAPI ואת התקן ברירת המחדל שלו
        (או את mic_device_index אם הוגדר במפורש), ולבנות הגדרות WASAPI
        במצב Shared (לא Exclusive).

        למה זה חשוב: PortAudio/sounddevice עלול לבחור ברירת מחדל
        (MME/DirectSound, או אפילו WASAPI ב-Exclusive) שנועלת את
        המיקרופון לגמרי לאפליקציה אחת - כך שאף אפליקציה אחרת (כולל
        Chrome/Gemini) לא יכולה לגשת אליו בו-זמנית, גם אם דאוס מושתק
        או אפילו אחרי שהתהליך נסגר (עד שהדרייבר "משחרר" את הנעילה).
        WASAPI Shared הוא בדיוק המצב שדפדפנים משתמשים בו - ה-OS מערבב
        בין כל האפליקציות שמאזינות, בלי נעילה בלעדית לאף אחת מהן.

        מחזיר: (device_index_or_None, extra_settings_or_None, capture_rate)
        capture_rate הוא הקצב שבו יש להקליט בפועל - הקצב הטבעי של
        ההתקן תחת WASAPI (self.sample_rate אם WASAPI לא זמין/נכשל, ואז
        אין המרה נדרשת).
        """
        try:
            hostapis = sd.query_hostapis()
        except Exception:
            self.log.warning("לא ניתן לשאול host APIs - ממשיך עם ברירת המחדל")
            return self.mic_index, None, self.sample_rate

        wasapi_index = None
        for i, api in enumerate(hostapis):
            if "wasapi" in api.get("name", "").lower():
                wasapi_index = i
                break

        if wasapi_index is None:
            self.log.info("WASAPI לא נמצא במערכת הזו (כנראה לא Windows) - "
                           "ממשיך עם ברירת המחדל של PortAudio")
            return self.mic_index, None, self.sample_rate

        device_index = self.mic_index
        if device_index is None:
            device_index = hostapis[wasapi_index].get("default_input_device")
            if device_index is None or device_index < 0:
                self.log.warning("לא נמצא התקן קלט ברירת מחדל תחת WASAPI - "
                                  "ממשיך עם ברירת המחדל של PortAudio")
                return self.mic_index, None, self.sample_rate

        try:
            extra_settings = sd.WasapiSettings(exclusive=False)
        except Exception:
            self.log.warning("לא ניתן לבנות WasapiSettings - ממשיך בלי הגדרות מפורשות")
            return device_index, None, self.sample_rate

        # קריטי: במצב WASAPI Shared חובה להקליט בקצב הדגימה הטבעי של
        # ההתקן (default_samplerate) - בקשת קצב שרירותי (כמו 16000Hz)
        # יכולה להיכשל או לגרום נפילה ל-host API אחר שנועל את ההתקן.
        try:
            device_info = sd.query_devices(device_index)
            capture_rate = int(device_info["default_samplerate"])
        except Exception:
            self.log.warning("לא ניתן לשאול קצב דגימה טבעי - נופל חזרה ל-%s Hz",
                              self.sample_rate)
            capture_rate = self.sample_rate

        self.log.info(
            "נבחר התקן קלט תחת WASAPI (אינדקס=%s) במצב Shared (לא בלעדי), "
            "קצב דגימה טבעי=%d Hz (יומר בתוכנה ל-%d Hz) - "
            "כדי לאפשר לאפליקציות אחרות (כמו Chrome/Gemini) להאזין במקביל",
            device_index, capture_rate, self.sample_rate,
        )
        return device_index, extra_settings, capture_rate

    def _run(self):
        self._load_model()
        if self._model is None:
            return

        try:
            devices_info = sd.query_devices()
            self.log.debug("התקני שמע זמינים:\n%s", devices_info)
        except Exception:  # noqa: BLE001
            pass

        device_index, extra_settings, capture_rate = self._resolve_wasapi_input()
        blocksize = int(capture_rate * self.chunk_duration)

        try:
            stream = sd.InputStream(
                samplerate=capture_rate,
                channels=1,
                dtype="float32",
                blocksize=blocksize,
                device=device_index,
                extra_settings=extra_settings,
                callback=self._audio_callback,
            )
        except Exception as e:  # noqa: BLE001
            if extra_settings is not None:
                # ייתכן שהבקשה המפורשת ל-Shared נכשלה מסיבה כלשהי (למשל
                # דרייבר ספציפי) - נופלים חזרה להתנהגות הקודמת (ברירת
                # מחדל של PortAudio, בקצב sample_rate הרגיל) במקום לקרוס.
                self.log.warning(
                    "פתיחת המיקרופון במצב WASAPI Shared נכשלה (%s) - מנסה שוב "
                    "בלי הגדרות מפורשות ובקצב %d Hz", e, self.sample_rate
                )
                capture_rate = self.sample_rate
                blocksize = int(capture_rate * self.chunk_duration)
                try:
                    stream = sd.InputStream(
                        samplerate=capture_rate,
                        channels=1,
                        dtype="float32",
                        blocksize=blocksize,
                        device=self.mic_index,
                        callback=self._audio_callback,
                    )
                except Exception as e2:  # noqa: BLE001
                    self.log.exception("לא ניתן לפתוח את המיקרופון")
                    self.on_error(f"לא ניתן לפתוח את המיקרופון: {e2}")
                    self._running = False
                    return
            else:
                self.log.exception("לא ניתן לפתוח את המיקרופון")
                self.on_error(f"לא ניתן לפתוח את המיקרופון: {e}")
                self._running = False
                return

        self.log.info(
            "המיקרופון נפתח בהצלחה (קצב הקלטה=%d Hz), מאזין... "
            "(מילות הפעלה: %s, סף פאזי: %.2f, סף שקט: %.4f)",
            capture_rate, self.wake_words, self.threshold, self.silence_rms_threshold,
        )
        # שימו לב: משתמשים כאן ב-_request_ambient_state ולא בקריאה
        # ישירה ל-on_state_change - כדי לא לקטוע באכזריות הכרזה
        # שעדיין "מדברת" (למשל "model_loaded", שיכולה עדיין להיות
        # בעיצומה ברגע הזה בדיוק - פתיחת ה-stream מהירה בהרבה מאשר
        # טעינת המודל, כך שהקוד מגיע לכאן עוד לפני שהצליל השני סיים).
        # קריאה ישירה כאן גרמה לגיף "talking" לקפוץ חזרה ל-idle
        # באמצע הדיבור השני, בזמן שהצליל עצמו (שרץ ב-thread נפרד)
        # המשיך להתנגן במלואו - בדיוק התקלה שדווחה.
        self._request_ambient_state("idle")

        with stream:
            buffer = np.zeros((0, 1), dtype="float32")
            while self._running:
                try:
                    chunk = self._audio_q.get(timeout=0.5)
                except queue.Empty:
                    continue

                if self._muted:
                    # עדיין מרוקנים את התור כדי לא לצבור זיכרון, אבל לא מעבדים
                    buffer = np.zeros((0, 1), dtype="float32")
                    continue

                buffer = np.concatenate([buffer, chunk], axis=0)

                if len(buffer) >= blocksize:
                    audio_slice = buffer[:blocksize, 0]
                    buffer = buffer[blocksize:]
                    if capture_rate != self.sample_rate:
                        audio_slice = _resample_audio(
                            audio_slice, capture_rate, self.sample_rate
                        )
                    self._process_chunk(audio_slice)

    # ------------------------------------------------------------------ #
    # צלילי "הכרזה" + אנימציית "מדבר"
    # ------------------------------------------------------------------ #

    def _request_ambient_state(self, state: str):
        """כמו self.on_state_change(state), אבל רק אם דאוס לא באמצע
        "לדבר" כרגע (ראו self._announce_busy) - משמש בכל מקום
        ב-_process_chunk שרוצה לעדכן listening/idle "סתם" (רעש רקע,
        שקט, המתנה) בלי לדרוס הכרזה שרצה.

        קריטי: הבדיקה וההפעלה קורות **יחד תחת אותו לוק** (_announce_lock)
        שההכרזות עצמן (_announce/_start_next_announcement_locked)
        משתמשות בו כדי לשנות את self._announce_busy - לא "בודקים ואז
        קוראים" (שזה חשוף למרוץ: ת'רד הרקע שטוען את המודל יכול להתחיל
        הכרזה חדשה בדיוק בפער שבין הבדיקה לקריאה בפועל, ואז הקריאה
        "הישנה" שכבר עברה את הבדיקה מגיעה רגע אחרי ודורסת את 'talking'
        הטרי - בדיוק התופעה של 'מתחיל לדבר ואז חצי שנייה אחר כך חוזר
        ל-idle'). עם לוק משותף אין פער כזה: או שהבדיקה קורית *לפני*
        שההכרזה בכלל התחילה (busy=False, הקריאה עוברת, ואז ההכרזה
        מתחילה אחריה כרגיל), או *אחרי* שהיא כבר סימנה busy=True
        (הקריאה מדלגת, כמתוכנן) - אף פעם לא "באמצע"."""
        with self._announce_lock:
            if self._announce_busy:
                return
            self.on_state_change(state)

    def _announce(self, event_name: str, next_state: str = "idle", active_state: str = "talking",
                  on_complete=None):
        """מוסיף הכרזה לתור ההכרזות (ראו self._announce_queue) - כל
        צליל שדאוס "אומר" למשתמש (תחילת/סיום תמלול, תחילת/סיום לימוד
        מאקרו, הפעלת מאקרו, טעינת/סיום טעינת מודל) - חוץ מה-"blip"
        הקצר שמתנגן בכל זיהוי מילת ההפעלה (ראו _WAKE_WORD_SOUND_EVENT
        וקריאתו ב-_process_chunk, שלא עובר דרך הפונקציה הזו בכוונה).

        active_state קובע איזו אנימציה מוצגת *בזמן* שהצליל מתנגן:
        ברירת המחדל "talking" (deus_talking.gif) מתאימה לצלילים
        שהם ממש "דיבור" של דאוס (למשל "מתחיל תמלול"). לצלילים
        שהם יותר "רעש פעולה/עיבוד" (למשל צליל צילום מסך, או צליל
        שמלווה הרצת סקריפט/מאקרו/קליקר/הורדת סרטון/תרגום/ניקוי
        זיכרון) קוראים עם active_state="thinking" - כדי שנשאר מוצג
        deus_thinking.gif לאורך כל הפעולה, במקום לעבור ל-talking.

        on_complete (אופציונלי) נקרא *אחרי* שההכרזה סיימה להתנגן (לפי
        המשך המוערך שלה) - משמש בעיקר לפקודת "shutdown_app", כדי
        להבטיח שהצליל מתנגן *במלואו* לפני שדאוס נסגר בפועל, במקום
        לסגור מיד ולתת לצליל "להיקטע" (ראו command_id == "shutdown_app"
        ב-_handle_command).

        אם אין הכרזה אחרת "מדברת" כרגע - זו מתחילה מיד (עדיין תחת
        אותו לוק, ראו _start_next_announcement_locked). אם כן (למשל
        "model_loaded" שמגיע מיד אחרי "model_loading") - היא נכנסת
        לתור ומתנגנת רק אחרי שההכרזה הנוכחית מסיימת לפי המשך המוערך
        שלה, כדי ששני הצלילים לא ירוצו אחד על השני. אנימציית ה-
        active_state נשארת רציפה (בלי לאתחל את הגיף) לאורך כל התור,
        ורק כשהוא מתרוקן חוזרים ל-next_state של ההכרזה האחרונה."""
        with self._announce_lock:
            self._announce_queue.append((event_name, next_state, active_state, on_complete))
            if self._announce_busy:
                return  # יתנגן בתורו, אחרי מה שמתנגן כרגע
            self._announce_busy = True
            self._start_next_announcement_locked()

    def _start_next_announcement_locked(self):
        event_name, next_state, active_state, on_complete = self._announce_queue.pop(0)
        self.on_state_change(active_state)

        play_event_sound(event_name, self.config, self.log)
        duration = get_event_sound_duration(event_name, self.config, self.log)
        if not duration:
            duration = _DEFAULT_TALKING_ANIMATION_SEC

        timer = threading.Timer(
            duration, self._on_announcement_done, args=[next_state, on_complete]
        )
        timer.daemon = True
        timer.start()

    def _on_announcement_done(self, next_state: str, on_complete=None):
        """נקרא (מת'רד ה-Timer) כשההכרזה הנוכחית מסיימת לפי המשך
        המוערך שלה. אם יש עוד הכרזות בתור - ממשיכים ישר לבאה (בלי
        לחזור ל-next_state באמצע), אחרת חוזרים ל-next_state ומשחררים
        את הדגל 'busy' - הכל תחת אותו לוק, כדי שלא יהיה פער שבו
        _request_ambient_state "יתפוס" busy=False רגע לפני שההכרזה
        הבאה בתור בכלל התחילה. on_complete (אם סופק) נקרא בסוף, מחוץ
        ללוק - כדי שקריאה שנכנסת אליו (כמו סגירת דאוס) לא תיתקע אם
        היא בעצמה מנסה לנעול את אותו לוק בטעות."""
        call_after_unlock = None
        with self._announce_lock:
            if self._announce_queue:
                self._start_next_announcement_locked()
            else:
                self._announce_busy = False
                self.on_state_change(next_state)
                call_after_unlock = on_complete

        if call_after_unlock is not None:
            try:
                call_after_unlock()
            except Exception:
                self.log.exception("שגיאה בהרצת on_complete של הכרזה")

    # ------------------------------------------------------------------ #
    # מאקרו: עזרי חיפוש/הפעלה
    # ------------------------------------------------------------------ #

    def _play_macro_by_name(self, name: str):
        macros = self.config.get("macros", {})
        if not macros:
            self.log.warning("לא קיימים מאקרואים שמורים - אין מה להפעיל עבור '%s'", name)
            self._request_ambient_state("idle")
            return

        threshold = self.config.get("commands", {}).get(
            "command_fuzzy_threshold", self.config.get("fuzzy_threshold", 0.72)
        )
        best_name, best_score = None, 0.0
        for macro_name in macros:
            score = similarity(name, macro_name)
            if score > best_score:
                best_name, best_score = macro_name, score

        if best_name is None or best_score < threshold:
            self.log.warning("לא נמצא מאקרו תואם ל-'%s' (ציון הכי טוב=%.2f)", name, best_score)
            self._request_ambient_state("idle")
            return

        macro = macros[best_name]
        events = macro.get("events", [])
        repeat = macro.get("repeat", 1)
        self.log.info("מפעיל מאקרו '%s' (חזרות=%s)",
                       best_name, "אינסופי" if not repeat else repeat)
        self._announce("macro_play", next_state="idle", active_state="thinking")
        self._macro_player.play(events, repeat)

    # ------------------------------------------------------------------ #
    # ביצוע פקודות
    # ------------------------------------------------------------------ #

    def _handle_command(self, command_id: str, argument: str):
        """מבצע בפועל command_id/argument שכבר זוהו (על ידי parse_command) -
        בין אם זה קרה מיד אחרי מילת ההפעלה, ובין אם זה קרה בצ'אנק מאוחר
        יותר בתוך 'חלון ההמתנה לפקודה' (ראו _awaiting_command_chunks_left)."""
        if command_id == "dictation_start":
            self._dictation_active = True
            self.log.info("מתחיל מצב תמלול")
            self._announce("dictation_start", next_state="listening")
            return

        if command_id == "dictation_stop":
            # "דאוס עצור" הוא פקודת "עצור" גנרית - עוצרת את המצב
            # המתמשך שפעיל כרגע: קודם לימוד מאקרו, אחר כך הפעלת מאקרו,
            # אחר כך הקלטת שמע חופשית, אחר כך קליקר, ורק אם אף אחד
            # מהם לא פעיל - התנהגות "עצור תמלול" הרגילה.
            if self._macro_recorder.is_recording():
                events = self._macro_recorder.stop()
                name = self._pending_macro_name or "מאקרו_ללא_שם"
                self._pending_macro_name = None
                self.log.info("מאקרו '%s' נשמר עם %d אירועים", name, len(events))
                self.on_macro_saved(name, {"events": events, "repeat": 1})
                self._announce("macro_learn_stop", next_state="idle", active_state="thinking")
                return

            if self._macro_player.is_playing():
                self._macro_player.stop()
                self.log.info("עצירת הפעלת מאקרו לפי בקשה")
                self._request_ambient_state("idle")
                return

            if self._audio_recorder.is_recording():
                saved_path = self._audio_recorder.stop()
                if saved_path:
                    self.log.info("ההקלטה נעצרה ונשמרה: %s", saved_path)
                else:
                    self.log.warning("ההקלטה נעצרה אבל לא נשמר קובץ")
                self._announce("record_stop", next_state="idle")
                return

            if self._clicker.is_running():
                self._clicker.stop()
                self.log.info("עצירת קליקר לפי בקשה")
                self._announce("clicker_stop", next_state="idle")
                return

            self.log.info("פקודת עצירת תמלול")
            self._announce("dictation_stop", next_state="idle")
            return

        if command_id == "economy_mode_on":
            self.set_economy_mode(True)
            self._announce("economy_mode_on", next_state="idle", active_state="thinking")
            return

        if command_id == "economy_mode_off":
            self.set_economy_mode(False)
            self._announce("economy_mode_off", next_state="idle", active_state="thinking")
            return

        if command_id == "screen_record_toggle":
            # טוגל עצמאי - בכוונה *לא* מטופל דרך "דאוס עצור" הגנרי (כמו
            # מאקרו/הקלטת שמע/קליקר), כדי שאפשר יהיה להקליט מסך תוך
            # כדי שימוש בפקודות אחרות שמשתמשות ב"עצור" (למשל תמלול)
            # בלי שההקלטה תיפסק בטעות - אותה פקודה נאמרת שוב כדי לעצור.
            if self._screen_recorder.is_recording():
                saved_path = self._screen_recorder.stop()
                if saved_path:
                    self.log.info("הקלטת המסך נעצרה ונשמרה: %s", saved_path)
                else:
                    self.log.warning("הקלטת המסך נעצרה אבל לא נשמר קובץ")
                self._announce("screen_record_stop", next_state="idle")
            else:
                # ברירת מחדל: כן לכלול מיקרופון (כדי שאפשר יהיה לשמוע
                # מה שנאמר בהקלטה) - טוגל נפרד בתפריט המגש מאפשר לכבות.
                include_mic = self.config.get("screen_record", {}).get("include_mic", True)
                self._screen_recorder.start(include_mic=include_mic)
                self.log.info("מתחיל הקלטת מסך (כולל מיקרופון=%s)", include_mic)
                self._announce("screen_record_start", next_state="listening")
            return

        if command_id == "shutdown_app":
            # "דאוס כיבוי" - בכוונה *לא* עובר דרך ONE_SHOT_COMMANDS/
            # execute_command הרגיל (ראו ai_engine/commands.py) - כדי
            # להבטיח שהצליל מתנגן *במלואו* לפני שדאוס באמת נסגר
            # (on_complete נקרא רק אחרי שההכרזה מסתיימת, ראו _announce).
            # בלי זה, קריאה מיידית ל-app.quit() הייתה עלולה לחתוך את
            # הצליל באמצע (או לא להשמיע אותו בכלל).
            self.log.info("פקודת כיבוי - מכריז ואז סוגר את דאוס")

            def _do_shutdown():
                # חוצים בבטחה ל-thread הראשי דרך אותו מנגנון Bridge/
                # Signals הקיים (self.on_command) - קריאה ישירה ל-
                # QApplication.quit() מכאן (thread של ה-Timer, לא ה-
                # thread הראשי) הייתה עובדת ברוב המקרים, אבל עדיף
                # לא לסמוך על זה כשיש כבר מנגנון חציית-threads תקני.
                self.on_command("shutdown_app", argument)

            self._announce("shutdown_app", next_state="idle", on_complete=_do_shutdown)
            return

        if command_id == "record_start":
            interval_cfg = self.config.get("commands", {}).get("recording_sample_rate", 44100)
            self._audio_recorder.start(sample_rate=interval_cfg)
            self.log.info("מתחיל הקלטת שמע חופשית")
            self._announce("record_start", next_state="listening")
            return

        if command_id == "clicker_start":
            interval_sec = self.config.get("commands", {}).get("clicker_interval_sec", 0.5)
            self._clicker.start(interval_sec=interval_sec)
            self.log.info("מתחיל קליקר (כל %.2f שניות)", interval_sec)
            self._announce("clicker_start", next_state="listening", active_state="thinking")
            return

        if command_id == "macro_learn":
            name = argument.strip()
            if not name:
                self.log.warning("פקודת 'תלמד מאקרו' בלי שם - מדלג")
                self._request_ambient_state("idle")
                return
            self._pending_macro_name = name
            self._macro_recorder.start()
            self.log.info("מתחיל הקלטת מאקרו בשם '%s'", name)
            self._announce("macro_learn_start", next_state="listening", active_state="thinking")
            return

        if command_id == "macro_play":
            self._play_macro_by_name(argument.strip())
            return

        if command_id in ONE_SHOT_COMMANDS:
            self.log.info("מבצע פקודה '%s' (ארגומנט='%s')", command_id, argument)
            self.on_state_change("thinking")
            self.on_command(command_id, argument)

            sound_event = _COMMAND_SOUND_EVENTS.get(command_id)
            if sound_event:
                # אחרי ביצוע הפקודה בפועל - "מכריזים" עליה: אם יש קובץ
                # שמע מתאים (assets/sounds/<sound_event>.wav/.mp3) - הוא
                # מתנגן, ולאורך משכו מוצגת אנימציה - "talking"
                # (deus_talking.gif) לפקודות שבהן דאוס ממש "אומר" משהו
                # בקול, או "thinking" (deus_thinking.gif) לפקודות
                # שבהן הצליל הוא רעש פעולה/עיבוד בלבד (ראו
                # _THINKING_GIF_COMMANDS למעלה). אם אין קובץ - שקט
                # לגמרי, ורק אנימציית ברירת המחדל הקצרה
                # (_DEFAULT_TALKING_ANIMATION_SEC) מוצגת לפני חזרה
                # ל-idle - בלי קריסה בשני המקרים.
                active_state = "thinking" if command_id in _THINKING_GIF_COMMANDS else "talking"
                self._announce(sound_event, next_state="idle", active_state=active_state)
            else:
                time.sleep(1.0)  # מרווח קצר לפני חזרה להאזנה, מונע זיהוי כפול מיידי
                self._request_ambient_state("idle")
            return

        self.log.warning("command_id לא מטופל: %s", command_id)
        self._request_ambient_state("idle")

    def _process_chunk(self, audio: np.ndarray):
        rms = float(np.sqrt(np.mean(np.square(audio))))
        self.log.debug("צ'אנק שמע: RMS=%.4f (סף שקט=%.4f)", rms, self.silence_rms_threshold)

        if rms < self.silence_rms_threshold:
            # שקט - לא שווה להריץ תמלול. לא חוזרים מיד ל-idle ויזואלית -
            # רק אחרי כמה צ'אנקים רצופים של שקט, כדי לא להבהב את
            # האנימציה מרעש רקע רגעי (למשל בלופ) בין דיבור אמיתי.
            # שימו לב: שקט לא "צורך" ניסיון מתוך חלון ההמתנה לפקודה -
            # רק צ'אנקים עם דיבור בפועל נספרים (ראו למטה). לעומת זאת,
            # חלון ההמתנה ל*ארגומנט* (ראו __init__) כן נספר גם על פני
            # שקט - אחרת פקודה שממתינה לארגומנט הייתה יכולה להישאר
            # "תקועה" במתנה לנצח אם המשתמש פשוט השתתק ולא השלים אותה.
            self._consecutive_silence += 1
            if self._pending_argument_chunks_left > 0:
                self._pending_argument_chunks_left -= 1
                if self._pending_argument_chunks_left <= 0:
                    self.log.warning(
                        "לא נאמר ארגומנט עבור '%s' בזמן - מדלג על הפקודה",
                        self._pending_argument_command_id,
                    )
                    self._pending_argument_command_id = None
                    self._request_ambient_state("idle")
                    return
            still_waiting = (self._awaiting_command_chunks_left > 0
                              or self._pending_argument_chunks_left > 0)
            if (self._consecutive_silence >= _SILENCE_CHUNKS_BEFORE_IDLE
                    and not self._dictation_active and not still_waiting):
                self._request_ambient_state("idle")
            return

        self._consecutive_silence = 0
        self._request_ambient_state("listening")

        # תופסים "צילום" מקומי של המודל הנוכחי במקום לגשת ל-self._model
        # שוב ושוב - כדי למנוע מירוץ (race condition): אם set_economy_mode
        # מריץ ברקע טעינה מחדש של המודל (שם self._model מתאפס זמנית
        # ל-None) בדיוק באמצע העיבוד של הצ'אנק הזה, גישה חוזרת ל-
        # self._model הייתה עלולה לתפוס None ולקרוס עם
        # "'NoneType' object has no attribute 'transcribe'". עם משתנה
        # מקומי, גם אם self._model משתנה במקביל, הקריאה הנוכחית עדיין
        # משתמשת במה שהיה תקף כשהיא התחילה.
        model = self._model
        if model is None:
            self.log.debug("המודל עדיין לא נטען (או בטעינה מחדש כרגע) - מדלג על הצ'אנק הזה")
            self._request_ambient_state("listening")
            return

        try:
            segments, _info = model.transcribe(
                audio,
                language=self.config.get("language", "he"),
                vad_filter=True,
                # beam_size=1 (ברירת המחדל הישנה) הוא greedy decoding -
                # הכי מהיר אבל הכי פגיע לטעויות (בוחר את המילה הכי
                # סבירה בכל צעד, בלי לשקול חלופות). מאז שחלון ההמתנה
                # לפקודה נספר בצ'אנקים ולא בזמן שעון, זמן תמלול קצת
                # יותר ארוך כבר לא "עולה" לנו בפספוס - אז שווה לשלם
                # אותו בשביל דיוק גבוה יותר. ניתן לכוונון בקונפיג
                # ("whisper_beam_size") אם זה איטי מדי אצלכם.
                beam_size=self.config.get("whisper_beam_size", 5),
                # ה-prompt הזה "מטה" את וויספר להטות ניחושים לכיוון המילה
                # דאוס/Deus כשהוא לא בטוח - עוזר לזהות אותה יותר טוב.
                initial_prompt=self.initial_prompt,
            )
            transcript = " ".join(seg.text for seg in segments).strip()
        except Exception as e:  # noqa: BLE001
            self.log.exception("שגיאת תמלול")
            self.on_error(f"שגיאת תמלול: {e}")
            still_waiting = (self._dictation_active or self._awaiting_command_chunks_left > 0
                              or self._pending_argument_chunks_left > 0)
            self._request_ambient_state("listening" if still_waiting else "idle")
            return

        if not transcript:
            self.log.debug("תומלל: (ריק - כנראה רעש/שקט שעבר את סף ה-RMS אך לא זוהה בו דיבור)")
            still_waiting = (self._dictation_active or self._awaiting_command_chunks_left > 0
                              or self._pending_argument_chunks_left > 0)
            self._request_ambient_state("listening" if still_waiting else "idle")
            return

        raw_words = transcript.split()
        found, matched_word, score, start_idx, end_idx = locate_wake_word(
            raw_words, self.wake_words, self.threshold
        )

        # --- לא נאמרה מילת ההפעלה בצ'אנק הזה ---
        if not found:
            if self._pending_argument_command_id is not None:
                # ממתינים לארגומנט של פקודה שכבר זוהתה (למשל "google_search"
                # אחרי "דאוס חפש בגוגל" בלי מילות חיפוש מיד אחריו) - הצ'אנק
                # הזה כולו נלקח כארגומנט, גם בלי חזרה על "דאוס".
                command_id = self._pending_argument_command_id
                argument_text = transcript.strip()
                self._pending_argument_command_id = None
                self._pending_argument_chunks_left = 0
                self.log.info("נמצא ארגומנט ממתין עבור '%s': '%s'", command_id, argument_text)
                self._handle_command(command_id, argument_text)
                return

            if self._dictation_active:
                # במצב תמלול - כל מה שנאמר (בלי מילת הפעלה) פשוט מוקלד
                self._type_text(transcript + " ")
                self._request_ambient_state("listening")
                return

            if self._awaiting_command_chunks_left > 0:
                # מילת ההפעלה נאמרה בצ'אנק קודם בלי פקודה מיד אחריה
                # (למשל כי המשפט נחתך בין שני צ'אנקים) - בודקים אם
                # *הצ'אנק הזה כולו* הוא הפקודה שהמתנו לה, גם בלי חזרה
                # על "דאוס".
                command_id, argument = parse_command(raw_words, self.config, self.log)
                if command_id is not None:
                    self._awaiting_command_chunks_left = 0

                    if command_id in ARGUMENT_REQUIRED_COMMANDS and not argument.strip():
                        # אותה בעיה כמו למטה - הפקודה זוהתה אבל בלי
                        # ארגומנט, כנראה שהוא עוד יגיע בצ'אנק שאחרי זה.
                        self._pending_argument_command_id = command_id
                        self._pending_argument_chunks_left = self.argument_window_chunks
                        self.log.info(
                            "פקודה ממתינה '%s' זוהתה בלי ארגומנט - ממתין עד %d צ'אנקים נוספים",
                            command_id, self.argument_window_chunks,
                        )
                        self._request_ambient_state("listening")
                        return

                    self.log.info(
                        "נמצאה פקודה ממתינה בצ'אנק הבא (בלי חזרה על מילת ההפעלה): '%s'",
                        transcript,
                    )
                    self._handle_command(command_id, argument)
                    return

                self._awaiting_command_chunks_left -= 1
                self.log.debug(
                    "עדיין בחלון ההמתנה לפקודה (עוד %d צ'אנקים) - הצ'אנק הזה לא תאם אף פקודה: '%s'",
                    self._awaiting_command_chunks_left, transcript,
                )
                self._request_ambient_state("listening")
                return

            self.log.debug("תומלל: '%s' (אין מילת הפעלה, ציון הכי טוב=%.2f)",
                            transcript, score)
            self._request_ambient_state("idle")
            return

        self.log.info("זוהתה מילת הפעלה '%s' (ציון=%.2f) בתוך: '%s'",
                       matched_word, score, transcript)
        # "בליפ" קצר בכל זיהוי בפועל של מילת ההפעלה - יוצא דופן בכוונה:
        # לא עובר דרך _announce ולא מפעיל את אנימציית "talking" (ראו
        # התיעוד שם), זה רק אישור קולי קצר שהמילה נקלטה.
        play_event_sound(_WAKE_WORD_SOUND_EVENT, self.config, self.log)

        if self._pending_argument_command_id is not None:
            # המשתמש אמר "דאוס" שוב לפני שהשלים את הארגומנט הקודם -
            # מניחים שהוא מתחיל פקודה חדשה, ונוטשים את הקודמת בלי לבצע
            # אותה עם ארגומנט ריק/שגוי.
            self.log.info(
                "מילת ההפעלה נאמרה שוב לפני השלמת הארגומנט ל-'%s' - נוטש אותה",
                self._pending_argument_command_id,
            )
            self._pending_argument_command_id = None
            self._pending_argument_chunks_left = 0

        pre_words = raw_words[:start_idx]
        post_words = raw_words[end_idx:]

        # אם היינו במצב תמלול - הטקסט שלפני מילת ההפעלה עדיין שייך
        # לתמלול (מוקלד כרגיל), ומכאן התמלול נעצר - "דאוס" עצמו לעולם
        # לא מוקלד, גם אם לא נאמרה אחריו פקודה מוכרת.
        if self._dictation_active:
            pre_text = " ".join(pre_words).strip()
            if pre_text:
                self._type_text(pre_text + " ")
            self._dictation_active = False
            self.log.info("התמלול נעצר (זוהתה מילת ההפעלה תוך כדי דיבור)")

        command_id, argument = parse_command(post_words, self.config, self.log)

        if command_id is None:
            # מילת ההפעלה נאמרה, אבל לא זוהתה אחריה פקודה *באותו צ'אנק*.
            # לא מוותרים מיד - פותחים חלון המתנה (נספר בצ'אנקים, לא
            # בזמן שעון - ראו הערה ב-__init__): הצ'אנקים הבאים ייבדקו
            # כמועמדים לפקודה גם בלי לחזור על "דאוס".
            self._awaiting_command_chunks_left = self.command_window_chunks
            self.log.info(
                "לא זוהתה פקודה מיד אחרי מילת ההפעלה - ממתין עד %d צ'אנקים נוספים",
                self.command_window_chunks,
            )
            self.on_state_change("listening")
            return

        if command_id in ARGUMENT_REQUIRED_COMMANDS and not argument.strip():
            # הפקודה זוהתה בוודאות (למשל "חפש בגוגל"), אבל בלי ארגומנט
            # מיד אחריה - כנראה שהמשתמש עצר לנשום בין הפקודה למילים
            # שאחריה (למשל "דאוס חפש בגוגל" ... הפסקה קצרה ... "מסעדות
            # בתל אביב"), שנחתכות לצ'אנק השמע הבא. בלי הבדיקה הזו הפקודה
            # הייתה מתבצעת מיד עם ארגומנט ריק (חיפוש ריק בגוגל) - במקום
            # זאת ממתינים לצ'אנקים הבאים ולוקחים אותם כארגומנט (ראו
            # הטיפול למעלה תחת "לא נאמרה מילת ההפעלה").
            self._awaiting_command_chunks_left = 0
            self._pending_argument_command_id = command_id
            self._pending_argument_chunks_left = self.argument_window_chunks
            self.log.info(
                "פקודה '%s' זוהתה בלי ארגומנט - ממתין עד %d צ'אנקים נוספים למילות ההמשך",
                command_id, self.argument_window_chunks,
            )
            self.on_state_change("listening")
            return

        self._awaiting_command_chunks_left = 0
        self._handle_command(command_id, argument)
