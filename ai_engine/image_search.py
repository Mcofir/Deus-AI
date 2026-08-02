"""
"דאוס חפש תמונה בגוגל" - מצלם את המסך הראשי ומעלה אותו ל-Google Lens
*דרך הדפדפן האמיתי של המשתמש* (לא דרך בקשת HTTP נפרדת) - חיפוש הפוך
*מלא ואוטומטי*, בלי צורך בהדבקה (Ctrl+V) ידנית.

**גרסה שנייה, אחרי שהגרסה הראשונה נכשלה בבדיקה אמיתית**: הגרסה
הקודמת שלחה POST ישירות מ-Python (חבילת requests) אל
https://lens.google.com/upload, וקיבלה בחזרה כתובת תוצאות תקינה
(נבדק ועבד ב-POST גולמי) - אבל כשהמשתמש בפועל פתח את הכתובת הזו
בדפדפן שלו, גוגל הציגה "פג תוקף של החיפוש החזותי" עם אייקון תמונה
שבורה. הסיבה: Google Lens קושר את התמונה שהועלתה לעוגיות (cookies)
של ה-session שביצע את ההעלאה. Python (requests) מבצע את ההעלאה
כ-session אנונימי בלי שום עוגיות של המשתמש - ואז כשפותחים את כתובת
התוצאות בדפדפן *האמיתי* (session שונה לגמרי, עם העוגיות האמיתיות של
המשתמש) - גוגל לא מזהה/לא סומך על ה-session שהעלה את התמונה, ומציגה
שהחיפוש "פג תוקף".

**הפתרון בגרסה הזו**: במקום לבצע את ההעלאה מ-Python, בונים קובץ HTML
זמני מקומי עם טופס שמעלה את התמונה (מקודדת כ-base64, מומרת ל-File
דרך DataTransfer API בג'אווהסקריפט) ישירות ל-lens.google.com/upload,
ופותחים את הקובץ הזה *בדפדפן עצמו* - כך שההעלאה בפועל מתבצעת מתוך
ה-session האמיתי של המשתמש (עם כל העוגיות שלו), בדיוק כמו שהיה קורה
אם הוא היה לוחץ ידנית על "בחר קובץ" ומעלה את הצילום בעצמו. זו לא
"עקיפה" - זו בדיוק אותה בקשת POST שהדפדפן היה שולח ממילא בהעלאה
ידנית, רק שהקובץ נבחר אוטומטית על ידי קוד ולא דרך תיבת דו-שיח.

גיבוי (fallback): אם היצירה/הפתיחה של הקובץ הזמני נכשלת מכל סיבה -
נופלים חזרה להתנהגות הישנה והבטוחה: מעתיקים את הצילום ל-clipboard
ופותחים את images.google.com כדי שאפשר יהיה להדביק (Ctrl+V) ידנית.

תלויות: numpy, mss (לצילום מסך - כבר תלויות קיימות בפרויקט דרך
screen_actions.py). אין תלות בחבילת requests - ההעלאה עצמה קורית
בדפדפן, לא ב-Python.
"""

import base64
import logging
import os
import tempfile
import webbrowser

try:
    import numpy as np
    import mss
    import mss.tools
    _HAS_SCREEN = True
except ImportError:
    _HAS_SCREEN = False

try:
    import cv2
    _HAS_CV2 = True
except ImportError:
    _HAS_CV2 = False

_log = logging.getLogger("deus")

_IMAGES_HOME_URL = "https://images.google.com/"
_LENS_UPLOAD_URL = "https://lens.google.com/upload"

_UPLOAD_HTML_TEMPLATE = """<!doctype html>
<html><head><meta charset="utf-8"><title>Deus - חיפוש תמונה בגוגל</title></head>
<body style="background:#202124;color:#e8eaed;font-family:Arial,sans-serif;
display:flex;align-items:center;justify-content:center;height:100vh;margin:0;">
<p>מעלה תמונה לחיפוש הפוך ב-Google...</p>
<form id="lensForm" action="{upload_url}" method="POST" enctype="multipart/form-data">
  <input type="file" name="encoded_image" id="lensFile" style="display:none">
</form>
<script>
  var b64 = "{b64_data}";
  var byteChars = atob(b64);
  var bytes = new Uint8Array(byteChars.length);
  for (var i = 0; i < byteChars.length; i++) {{
    bytes[i] = byteChars.charCodeAt(i);
  }}
  var file = new File([bytes], "screenshot.png", {{ type: "image/png" }});
  var dt = new DataTransfer();
  dt.items.add(file);
  document.getElementById("lensFile").files = dt.files;
  document.getElementById("lensForm").submit();
</script>
</body></html>
"""


