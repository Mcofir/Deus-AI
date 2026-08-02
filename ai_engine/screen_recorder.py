"""
"דאוס הקלטת מסך" - מתחיל להקליט את המסך לוידאו. אמירת אותה פקודה שוב
("דאוס הקלטת מסך") עוצרת ושומרת - זה **טוגל עצמאי**, בכוונה *לא* קשור
לפקודת "דאוס עצור" הגנרית (בשונה מהקלטת שמע/קליקר/מאקרו/תמלול) - כדי
שאפשר יהיה להקליט מסך תוך כדי שימוש בפקודות אחרות שמשתמשות ב"עצור"
(למשל תמלול קולי) בלי שההקלטה תיפסק בטעות.

חשוב: זו הקלטת **מסך לוידאו**, לא לבלבל עם "דאוס הקלט" (ai_engine/
audio_recorder.py) שמקליט רק שמע גולמי מהמיקרופון. אם רוצים גם קול
בהקלטת המסך - יש אופציה נפרדת include_mic (config["screen_record"]
["include_mic"], כבוי כברירת מחדל) שמערבבת (mux) גם את המיקרופון
לתוך אותו קובץ וידאו.

מימוש: משתמש ב-ffmpeg כתהליך חיצוני (subprocess) - כלי חינמי, נפוץ
מאוד, וכבר "ברוח" הפרויקט (yt-dlp כבר נשען עליו למיזוג וידאו+אודיו).
לא כתבנו קידוד/הקלטה ידניים ב-Python כי ffmpeg עושה את זה הרבה יותר
טוב ויציב (בלי לגרור תלויות כבדות כמו OpenCV לצורך זה בלבד).

**משתמש רגיל לא צריך להתקין ffmpeg בעצמו**: אם הוא לא נמצא במערכת,
`ai_engine/ffmpeg_utils.ensure_ffmpeg()` מוריד אוטומטית (חד-פעמי,
ברקע) בילד קל ורשמי ושומר אותו תחת `%LOCALAPPDATA%\\Deus\\ffmpeg` -
בדיוק כמו ש-Deus.exe עצמו לא דורש התקנת Python. אם גם ההורדה נכשלת
(אין אינטרנט וכו') - הפקודה נכשלת בשקט עם אזהרה בלוג, בלי לקרוס.
"""

import datetime
import logging
import os
import re
import subprocess
import sys
import threading

from ai_engine.ffmpeg_utils import ensure_ffmpeg

_log = logging.getLogger("deus")

# מונע חלון קונסולה שחור שקופץ למסך בכל קריאה ל-ffmpeg.exe (הוא תהליך
# קונסולה, ו-Deus.exe עצמו בנוי --windowed בלי קונסולה - בלי הדגל הזה
# Windows פותח קונסולה חדשה עבורו). קיים רק ב-Windows.
_NO_WINDOW_FLAGS = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0


def _downloads_dir() -> str:
    home = os.path.expanduser("~")
    downloads = os.path.join(home, "Downloads")
    os.makedirs(downloads, exist_ok=True)
    return downloads


_DSHOW_DEVICE_RE = re.compile(r'"([^"]+)"\s*\((audio|video)\)')


def _find_default_mic_dshow_name(ffmpeg_path: str, log: logging.Logger):
    """שואל את ffmpeg עצמו (ffmpeg -list_devices) איזה שם dshow יש
    למיקרופון ברירת המחדל של Windows - כדי לא להכריח את המשתמש להזין
    אותו ידנית. מחזיר None אם לא הצליח (במקרה כזה מקליטים בלי מיקרופון
    ולא נכשלים על כל ההקלטה בגלל זה).

    חשוב: גרסאות ffmpeg ישנות יותר הדפיסו כותרות סקציה נפרדות
    ("DirectShow audio devices" / "DirectShow video devices") ואז את
    שמות ההתקנים תחתן. גרסאות עדכניות (למשל 8.x) **לא** מדפיסות
    כותרות כאלה בכלל יותר - כל שורת התקן מסומנת inline עם "(audio)"
    או "(video)" מיד אחרי השם, למשל:
        [dshow @ ...] "Microphone Array (...)" (audio)
    פרסור לפי כותרות סקציה (הגרסה הקודמת של הפונקציה) פשוט לא מוצא
    שום דבר מול הפורמט החדש - ה-regex כאן מזהה את השם ישירות מהשורה
    עצמה, בלי תלות בכותרות סקציה בכלל, ועובד מול שני הפורמטים."""
    try:
        result = subprocess.run(
            [ffmpeg_path, "-list_devices", "true", "-f", "dshow", "-i", "dummy"],
            capture_output=True, timeout=10, check=False,
            creationflags=_NO_WINDOW_FLAGS,
        )
        output = result.stderr.decode(errors="ignore")
        for line in output.splitlines():
            if "Alternative name" in line:
                continue  # שורת ה-GUID הפנימי של ההתקן, לא השם הידידותי
            match = _DSHOW_DEVICE_RE.search(line)
            if match and match.group(2) == "audio":
                return match.group(1)
    except Exception:
        log.debug("לא ניתן היה לזהות אוטומטית מיקרופון dshow עבור הקלטת מסך", exc_info=True)
    return None


