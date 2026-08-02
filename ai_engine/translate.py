"""
"דאוס תרגם" - מתרגם לעברית:
  - אם נאמר ארגומנט מפורש ("דאוס תרגם שלום") - מתרגם אותו ישירות.
  - אחרת: מנסה להעתיק את הטקסט המסומן כרגע (שולח Ctrl+C ובודק אם
    ה-clipboard השתנה), ואם אין בחירה פעילה - נופל בחזרה למה שכבר
    נמצא ב-clipboard (ה"אחרון" שהועתק).

התרגום עצמו משתמש ב-endpoint הלא-רשמי אך חינמי וללא מפתח של Google
Translate (translate.googleapis.com/translate_a/single - זה בדיוק
מה שספריית googletrans הפופולרית עושה מאחורי הקלעים). אם גוגל ישנה/
יחסום את זה בעתיד - הפונקציה נכשלת בשקט (מחזירה False) ורק רושמת
ללוג, בלי לקרוס.

התוצאה מוצגת בבועה קטנה, שקופה למחצה, צמודה לסמן העכבר (חלון Qt
נפרד, always-on-top, בלי מסגרת) שנעלמת אוטומטית אחרי כמה שניות.
אופציונלי: הקראה בקול (TTS) של הטקסט המתורגם, דרך pyttsx3 (מנוע
SAPI5 מקומי של Windows - לא דורש אינטרנט/מפתח). ניתן לבטל הקראה
בתפריט המגש (config["translate"]["speak"]).
"""

import json
import logging
import threading
import time
import urllib.parse
import urllib.request

_log = logging.getLogger("deus")

_TRANSLATE_URL = (
    "https://translate.googleapis.com/translate_a/single"
    "?client=gtx&sl=auto&tl={tl}&dt=t&q={q}"
)

_POPUP_MAX_CHARS = 500


def _get_clipboard_text() -> str:
    try:
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance()
        if app is None:
            return ""
        return app.clipboard().text() or ""
    except Exception:
        _log.exception("נכשל בקריאת ה-clipboard")
        return ""


def _set_clipboard_text(text: str):
    try:
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance()
        if app is not None:
            app.clipboard().setText(text)
    except Exception:
        pass


def _get_text_to_translate(argument: str, config: dict, log: logging.Logger) -> str:
    argument = (argument or "").strip()
    if argument:
        return argument

    # מנסים לתפוס את הבחירה הנוכחית: מחליפים זמנית את ה-clipboard בערך
    # ייחודי ("סנטינל"), שולחים Ctrl+C, ובודקים אם ה-clipboard באמת
    # השתנה מהסנטינל - כך אפשר להבחין בבירור בין "יש בחירה, הועתקה"
    # לבין "אין בחירה פעילה, שום דבר לא הועתק". אם אין בחירה - נופלים
    # חזרה למה שכבר היה ב-clipboard *לפני* הניסיון (בקשה מפורשת של
    # המשתמש: "אם לא מסמן אז את מה שנמצא אחרון ב-clipboard"), ומשחזרים
    # אותו בחזרה כדי לא "לאבד" אותו.
    previous = _get_clipboard_text()
    try:
        import keyboard
    except ImportError:
        log.warning("חבילת keyboard לא מותקנת - לא ניתן לזהות בחירה, נופל ל-clipboard")
        return previous.strip()

    sentinel = "\u0000__DEUS_NO_SELECTION__\u0000"
    try:
        _set_clipboard_text(sentinel)
        keyboard.send("ctrl+c")
        time.sleep(0.15)
        copied = _get_clipboard_text()
    except Exception:
        log.exception("נכשל בניסיון להעתיק את הבחירה הנוכחית")
        _set_clipboard_text(previous)
        return previous.strip()

    if copied and copied != sentinel:
        return copied.strip()

    _set_clipboard_text(previous)
    return previous.strip()


def _translate_text(text: str, target_lang: str, log: logging.Logger):
    """מחזיר את הטקסט המתורגם, או None אם נכשל."""
    url = _TRANSLATE_URL.format(tl=target_lang, q=urllib.parse.quote(text))
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        # מבנה התשובה: [[[translated_chunk, original_chunk, ...], ...], ...]
        translated = "".join(chunk[0] for chunk in data[0] if chunk and chunk[0])
        return translated.strip() or None
    except Exception:
        log.exception("נכשל בתרגום דרך Google Translate")
        return None