def _grab_screenshot_png_bytes() -> bytes:
    """מצלם את המסך הראשי ומחזיר אותו כבייטים בפורמט PNG."""
    with mss.mss() as sct:
        monitor = sct.monitors[1]
        shot = sct.grab(monitor)

    if _HAS_CV2:
        img = np.array(shot)  # BGRA
        bgr = cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)
        ok, buf = cv2.imencode(".png", bgr)
        if ok:
            return buf.tobytes()

    # נפילה חזרה בלי cv2: mss יודע לכתוב PNG בעצמו דרך mss.tools.
    return mss.tools.to_png(shot.rgb, shot.size)


def _copy_png_bytes_to_clipboard(png_bytes: bytes, log: logging.Logger) -> bool:
    try:
        from PySide6.QtWidgets import QApplication
        from PySide6.QtGui import QImage

        app = QApplication.instance()
        if app is None:
            log.warning("אין QApplication פעיל - לא ניתן להעתיק את הצילום ל-clipboard")
            return False

        image = QImage.fromData(png_bytes, "PNG")
        if image.isNull():
            log.warning("נכשל בפענוח הצילום להעתקה ל-clipboard")
            return False

        app.clipboard().setImage(image)
        return True
    except Exception:
        log.exception("נכשל בהעתקת הצילום ל-clipboard")
        return False


def _write_upload_html(png_bytes: bytes) -> str:
    """בונה קובץ HTML זמני שמעלה את התמונה ל-Lens *מתוך הדפדפן עצמו*
    (ראו הסבר בראש הקובץ), ומחזיר את הנתיב שלו על הדיסק."""
    b64_data = base64.b64encode(png_bytes).decode("ascii")
    html = _UPLOAD_HTML_TEMPLATE.format(upload_url=_LENS_UPLOAD_URL, b64_data=b64_data)
    path = os.path.join(tempfile.gettempdir(), "deus_lens_upload.html")
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    return path


def _open_and_focus(url: str, config: dict, log: logging.Logger):
    webbrowser.open(url)
    try:
        from ai_engine.launcher import bring_browser_to_foreground
        bring_browser_to_foreground(config, log)
    except Exception:
        log.debug("לא ניתן היה להביא את חלון הדפדפן לקדמה (לא קריטי)", exc_info=True)


def screenshot_and_search_google(config: dict, log: logging.Logger = None) -> bool:
    log = log or _log

    if not _HAS_SCREEN:
        log.warning(
            "חיפוש תמונה בגוגל לא זמין (חסרות חבילות mss / numpy) - "
            "אפשר להתקין עם: pip install mss numpy"
        )
        return False

    try:
        png_bytes = _grab_screenshot_png_bytes()
    except Exception:
        log.exception("נכשל בצילום מסך לצורך חיפוש תמונה בגוגל")
        return False

    # --- ניסיון ראשון: העלאה אוטומטית ל-Lens *דרך הדפדפן עצמו* - חיפוש
    # הפוך מלא, בלי שום פעולה ידנית נדרשת מהמשתמש, ובלי בעיית "פג תוקף"
    # (כי ההעלאה קורית מתוך ה-session/עוגיות האמיתיים של המשתמש). ---
    try:
        html_path = _write_upload_html(png_bytes)
        _open_and_focus("file:///" + html_path.replace("\\", "/"), config, log)
        log.info("חיפוש תמונה הפוך בוצע אוטומטית - התמונה מועלית ל-Lens דרך הדפדפן")
        return True
    except Exception:
        log.exception("נכשל בהעלאה האוטומטית ל-Lens - נופל לגיבוי הידני")

    # --- גיבוי: היצירה/הפתיחה של קובץ ההעלאה נכשלה - חוזרים להתנהגות
    # הישנה (מעתיקים ל-clipboard ופותחים את Google Images להדבקה ידנית). ---
    copied = _copy_png_bytes_to_clipboard(png_bytes, log)
    if not copied:
        log.warning("הצילום לא הועתק ל-clipboard - פותח את Google Images בכל זאת")

    try:
        _open_and_focus(_IMAGES_HOME_URL, config, log)
        log.info(
            "נפתח Google Images (%s) - הצילום %s ל-clipboard, אפשר להדביק (Ctrl+V) בתיבת החיפוש",
            _IMAGES_HOME_URL, "הועתק" if copied else "לא הועתק",
        )
        return True
    except Exception:
        log.exception("נכשל בפתיחת Google Images בדפדפן")
        return False