class ScreenRecorder:
    """מקליט את המסך (ואופציונלית מיקרופון) לקובץ mp4 דרך ffmpeg, עד
    stop(). טוגל עצמאי - לא קשור למנגנון "דאוס עצור" הגנרי."""

    def __init__(self, logger: logging.Logger = None):
        self.log = logger or _log
        self._process = None
        self._output_path = None
        self._lock = threading.Lock()

    def is_recording(self) -> bool:
        with self._lock:
            return self._process is not None and self._process.poll() is None

    def start(self, include_mic: bool = False, fps: int = 30):
        with self._lock:
            if self._process is not None and self._process.poll() is None:
                self.log.warning("כבר מתבצעת הקלטת מסך - מדלג")
                return

            ffmpeg_path = ensure_ffmpeg(self.log)
            if not ffmpeg_path:
                self.log.warning(
                    "הקלטת מסך לא זמינה - לא נמצא ולא ניתן היה להוריד "
                    "ffmpeg אוטומטית (בדקו חיבור לאינטרנט)"
                )
                return

            timestamp = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
            out_dir = _downloads_dir()
            self._output_path = os.path.join(out_dir, f"deus_screen_recording_{timestamp}.mp4")

            cmd = [
                ffmpeg_path, "-y",
                "-f", "gdigrab", "-framerate", str(fps), "-i", "desktop",
            ]

            mic_name = None
            if include_mic:
                mic_name = _find_default_mic_dshow_name(ffmpeg_path, self.log)
                if mic_name:
                    cmd += ["-f", "dshow", "-i", f"audio={mic_name}"]
                else:
                    self.log.warning(
                        "לא זוהה מיקרופון אוטומטית - ממשיך בהקלטת מסך בלי שמע"
                    )

            cmd += ["-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p"]
            if mic_name:
                cmd += ["-c:a", "aac"]
            cmd += [self._output_path]

            try:
                self._process = subprocess.Popen(
                    cmd, stdin=subprocess.PIPE,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    creationflags=_NO_WINDOW_FLAGS,
                )
                self.log.info(
                    "התחלת הקלטת מסך -> %s (מיקרופון=%s)",
                    self._output_path, "כן" if mic_name else "לא",
                )
            except Exception:
                self.log.exception("נכשל בהפעלת ffmpeg להקלטת מסך")
                self._process = None

    def stop(self):
        """עוצר את ההקלטה בעדינות (שולח 'q' ל-ffmpeg כדי שיסגור את
        קובץ ה-mp4 כמו שצריך, במקום להרוג את התהליך בכוח ולסכן קובץ
        פגום), ומחזיר את הנתיב שנשמר (או None אם לא הייתה הקלטה)."""
        with self._lock:
            if self._process is None:
                return None
            proc = self._process
            output_path = self._output_path
            self._process = None

        if proc.poll() is not None:
            return output_path  # כבר הסתיים לבד (למשל ffmpeg קרס)

        try:
            proc.stdin.write(b"q")
            proc.stdin.flush()
            proc.wait(timeout=10)
        except Exception:
            self.log.warning("עצירה עדינה של ffmpeg נכשלה - מסיים בכוח")
            try:
                proc.terminate()
                proc.wait(timeout=5)
            except Exception:
                self.log.exception("נכשל גם בעצירה בכוח של הקלטת המסך")

        if output_path and os.path.exists(output_path):
            self.log.info("הקלטת המסך נשמרה: %s", output_path)
            return output_path

        self.log.warning("קובץ הקלטת המסך לא נמצא בסיום - ייתכן שההקלטה נכשלה")
        return None