def _show_popup_near_cursor(text: str, config: dict, log: logging.Logger):
    """מציג בועת תרגום שקופה למחצה ליד סמן העכבר. חייב לרוץ ב-thread
    הראשי (Qt) - execute_command תמיד רץ שם (ראו ai_engine/commands.py)."""
    try:
        from PySide6.QtCore import Qt, QTimer
        from PySide6.QtGui import QCursor, QFont
        from PySide6.QtWidgets import QApplication, QLabel

        app = QApplication.instance()
        if app is None:
            log.warning("אין QApplication פעיל - לא ניתן להציג את בועת התרגום")
            return

        popup = QLabel()
        popup.setWindowFlags(
            Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
            | Qt.Tool | Qt.NoDropShadowWindowHint
        )
        popup.setAttribute(Qt.WA_TranslucentBackground)
        popup.setAttribute(Qt.WA_DeleteOnClose)
        popup.setTextFormat(Qt.PlainText)
        popup.setWordWrap(True)
        popup.setMaximumWidth(420)
        popup.setFont(QFont("Segoe UI", 11))
        popup.setStyleSheet(
            "QLabel {"
            "  background-color: rgba(30, 30, 40, 220);"
            "  color: #f2f2f2;"
            "  border: 1px solid rgba(255,255,255,60);"
            "  border-radius: 10px;"
            "  padding: 10px 14px;"
            "}"
        )
        popup.setText(text[:_POPUP_MAX_CHARS])
        popup.adjustSize()

        pos = QCursor.pos()
        popup.move(pos.x() + 18, pos.y() + 18)
        popup.show()

        duration_ms = config.get("translate", {}).get("popup_duration_ms", 6000)
        QTimer.singleShot(duration_ms, popup.close)
        # שומרים רפרנס כדי שה-popup לא ייאסף על ידי garbage collector
        # לפני שהוא נסגר בעצמו - תלוי בקריאה חוזרת של Qt event loop.
        app.__dict__.setdefault("_deus_translate_popups", [])
        app._deus_translate_popups.append(popup)
        popup.destroyed.connect(
            lambda: app._deus_translate_popups.remove(popup)
            if popup in getattr(app, "_deus_translate_popups", []) else None
        )
    except Exception:
        log.exception("נכשל בהצגת בועת התרגום")


def _speak_text(text: str, config: dict, log: logging.Logger):
    """מקריא את הטקסט בקול, ברקע (thread נפרד, לא חוסם), דרך pyttsx3 -
    בלי קריסה אם החבילה לא מותקנת (רק אזהרה חד-פעמית ברמת debug)."""
    translate_cfg = config.get("translate", {})
    if not translate_cfg.get("speak", True):
        log.debug("הקראת תרגום כבויה בהגדרות - מדלג")
        return

    def _run():
        try:
            import pyttsx3
        except ImportError:
            log.debug(
                "pyttsx3 לא מותקן - לא ניתן להקריא את התרגום. "
                "אפשר להתקין עם: pip install pyttsx3"
            )
            return
        try:
            engine = pyttsx3.init()
            rate = translate_cfg.get("speech_rate")
            if rate:
                engine.setProperty("rate", rate)
            engine.say(text)
            engine.runAndWait()
        except Exception:
            log.exception("נכשל בהקראת התרגום")

    threading.Thread(target=_run, daemon=True).start()


def translate_and_show(argument: str, config: dict, log: logging.Logger = None) -> bool:
    log = log or _log

    source_text = _get_text_to_translate(argument, config, log)
    if not source_text:
        log.warning("אין טקסט לתרגום (לא סומן טקסט ואין כלום ב-clipboard) - מדלג")
        return False

    target_lang = config.get("translate", {}).get("target_lang", "iw")
    translated = _translate_text(source_text, target_lang, log)
    if not translated:
        log.warning("התרגום נכשל עבור: '%s'", source_text[:80])
        return False

    log.info("תורגם: '%s' -> '%s'", source_text[:60], translated[:60])

    # התצוגה חייבת לקרות ב-thread הראשי; execute_command כבר רץ שם.
    _show_popup_near_cursor(translated, config, log)
    _speak_text(translated, config, log)
    return True
