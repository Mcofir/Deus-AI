"""
עזר משותף לאיתור/הבטחת זמינות ffmpeg - בלי לדרוש ממשתמש רגיל להתקין
שום דבר בעצמו. סדר החיפוש:

  1. עותק שכבר הורד/נשמר קודם תחת %LOCALAPPDATA%\\Deus\\ffmpeg\\ffmpeg.exe.
  2. ffmpeg שכבר מותקן במערכת ונמצא ב-PATH (למי שהתקין בעצמו/מפתחים).
  3. **הורדה אוטומטית חד-פעמית** של בילד סטטי רשמי-חינמי (gyan.dev, בילד
     "essentials") לתוך אותה תיקייה, ברקע, בפעם הראשונה שתכונה שצריכה
     ffmpeg (הקלטת מסך / הורדת סרטון) בפועל מופעלת - כדי שמשתמש רגיל
     שמתקין רק את Deus.exe עדיין יקבל את התכונות האלה "מהקופסה", בלי
     לדעת בכלל שיש כזה דבר בשם ffmpeg.

ההורדה קורית פעם אחת בלבד (הקובץ נשמר ונבדק בהפעלות הבאות), ולא חוסמת
את שאר האפליקציה (רצה synchronous רק בתוך ה-thread של הפקודה עצמה,
שכבר לא ב-UI thread). אם ההורדה נכשלת (אין אינטרנט, חומת אש וכו') -
מוחזר None ומדווח בלוג, בלי לקרוס - התכונה הספציפית פשוט מדלגת.
"""

import logging
import os
import shutil
import stat
import subprocess
import zipfile
import tempfile
import urllib.request

_log = logging.getLogger("deus")

# בילד "essentials" רשמי וחינמי - קטן יותר מ-"full" (~80MB לעומת מעל
# 200MB), מספיק לגמרי להקלטת מסך/מיזוג וידאו. אם הכתובת הזו תפסיק
# לעבוד בעתיד (גרסאות מתעדכנות) - ההורדה פשוט תיכשל בשקט ותירשם ללוג,
# בלי להפיל שום דבר אחר באפליקציה.
_FFMPEG_DOWNLOAD_URL = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"


def _cache_dir() -> str:
    local_appdata = os.environ.get("LOCALAPPDATA")
    base = os.path.join(local_appdata, "Deus", "ffmpeg") if local_appdata \
        else os.path.join(os.path.expanduser("~"), ".deus", "ffmpeg")
    os.makedirs(base, exist_ok=True)
    return base


def _cached_ffmpeg_path() -> str:
    return os.path.join(_cache_dir(), "ffmpeg.exe")


def _works(path: str) -> bool:
    try:
        subprocess.run([path, "-version"], capture_output=True, timeout=5, check=False)
        return True
    except Exception:
        return False


def _download_and_extract(log: logging.Logger) -> str:
    log.info(
        "ffmpeg לא נמצא - מוריד אוטומטית בילד קל (חד-פעמי, ~80MB) "
        "כדי שהקלטת מסך/הורדת סרטונים יעבדו בלי שתצטרכו להתקין כלום..."
    )
    try:
        with tempfile.TemporaryDirectory() as tmp_dir:
            zip_path = os.path.join(tmp_dir, "ffmpeg.zip")
            urllib.request.urlretrieve(_FFMPEG_DOWNLOAD_URL, zip_path)

            with zipfile.ZipFile(zip_path) as zf:
                exe_member = next(
                    (n for n in zf.namelist() if n.lower().endswith("/bin/ffmpeg.exe")),
                    None,
                )
                if exe_member is None:
                    log.warning("קובץ ה-ffmpeg שהורד לא במבנה הצפוי - מבטל")
                    return None
                zf.extract(exe_member, tmp_dir)
                extracted_path = os.path.join(tmp_dir, exe_member)

            dest = _cached_ffmpeg_path()
            shutil.copyfile(extracted_path, dest)
            os.chmod(dest, os.stat(dest).st_mode | stat.S_IEXEC)

        if _works(dest):
            log.info("ffmpeg הורד והותקן בהצלחה: %s", dest)
            return dest

        log.warning("ffmpeg שהורד לא עובד כמצופה")
        return None
    except Exception:
        log.exception(
            "ההורדה האוטומטית של ffmpeg נכשלה (ייתכן שאין אינטרנט/חומת "
            "אש חוסמת) - התכונה שצריכה אותו תדלג הפעם"
        )
        return None


def ensure_ffmpeg(log: logging.Logger = None) -> str:
    """מחזיר נתיב מלא ל-ffmpeg שאפשר להריץ, או None אם אין דרך להשיג
    אחד (גם ההורדה נכשלה). לא קורא לזה בכל הפעלה של דאוס - רק בפועל
    כשתכונה שצריכה ffmpeg (הקלטת מסך/הורדת סרטון) מופעלת, כדי לא
    "לבזבז" ניסיון הורדה סתם."""
    log = log or _log

    cached = _cached_ffmpeg_path()
    if os.path.exists(cached) and _works(cached):
        return cached

    system_ffmpeg = shutil.which("ffmpeg")
    if system_ffmpeg:
        return system_ffmpeg

    return _download_and_extract(log)
