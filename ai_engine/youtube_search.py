"""
חיפוש שיר/סרטון ביוטיוב ופתיחה/הפעלה של התוצאה הראשונה, בעקבות הפקודה
הקולית "דאוס חפש ביוטיוב <שם השיר>".

אין ל-YouTube API רשמי וחינמי בלי מפתח (API key) שמצריך רישום נפרד -
כדי לא להוסיף תלות/הגדרה מסובכת, שולפים במקום זאת את דף תוצאות
החיפוש הרגיל של יוטיוב (HTML) ומחלצים ממנו את מזהה הסרטון (videoId)
הראשון שמופיע בתוך המבנה הפנימי של הדף (ytInitialData) בעזרת regex
פשוט. זו שיטה לא רשמית שיכולה להישבר אם יוטיוב ישנה את מבנה הדף -
אם זה קורה, נופלים בחזרה לפתיחת דף תוצאות החיפוש הרגיל (כדי שהמשתמש
עדיין יוכל לבחור סרטון ידנית), במקום לקרוס.
"""

import logging
import re
import urllib.parse
import urllib.request
import webbrowser

_log = logging.getLogger("deus")

# מחפש את המחרוזת "videoRenderer":{"videoId":"XXXXXXXXXXX" בתוך ה-HTML -
# זה מזהה הסרטון הראשון שמופיע ברשימת התוצאות בפועל (לא פרסומות/הצעות
# אחרות שמופיעות במבנים אחרים בדף).
_VIDEO_ID_RE = re.compile(r'"videoRenderer":\{"videoId":"([a-zA-Z0-9_-]{11})"')


def _open_and_focus(url: str, config: dict, log: logging.Logger):
    """webbrowser.open(url) + ניסיון להביא את חלון הדפדפן לקדמה -
    בלי זה, אם הדפדפן כבר פתוח ברקע (למשל מ"דאוס צאט" קודם), הכרטיסייה
    החדשה נפתחת בלי שהחלון קופץ לתצוגה - נראה כאילו כלום לא קרה, למרות
    שהיא כן נפתחה. ראו ai_engine/commands.py::_open_url_and_focus
    (אותה לוגיקה בדיוק, כפולה כאן כי למודול הזה יש פונקציית כניסה
    עצמאית משלו)."""
    webbrowser.open(url)
    try:
        from ai_engine.launcher import bring_browser_to_foreground
        bring_browser_to_foreground(config or {}, log)
    except Exception:
        log.debug("לא ניתן היה להביא את חלון הדפדפן לקדמה (לא קריטי)", exc_info=True)


def search_and_play_first(query: str, config: dict = None, log: logging.Logger = None) -> bool:
    """מחפש את query ביוטיוב ופותח את הסרטון הראשון בדפדפן ברירת המחדל.
    מחזיר True אם משהו נפתח בפועל (גם אם זה רק דף החיפוש כ-fallback)."""
    log = log or _log
    query = (query or "").strip()
    if not query:
        log.warning("פקודת חיפוש ביוטיוב בלי מילות חיפוש - מדלג")
        return False

    search_url = "https://www.youtube.com/results?search_query=" + urllib.parse.quote(query)

    try:
        req = urllib.request.Request(search_url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=6) as resp:
            html = resp.read().decode("utf-8", errors="ignore")

        match = _VIDEO_ID_RE.search(html)
        if match:
            video_id = match.group(1)
            watch_url = f"https://www.youtube.com/watch?v={video_id}"
            _open_and_focus(watch_url, config, log)
            log.info("נפתחה תוצאת יוטיוב ראשונה עבור '%s': %s", query, watch_url)
            return True

        log.warning("לא נמצא מזהה סרטון בדף תוצאות היוטיוב - פותח את דף החיפוש עצמו")
    except Exception:
        log.exception("שגיאה בחיפוש ביוטיוב עבור '%s' - פותח את דף החיפוש הרגיל", query)

    try:
        _open_and_focus(search_url, config, log)
        return True
    except Exception:
        log.exception("נכשל גם בפתיחת דף חיפוש היוטיוב")
        return False
