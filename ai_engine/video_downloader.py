"""
"דאוס הורד סרטון" - מוריד אוטומטית את סרטון היוטיוב שכתובתו נמצאת
כרגע ב-clipboard, באיכות הטובה ביותר הזמינה (וידאו+אודיו ממוזגים),
ושומר לתיקיית Downloads של המשתמש.

משתמש ב-yt-dlp (pip install yt-dlp) - מנסה קודם דרך ה-API הפייתוני
(import yt_dlp), ואם לא מותקן מנסה להריץ אותו כפקודת שורת-פקודה
חיצונית (yt-dlp.exe / yt-dlp בנתיב PATH). אם אף אחת מהן לא זמינה -
נכשל בשקט עם אזהרה בלוג, בלי לקרוס.
"""

import logging
import os
import re
import subprocess

from ai_engine.ffmpeg_utils import ensure_ffmpeg

_log = logging.getLogger("deus")

_YOUTUBE_URL_RE = re.compile(
    r"(https?://)?(www\.)?(youtube\.com/watch\?v=[\w-]+|youtu\.be/[\w-]+"
    r"|youtube\.com/shorts/[\w-]+|m\.youtube\.com/watch\?v=[\w-]+)"
)


def _get_clipboard_text() -> str:
    try:
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance()
        if app is None:
            return ""
        return app.clipboard().text() or ""
    except Exception:
        return ""


def _downloads_dir() -> str:
    home = os.path.expanduser("~")
    downloads = os.path.join(home, "Downloads")
    os.makedirs(downloads, exist_ok=True)
    return downloads


def _download_with_python_api(url: str, out_dir: str, log: logging.Logger) -> bool:
    try:
        import yt_dlp
    except ImportError:
        return None  # לא מותקן - נסמן שצריך לנסות דרך אחרת

    ydl_opts = {
        "outtmpl": os.path.join(out_dir, "%(title)s.%(ext)s"),
        "format": "bestvideo+bestaudio/best",
        "merge_output_format": "mp4",
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
    }
    # מצביעים במפורש על ffmpeg (מותקן מקומית, או שהורד אוטומטית ב-
    # ai_engine/ffmpeg_utils.py) - כדי שמיזוג וידאו+אודיו באיכות הכי
    # טובה יעבוד גם אצל משתמש רגיל שלא התקין ffmpeg בעצמו. בלי זה,
    # yt-dlp עדיין עשוי להוריד סרטון (סטרים מוכן-ממוזג מראש, לרוב
    # באיכות נמוכה יותר), פשוט בלי היכולת למזג בעצמו.
    ffmpeg_path = ensure_ffmpeg(log)
    if ffmpeg_path:
        ydl_opts["ffmpeg_location"] = ffmpeg_path
    else:
        log.warning("ffmpeg לא זמין - ההורדה תמשיך אבל ייתכן שלא באיכות המקסימלית")

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
        return True
    except Exception:
        log.exception("yt-dlp (Python API) נכשל בהורדת %s", url)
        return False


def _download_with_cli(url: str, out_dir: str, log: logging.Logger) -> bool:
    exe = "yt-dlp"
    cmd = [
        exe, url,
        "-f", "bestvideo+bestaudio/best",
        "--merge-output-format", "mp4",
        "--no-playlist",
        "-o", os.path.join(out_dir, "%(title)s.%(ext)s"),
    ]
    ffmpeg_path = ensure_ffmpeg(log)
    if ffmpeg_path:
        cmd += ["--ffmpeg-location", ffmpeg_path]

    try:
        result = subprocess.run(cmd, capture_output=True, timeout=600, check=False)
        if result.returncode == 0:
            return True
        log.warning("yt-dlp (CLI) הסתיים עם קוד שגיאה %d: %s",
                    result.returncode, result.stderr.decode(errors="ignore")[:500])
        return False
    except FileNotFoundError:
        return None  # yt-dlp לא נמצא ב-PATH בכלל
    except Exception:
        log.exception("נכשל בהרצת yt-dlp (CLI) עבור %s", url)
        return False


def download_from_clipboard(config: dict, log: logging.Logger = None) -> bool:
    log = log or _log

    clipboard_text = _get_clipboard_text().strip()
    match = _YOUTUBE_URL_RE.search(clipboard_text)
    if not match:
        log.warning(
            "לא נמצאה כתובת יוטיוב תקינה ב-clipboard ('%s') - מדלג",
            clipboard_text[:80],
        )
        return False

    url = match.group(0)
    if not url.startswith("http"):
        url = "https://" + url

    out_dir = config.get("video_download", {}).get("dir") or _downloads_dir()
    log.info("מתחיל הורדת סרטון מ-%s אל %s", url, out_dir)

    ok = _download_with_python_api(url, out_dir, log)
    if ok is None:
        ok = _download_with_cli(url, out_dir, log)

    if ok is None:
        log.warning(
            "yt-dlp לא מותקן - אי אפשר להוריד סרטונים. "
            "אפשר להתקין עם: pip install yt-dlp"
        )
        return False

    if ok:
        log.info("הורדת הסרטון הושלמה בהצלחה")
    return bool(ok)
