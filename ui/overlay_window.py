"""
חלון האנימציה השקוף של "Deus".

- בלי מסגרת (Frameless)
- שקוף (Translucent background)
- Always on top
- ניתן לגרירה בעכבר (לחיצה שמאלית + גרירה בכל מקום על הדמות)
- ניתן לשינוי גודל (גרירה מהפינה הימנית-תחתונה), וגם דרך תפריט גדלים מוכנים
- הגיף תמיד שומר על יחס הגובה-רוחב המקורי שלו (לעולם לא נראה מתוח/מעוך),
  גם אם החלון עצמו משתנה לגודל "לא נכון" - הגיף פשוט ממורכז בתוך החלון
- מציג GIF לפי מצב: idle / listening / thinking / shut1 -> shut2 (השתקה)
- נעלם אוטומטית בסוף אנימציה מסוימת (למשל אחרי thinking), לפי הקונפיג
- שרשור מצבים כללי (config["state_transitions"]): כל מצב יכול להיות
  מוגדר "לעבור אוטומטית" למצב אחר אחרי לופ אחד - למשל shut1 -> shut2
  (אנימציית ההשתקה: פעימה חד-פעמית ואז קיפאון על shut2 עד ביטול ההשתקה).
  זיהוי "סיום לופ אחד" מבוסס על קפיצה-אחורה של מספר הפריים (ולא על
  movie.frameCount(), שיכול להחזיר ערך לא ידוע/שגוי לפני שכל פריימי
  ה-GIF נקראו במלואם - מה שגרם בעבר לתקיעות באנימציה בלי מעבר הלאה).
- סמל מגש (tray) עם התמונה הראשונה מתוך ה-GIF כאייקון, ותפריט מלא:
  הצג/הסתר, גודל, שקיפות, לחיצה-דרך, השתקה, הפעלה אוטומטית עם Windows,
  לוגים, דיבור אוטומטי, שינוי קיצור הפעלה, ניהול אפליקציות, ניהול
  סקריפטים, ניהול מאקרואים, מדריך הפעלה
"""

import logging
import os
import subprocess

from PySide6.QtCore import (
    Qt, QTimer, Signal, QSize, QPropertyAnimation, QEasingCurve, QParallelAnimationGroup,
)
from PySide6.QtGui import (
    QMovie, QIcon, QAction, QActionGroup, QImageReader,
    QPixmap, QPainter, QColor, QBrush, QCursor,
)
from PySide6.QtWidgets import (
    QWidget,
    QLabel,
    QSystemTrayIcon,
    QMenu,
    QSizeGrip,
    QInputDialog,
    QMessageBox,
    QGraphicsOpacityEffect,
)

from ui.apps_dialog import AppsDialog
from ui.scripts_dialog import ScriptsDialog
from ui.macros_dialog import MacrosDialog
from ui.sites_dialog import SitesDialog
from ui.ram_clean_dialog import RamCleanDialog


def _build_fallback_icon() -> QIcon:
    """אייקון גיבוי פשוט (עיגול סגול) - נטען רק אם אין GIF זמין,
    כדי שלעולם לא נקבל את האזהרה 'No Icon set'."""
    size = 64
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setBrush(QBrush(QColor("#6c5ce7")))
    painter.setPen(Qt.NoPen)
    painter.drawEllipse(4, 4, size - 8, size - 8)
    painter.end()
    return QIcon(pixmap)


def _icon_from_first_frame(gif_path: str) -> QIcon:
    """בונה QIcon לאייקון המגש/חלון. מעדיף את assets/deus_icon.ico
    (נגזר מראש מהפריים הראשון של ה-GIF - ראו build_exe.bat) על פני
    חילוץ ישיר מה-GIF: הפריים הגולמי מה-GIF ריק/שקוף ברובו (הדמות
    בפועל תופסת רק חלק קטן מהקנבס), מה שגרם לאייקון להיראות זעיר -
    ל-deus_icon.ico יש חיתוך הדוק סביב הדמות עצמה שממלא את הקנבס
    הרבה יותר טוב. נופל לחילוץ הישיר מה-GIF רק אם קובץ ה-ico חסר,
    כדי שהאייקון עדיין יעבוד גם בסביבת פיתוח לפני שנוצר."""
    ico_path = os.path.join(os.path.dirname(gif_path or ""), "deus_icon.ico")
    if os.path.exists(ico_path):
        icon = QIcon(ico_path)
        if not icon.isNull():
            return icon

    if not gif_path or not os.path.exists(gif_path):
        return _build_fallback_icon()

    reader = QImageReader(gif_path)
    reader.setDecideFormatFromContent(True)
    image = reader.read()
    if image.isNull():
        return _build_fallback_icon()

    pixmap = QPixmap.fromImage(image)
    return QIcon(pixmap)


_SIZE_PRESETS = [
    ("קטן", 200),
    ("בינוני", 340),
    ("רגיל", 480),
    ("גדול", 640),
    ("ענק", 800),
]

_OPACITY_PRESETS = [100, 85, 70, 50, 30]

_VOLUME_PRESETS = [100, 75, 50, 25, 0]

_SETUP_GUIDE_HTML = """
<b>מדריך הפעלה מהיר</b><br><br>
1. חובה שיהיה מותקן במחשב <b>Google Chrome</b>.<br>
2. בהגדרות הקיצורים של Chrome (chrome://extensions/shortcuts) יש להגדיר
קיצור מקשים לפתיחה/מיקוד של ג'מיני. ברירת המחדל בתוכנה היא <b>Alt+G</b>
(אפשר לשנות בתפריט המגש: "שנה קיצור הפעלה ל-AI").<br>
3. חשוב לוודא שבהגדרות הקיצור מסומנת האפשרות שהוא יעבוד
<b>גם כש-Chrome סגור</b>.<br>
4. מומלץ לכתוב בהנחיות האישיות (System instructions) של ג'מיני
שהוא <b>ידבר בעברית</b>.<br>
5. מומלץ להפעיל <b>זיכרון לטווח ארוך (Memory)</b> בהגדרות ג'מיני.<br>
6. אופציונלי: אפשר לאשר לג'מיני גישה למיקום, ואפשר לכבות בהגדרות
את שיתוף הטאב הפתוח בדפדפן.<br>
7. אופציונלי: אפשר לחבר בהגדרות ג'מיני אפליקציות נוספות
(Gmail, יומן וכו') כדי שיוכל לבצע פעולות בפועל.<br><br>
<b>דיבור אוטומטי</b> (לחיצה אוטומטית על כפתור השיחה/הקלדה):<br>
יש לשמור צילומי מסך צמודים (crop הדוק) של הכפתורים בתיקיית
<code>assets/icons</code>, בשמות הבאים בדיוק:<br>
mic_light.png, mic_dark.png, keyboard_light.png, keyboard_dark.png<br><br>
<b>צלילים</b> (אופציונלי): שימרו קבצי wav/mp3 בתיקיית
<code>assets/sounds</code>, בשמות שמוגדרים תחת config["sounds"]["files"]
(כברירת מחדל שם הקובץ זהה לשם האירוע, למשל mute.wav, screenshot.wav).
צליל <code>blip</code> מתנגן בכל זיהוי של מילת ההפעלה "דאוס" עצמה.
שאר הצלילים (תחילת/סיום תמלול, לימוד/הפעלת מאקרו) גם מציגים לרגע את
אנימציית <code>talking</code> (יש להוסיף קובץ
<code>assets/deus_talking.gif</code> אם רוצים אנימציה כזו).<br><br>
<b>סקריפטים ומאקרואים</b>: ניתנים לניהול מתפריט המגש. מאקרו נלמד
בפקודה "דאוס תלמד מאקרו &lt;שם&gt;" ואז "דאוס עצור", ומופעל עם
"דאוס תפעיל מקרו &lt;שם&gt;".
"""


_COMMANDS_GUIDE_HTML = """
<b>רשימת פקודות - Deus</b><br>
כל פקודה נאמרת כ"דאוס" + הפקודה (ולעיתים + פרטים נוספים).<br><br>

<b>פתיחת אפליקציות ואתרים</b><br>
"פתח &lt;אפליקציה&gt;" (וורד, כתבן=Notepad, מחברת=OneNote, כרום, ווטסאפ...) -
אם אין אפליקציה תואמת, מנסה גם למצוא אתר תואם ולפתוח אותו בדפדפן &middot;
"פתח קלוד קוד" &middot; "צאט" (פתיחת ה-AI) &middot;
"פתח אתר &lt;שם&gt;" (שאזאם, pdf, פייסבוק, אינסטגרם, טיקטוק, לינקדין, אימייל, או אתר משלכם)<br><br>

<b>חיפוש</b><br>
"חפש בגוגל &lt;...&gt;" &middot; "חפש ביוטיוב &lt;...&gt;" &middot;
"חפש תמונה בגוגל" (מצלם מסך, מעתיק ל-clipboard, פותח Google Images - הדביקו עם Ctrl+V)<br><br>

<b>הקלדה וטקסט</b><br>
"תמלל" (עד "עצור") &middot; "עצור" (עוצר תמלול/מאקרו/הקלטה/קליקר - איזה שפעיל) &middot;
"מחק שורה" &middot; "תרגם" (או "תרגם &lt;טקסט&gt;") - מציג בועה ליד העכבר &middot; "אנטר"<br><br>

<b>מדיה</b><br>
"מדיה" (Play/Pause) &middot; "צלם מסך"<br><br>

<b>סקריפטים ומאקרו</b><br>
"הפעל סקריפט &lt;שם&gt;" - מריץ סקריפט מוגדר מראש (.exe/.bat/.cmd/.py/.ps1) &middot;
"תלמד מאקרו &lt;שם&gt;" ואז "עצור" לשמירה &middot; "תפעיל מקרו &lt;שם&gt;"<br><br>

<b>הקלטה, קליקר, הקלטת מסך</b><br>
"תקליט" - מקליט מיקרופון עד "עצור", נשמר ל-Downloads &middot;
"קליקר" - לוחץ כל חצי שנייה עד "עצור" &middot;
"הקלטת מסך" - מקליט וידאו של המסך (כולל מיקרופון כברירת מחדל, ניתן לכיבוי
בתפריט המגש); <b>אמרו "הקלטת מסך" שוב כדי לעצור</b>
(לא מושפע מ"עצור" - כך אפשר להקליט תוך כדי פקודות אחרות)<br><br>

<b>תחזוקת מחשב</b><br>
"נקה זיכרון" / "נקה ראם" / "סגור הכל" (לא נוגע ברשת/Wi-Fi, ומגן על תוכנות
ושירותי חומרה קריטיים) &middot;
"הורד סרטון" (מה-clipboard, ל-Downloads) &middot; "נעל מחשב" &middot;
"כיבוי" (סוגר את דאוס עצמו) &middot;
"כיבוי מחשב" (מכבה את המחשב עצמו, כברירת מחדל בעוד 10 דקות - ניתן לכיוונון
בתפריט המגש)<br><br>

<b>🐢 מצב חסכון (מודל תמלול קל יותר)</b><br>
"מצב חסכון" - עובר למודל Whisper קל וקטן יותר (CPU בלבד) - פחות מדויק
אבל הרבה יותר קל על המחשב &middot;
"תחזור" - חוזר למצב הרגיל &middot;
גם דרך <b>Ctrl+Alt+P</b> (הפיך, לא תלוי בתמלול - שימושי אם התמלול
במצב חסכון לא מזהה טוב את "תחזור" בעצמו)<br><br>

<b>עצירה כללית</b><br>
"עצור" עוצר כל מצב מתמשך פעיל (מאקרו/הקלטה/קליקר/תמלול) - חוץ מהקלטת מסך,
שנעצרת רק באמירה חוזרת של "הקלטת מסך".<br><br>

<b>רק בתפריט המגש (לא בקול)</b><br>
ניהול אפליקציות/סקריפטים/מאקרואים/אתרים &middot; ניקוי זיכרון: החרגות, סגירת מצלמה,
האם לכלול את דאוס עצמו &middot; טיימר כיבוי מחשב + ביטול (נפרד מ"כיבוי מחשב"
הקולי) &middot; מצב חסכון (עכשיו - שינוי מיידי) ומצב חסכון בהפעלה (שני פריטים
נפרדים) &middot; מיקרופון בהקלטת מסך &middot;
הקראת תרגומים בקול &middot; העלם את דאוס אוטומטית כשהעכבר מתקרב.
"""


class OverlayWindow(QWidget):
    quit_requested = Signal()
    mute_toggle_requested = Signal()
    autostart_toggle_requested = Signal(bool)
    logging_toggle_requested = Signal(bool)
    open_log_requested = Signal()
    auto_speech_toggle_requested = Signal(bool)
    hotkey_change_requested = Signal(str)
    size_change_requested = Signal(int)
    opacity_change_requested = Signal(float)
    auto_hide_near_cursor_toggle_requested = Signal(bool)
    volume_change_requested = Signal(int)
    apps_changed = Signal(dict)
    scripts_changed = Signal(dict)
    macros_changed = Signal(dict)
    sites_changed = Signal(dict)
    ram_clean_settings_changed = Signal(dict)
    translate_speak_toggle_requested = Signal(bool)
    shutdown_timer_requested = Signal(int)  # דקות
    shutdown_timer_cancel_requested = Signal()
    position_changed = Signal(int, int)  # (x, y) - נשמר כדי לזכור מיקום בין הפעלות
    economy_mode_start_toggle_requested = Signal(bool)
    economy_mode_now_toggle_requested = Signal(bool)
    screen_record_mic_toggle_requested = Signal(bool)
    shutdown_voice_default_requested = Signal(int)  # דקות, ברירת מחדל ל"דאוס כיבוי מחשב"

    def __init__(self, config: dict, logger: logging.Logger = None):
        super().__init__()
        self.config = config
        self.log = logger or logging.getLogger("deus")
        win_cfg = config.get("window", {})

        self.setWindowFlags(
            Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.Tool
        )
        self.setAttribute(Qt.WA_TranslucentBackground)

        start_w = win_cfg.get("start_width", 480)
        start_h = win_cfg.get("start_height", start_w)
        self.resize(start_w, start_h)
        self.move(win_cfg.get("start_x", 100), win_cfg.get("start_y", 100))
        self.setWindowOpacity(win_cfg.get("opacity", 1.0))
        self._min_size = win_cfg.get("min_size", 80)
        self.setMinimumSize(self._min_size, self._min_size)

        # שתי "שכבות" גיף חופפות (label + אפקט שקיפות משלה לכל אחת),
        # ולא לייבל בודד - כדי לאפשר קרוס-פייד *אמיתי*: כששני האנימציות
        # דועכות/מופיעות בו-זמנית (לא ברצף), אף פעם לא נוצר רגע עם שקיפות
        # 0 בשתיהן יחד, כלומר אין אפילו רגע אחד של "כלום מוצג". שימו לב:
        # הלייבלים לא בתוך QLayout - ממקמים אותם ידנית (setGeometry) בכל
        # שינוי גודל/מצב, כדי לשמור על יחס הגובה-רוחב המקורי של כל גיף.
        self._layers = []
        for _ in range(2):
            lbl = QLabel(self)
            lbl.setScaledContents(True)
            lbl.setAttribute(Qt.WA_TranslucentBackground)
            effect = QGraphicsOpacityEffect(lbl)
            effect.setOpacity(0.0)
            lbl.setGraphicsEffect(effect)
            self._layers.append({"label": lbl, "effect": effect, "state": None, "anim": None})
        self._front_index = 0  # אינדקס השכבה שמציגה כרגע את self._current_state בשקיפות מלאה
        self._crossfade_group = None
        self._fade_generation = 0
        # משך ההצתלבות (מילישניות) - שתי האנימציות דועכות/מופיעות
        # *בו-זמנית* למשך הזמן הזה, לא ברצף - זה מה שמבטיח שאין רגע
        # ריק ביניהן. ניתן לכוונון דרך config["window"]["fade_duration_ms"].
        self._fade_duration_ms = config.get("window", {}).get("fade_duration_ms", 150)

        self.size_grip = QSizeGrip(self)
        self.size_grip.setStyleSheet("background: transparent;")
        self.size_grip.resize(16, 16)
        self.size_grip.raise_()  # תמיד מעל שתי שכבות הגיף, כדי להישאר לחיץ

        self._movies = {}
        self._gif_native_size = {}
        self._current_state = None
        self._autohide_connected_state = None  # מצב שאליו כרגע *באמת* מחובר frameChanged (או None)
        # עוקב אחר מספר הפריים האחרון שראינו במצב הנוכחי, כדי לזהות
        # "קפיצה אחורה" (= לופ חדש התחיל) בלי תלות ב-frameCount().
        self._transition_last_frame = 0
        self._auto_hide_states = set(
            config.get("auto_hide", {}).get("states", [])
        ) if config.get("auto_hide", {}).get("enabled", False) else set()
        # שרשור מצבים: אחרי שהאנימציה של מצב X מסיימת לופ אחד, עוברים
        # אוטומטית למצב Y (למשל shut1 -> shut2, אנימציית ההשתקה: פעימה
        # חד-פעמית ואז קיפאון על הפריים האחרון עד ביטול ההשתקה).
        self._state_transitions = config.get("state_transitions", {})

        self._load_gifs()

        self._drag_pos = None
        self._mute_action = None
        self._autostart_action = None
        self._logging_action = None
        self._auto_speech_action = None
        self._economy_mode_now_action = None
        self._user_hidden = False  # True אם המשתמש הסתיר ידנית דרך תפריט המגש
        self._build_tray_icon()

        # --- "העלם את דאוס אוטומטית" (כשהעכבר מתקרב) ---
        # במקום "לחיצה דרך הגיף" הקבועה (Bool אחד, תמיד דולק/כבוי) -
        # כשהאופציה הזו מופעלת, דאוס נעלם *דינמית* ברגע שהעכבר מתקרב
        # אליו (מרווח קטן סביב הגיף), כדי לא לחסום קליקים על מה
        # שמתחתיו, ומופיע שוב חלק (fade) כשהעכבר מתרחק. ה-timer בודק
        # את מיקום העכבר מול גבולות החלון כל _CURSOR_PROXIMITY_INTERVAL_MS.
        self._cursor_auto_hide_enabled = self.config.get("window", {}).get(
            "auto_hide_near_cursor", False
        )
        self._cursor_hide_active = False  # True אם כרגע מוסתר בגלל קרבת העכבר
        self._cursor_proximity_margin = 40  # פיקסלים - "מרווח ביטחון" סביב הגיף
        self._cursor_proximity_timer = QTimer(self)
        self._cursor_proximity_timer.setInterval(120)
        self._cursor_proximity_timer.timeout.connect(self._check_cursor_proximity)
        if self._cursor_auto_hide_enabled:
            self._cursor_proximity_timer.start()

        self.set_state("idle")

    def _check_cursor_proximity(self):
        """נקרא מה-timer כל 120ms כשה'העלם את דאוס אוטומטית' מופעל -
        אם העכבר נכנס לתחום החלון (בתוספת מרווח ביטחון) - מסתירים
        אותו לגמרי (hide() - ערובה מלאה שהוא לא יחסום קליקים, בלי
        להסתבך עם WA_TransparentForMouseEvents שדורש לפעמים hide()+
        show() כדי ש-Windows "יקלוט" את השינוי - hide() אמיתי פשוט
        עובד תמיד). כשהעכבר יוצא - מציגים בחזרה (show()). לא נוגעים
        בהסתרה *ידנית* של המשתמש דרך תפריט המגש (_user_hidden)."""
        if not self._cursor_auto_hide_enabled or self._user_hidden:
            return

        cursor_pos = QCursor.pos()
        margin = self._cursor_proximity_margin
        rect = self.frameGeometry().adjusted(-margin, -margin, margin, margin)
        is_near = rect.contains(cursor_pos)

        if is_near and not self._cursor_hide_active:
            self._cursor_hide_active = True
            self.hide()
        elif not is_near and self._cursor_hide_active:
            self._cursor_hide_active = False
            self.show()

    # ------------------------------------------------------------------ #
    # GIF / מצבים
    # ------------------------------------------------------------------ #

    def _load_gifs(self):
        gif_paths = self.config.get("gifs", {})
        for state, path in gif_paths.items():
            if path and os.path.exists(path):
                self._movies[state] = QMovie(path)
                self._gif_native_size[state] = self._read_native_size(path)
                self.log.debug("נטען GIF למצב '%s': %s", state, path)
            else:
                self._movies[state] = None
                self.log.warning("קובץ GIF למצב '%s' לא נמצא בנתיב: %s", state, path)

    @staticmethod
    def _read_native_size(path: str):
        reader = QImageReader(path)
        reader.setDecideFormatFromContent(True)
        size = reader.size()
        if size.isValid() and size.width() > 0 and size.height() > 0:
            return size
        return None

    def set_state(self, state: str):
        if state == self._current_state:
            return

        if self._current_state is None:
            # הפעלה ראשונה - אין ממה "לדעוך", קופצים ישר למצב החדש.
            self._apply_state_immediate(state)
            return

        self._start_crossfade(state)

    def _configure_layer_movie(self, layer: dict, state: str):
        """מכין שכבה (label+effect) להציג את ה-GIF של state: מחליף את
        ה-QMovie המחובר, מפעיל אותו מהפריים הראשון, ומתאים גודל/יחס
        גובה-רוחב. לא נוגע בשקיפות (effect.opacity) - זה תפקידם של
        הקוראים (_apply_state_immediate / _start_crossfade)."""
        movie = self._movies.get(state)
        label = layer["label"]
        layer["state"] = state

        if movie is None:
            self.log.debug("אין GIF זמין למצב '%s' - השכבה תישאר ריקה/שקופה", state)
            label.setMovie(None)
            label.setText("")
            return

        label.setMovie(movie)
        self._apply_aspect_fit_for(label, state)
        movie.jumpToFrame(0)
        movie.start()

    def _apply_state_immediate(self, state: str):
        """קובע את state כמצב הראשון שמוצג, בלי אנימציית מעבר (אין
        ממה לדעוך - נקרא רק מ-__init__)."""
        front = self._layers[self._front_index]
        self._configure_layer_movie(front, state)
        front["effect"].setOpacity(1.0)
        self._current_state = state
        self._connect_transition_tracking(state)
        self._maybe_autoshow(state)

    def _start_crossfade(self, next_state: str):
        """מבצע קרוס-פייד *אמיתי* בין האנימציה המוצגת כרגע (בשכבה
        ה"קדמית") לבין next_state (שמוכן מראש בשכבה ה"אחורית"): שתי
        השכבות דועכות/מופיעות *בו-זמנית* (QParallelAnimationGroup),
        לא ברצף - כך שבכל רגע נתון סכום השקיפויות קרוב תמיד ל-1, ואף
        פעם לא נוצר רגע שבו שום דבר לא מוצג. משך ההצתלבות קצר בכוונה
        (config["window"]["fade_duration_ms"]) כדי שהמעבר ייראה נקי
        ומהיר, לא כמו השהיה.

        אם מגיעה בקשת set_state חדשה תוך כדי הצתלבות שכבר רצה - זו
        שכבר רצה מבוטלת (ה"דור" שלה כבר לא עדכני, ראו _fade_generation)
        כדי שלא ייערמו כמה מעברים זה על זה."""
        self._fade_generation += 1
        my_generation = self._fade_generation

        front = self._layers[self._front_index]
        back = self._layers[1 - self._front_index]

        for layer in self._layers:
            if layer["anim"] is not None:
                try:
                    layer["anim"].stop()
                except RuntimeError:
                    pass
                layer["anim"] = None

        # מכינים את השכבה האחורית עם ה-GIF החדש - הוא כבר "חי" ורץ,
        # רק שקוף לגמרי, עוד לפני שההצתלבות הוויזואלית בכלל מתחילה.
        self._configure_layer_movie(back, next_state)
        back["effect"].setOpacity(0.0)
        back["label"].show()
        back["label"].raise_()
        self.size_grip.raise_()

        fade_out = QPropertyAnimation(front["effect"], b"opacity", self)
        fade_out.setDuration(self._fade_duration_ms)
        fade_out.setStartValue(front["effect"].opacity())
        fade_out.setEndValue(0.0)
        fade_out.setEasingCurve(QEasingCurve.Linear)

        fade_in = QPropertyAnimation(back["effect"], b"opacity", self)
        fade_in.setDuration(self._fade_duration_ms)
        fade_in.setStartValue(0.0)
        fade_in.setEndValue(1.0)
        fade_in.setEasingCurve(QEasingCurve.Linear)

        group = QParallelAnimationGroup(self)
        group.addAnimation(fade_out)
        group.addAnimation(fade_in)

        def _on_finished():
            if my_generation != self._fade_generation:
                return  # הוחלף במעבר חדש יותר בינתיים - מתעלמים משאריות זה
            self._finish_crossfade(next_state, promoted_index=1 - self._front_index)

        group.finished.connect(_on_finished)
        front["anim"] = fade_out
        back["anim"] = fade_in
        self._crossfade_group = group
        group.start()

    def _finish_crossfade(self, state: str, promoted_index: int):
        """נקרא כשהצתלבות מסתיימת בפועל: מנתק מעקב לופ מהמצב הקודם,
        מקדם את השכבה האחורית להיות "קדמית" (state הופך רשמית ל-
        self._current_state), ומחבר מעקב לופ/auto-hide למצב החדש אם
        צריך."""
        prev_movie = self._movies.get(self._current_state)
        if prev_movie and self._autohide_connected_state == self._current_state:
            try:
                prev_movie.frameChanged.disconnect(self._on_frame_changed_for_transition)
            except (TypeError, RuntimeError):
                pass
            self._autohide_connected_state = None

        self._front_index = promoted_index
        self._current_state = state
        self._connect_transition_tracking(state)
        self._maybe_autoshow(state)

    def _connect_transition_tracking(self, state: str):
        """מחבר מעקב "סיום לופ" (ל-auto-hide או לשרשור מצבים כמו
        shut1->shut2) על ה-GIF הנוכחי, אם המצב מוגדר לאחד מהם."""
        movie = self._movies.get(state)
        if movie is None:
            return
        if state in self._auto_hide_states or state in self._state_transitions:
            self._transition_last_frame = 0
            movie.frameChanged.connect(self._on_frame_changed_for_transition)
            self._autohide_connected_state = state

    def _maybe_autoshow(self, state: str):
        # רק מצבים "משמעותיים" (מאזין/חושב/מדבר) מציגים את החלון
        # אוטומטית, ורק אם המשתמש לא הסתיר אותו ידנית דרך תפריט המגש,
        # וגם לא אם הוא מוסתר כרגע כי העכבר קרוב אליו (_cursor_hide_active) -
        # אחרת "העלם את דאוס אוטומטית" היה נשבר בכל שינוי מצב.
        if (state in ("listening", "thinking", "talking")
                and not self._user_hidden
                and not self._cursor_hide_active
                and not self.isVisible()):
            self.show()

    def _apply_aspect_fit_for(self, label: QLabel, state: str):
        """ממקם ומגדיל label כך שהוא תמיד ישמור על יחס הגובה-רוחב
        המקורי של ה-GIF של state, גם אם המשתמש שינה את גודל החלון
        בגרירה לגודל 'לא נכון' - כדי שהדמות לעולם לא תיראה מתוחה
        או מעוכה."""
        win_w, win_h = self.width(), self.height()
        movie = self._movies.get(state)

        if movie is None or win_h <= 0 or win_w <= 0:
            label.setGeometry(0, 0, max(win_w, 0), max(win_h, 0))
            return

        native = self._gif_native_size.get(state)
        if not native:
            # אין מידע על גודל מקורי - ממלאים את כל החלון (fallback)
            label.setGeometry(0, 0, win_w, win_h)
            movie.setScaledSize(QSize(win_w, win_h))
            return

        native_ratio = native.width() / native.height()
        win_ratio = win_w / win_h

        if win_ratio > native_ratio:
            target_h = win_h
            target_w = max(1, round(target_h * native_ratio))
        else:
            target_w = win_w
            target_h = max(1, round(target_w / native_ratio))

        x = (win_w - target_w) // 2
        y = (win_h - target_h) // 2
        label.setGeometry(x, y, target_w, target_h)
        movie.setScaledSize(QSize(target_w, target_h))

    def _apply_aspect_fit(self):
        """מיישם את התאמת יחס הגובה-רוחב על שתי שכבות הגיף - נקרא
        בשינוי גודל חלון (resizeEvent)."""
        for layer in self._layers:
            self._apply_aspect_fit_for(layer["label"], layer["state"])

    def _on_frame_changed_for_transition(self, frame_number: int):
        movie = self._movies.get(self._current_state)
        if movie is None:
            return

        # זיהוי "סיום לופ" על ידי קפיצה-אחורה של מספר הפריים (המספר
        # הנוכחי קטן מהמספר הקודם שראינו - כלומר הלופ חזר להתחלה),
        # במקום להסתמך על movie.frameCount() שלעיתים לא ידוע/שגוי.
        if frame_number < self._transition_last_frame:
            finished_state = self._current_state
            try:
                movie.frameChanged.disconnect(self._on_frame_changed_for_transition)
            except (TypeError, RuntimeError):
                pass
            self._autohide_connected_state = None

            delay = movie.nextFrameDelay()
            next_state = self._state_transitions.get(finished_state)
            if next_state:
                QTimer.singleShot(
                    delay if delay > 0 else 150,
                    lambda: self.set_state(next_state),
                )
            elif finished_state in self._auto_hide_states:
                QTimer.singleShot(delay if delay > 0 else 150, self._auto_hide_now)
            return

        self._transition_last_frame = frame_number

    def _auto_hide_now(self):
        self.log.debug("אנימציית המצב '%s' סיימה לופ אחד - מסתיר את החלון (auto-hide)",
                        self._current_state)
        self.hide()

    def reload_gif(self, state: str, path: str):
        """מאפשר להחליף GIF בזמן ריצה (שימושי לבדיקה מהירה של דמויות
        חדשות). מרענן ישירות בלי קרוס-פייד (זה עדכון טכני, לא מעבר
        מצב) אם זה המצב שמוצג כרגע."""
        if os.path.exists(path):
            self._movies[state] = QMovie(path)
            self._gif_native_size[state] = self._read_native_size(path)
            if state == self._current_state:
                front = self._layers[self._front_index]
                self._configure_layer_movie(front, state)
                front["effect"].setOpacity(1.0)

    # ------------------------------------------------------------------ #
    # גרירה ושינוי גודל
    # ------------------------------------------------------------------ #

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event):
        if self._drag_pos is not None and event.buttons() & Qt.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_pos)
            event.accept()

    def mouseReleaseEvent(self, event):
        if self._drag_pos is not None:
            # נשמר רק כשגרירה *הסתיימה* (לא בכל פיקסל תוך כדי תזוזה) -
            # כדי לזכור את המיקום להפעלה הבאה (ראו main.py: handle_position_changed).
            self.position_changed.emit(self.x(), self.y())
        self._drag_pos = None

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.size_grip.move(self.width() - 16, self.height() - 16)
        self._apply_aspect_fit()

    # ------------------------------------------------------------------ #
    # סמל מגש המערכת (system tray)
    # ------------------------------------------------------------------ #

    def _build_tray_icon(self):
        self.tray = QSystemTrayIcon(self)

        idle_path = self.config.get("gifs", {}).get("idle")
        icon = _icon_from_first_frame(idle_path)
        self.tray.setIcon(icon)
        self.setWindowIcon(icon)
        self.tray.setToolTip("Deus")

        menu = QMenu()

        show_action = QAction("הצג/הסתר", self)
        show_action.triggered.connect(self._toggle_visible)
        menu.addAction(show_action)

        menu.addSeparator()

        # --- גודל ---
        size_menu = menu.addMenu("גודל")
        size_group = QActionGroup(self)
        size_group.setExclusive(True)
        current_size = self.config.get("window", {}).get("start_width", 480)
        for label, value in _SIZE_PRESETS:
            act = QAction(f"{label} ({value})", self)
            act.setCheckable(True)
            act.setChecked(value == current_size)
            act.triggered.connect(lambda checked, v=value: self._on_size_selected(v))
            size_group.addAction(act)
            size_menu.addAction(act)

        # --- שקיפות ---
        opacity_menu = menu.addMenu("שקיפות")
        opacity_group = QActionGroup(self)
        opacity_group.setExclusive(True)
        current_opacity = round(self.config.get("window", {}).get("opacity", 1.0) * 100)
        for percent in _OPACITY_PRESETS:
            act = QAction(f"{percent}%", self)
            act.setCheckable(True)
            act.setChecked(percent == current_opacity)
            act.triggered.connect(lambda checked, p=percent: self._on_opacity_selected(p))
            opacity_group.addAction(act)
            opacity_menu.addAction(act)

        # --- עוצמת קול (צלילי דאוס - mute/dictation/screenshot וכו') ---
        volume_menu = menu.addMenu("עוצמת קול")
        volume_group = QActionGroup(self)
        volume_group.setExclusive(True)
        current_volume = self.config.get("sounds", {}).get("volume", 100)
        for percent in _VOLUME_PRESETS:
            label = "מושתק (0%)" if percent == 0 else f"{percent}%"
            act = QAction(label, self)
            act.setCheckable(True)
            act.setChecked(percent == current_volume)
            act.triggered.connect(lambda checked, p=percent: self._on_volume_selected(p))
            volume_group.addAction(act)
            volume_menu.addAction(act)

        menu.addSeparator()

        # --- אתרים (פתיחה מהירה + ניהול, גם עבור פקודת "דאוס פתח אתר") ---
        from ai_engine.commands import get_sites
        sites_menu = menu.addMenu("פתח אתר")
        for name, url in get_sites(self.config).items():
            act = QAction(name, self)
            act.triggered.connect(lambda checked, u=url: __import__("webbrowser").open(u))
            sites_menu.addAction(act)
        sites_menu.addSeparator()
        manage_sites_action = QAction("ניהול אתרים...", self)
        manage_sites_action.triggered.connect(self._on_manage_sites)
        sites_menu.addAction(manage_sites_action)

        # --- ניקוי זיכרון (אופציות + החרגות) ---
        ram_menu = menu.addMenu("ניקוי זיכרון")
        ram_cfg = self.config.get("ram_clean", {})

        self._ram_close_camera_action = QAction("סגור מצלמה בזמן ניקוי", self)
        self._ram_close_camera_action.setCheckable(True)
        self._ram_close_camera_action.setChecked(ram_cfg.get("close_camera", False))
        self._ram_close_camera_action.triggered.connect(self._on_ram_close_camera_toggled)
        ram_menu.addAction(self._ram_close_camera_action)

        self._ram_close_self_action = QAction("כלול את דאוס עצמו בניקוי (ייסגר אחרון)", self)
        self._ram_close_self_action.setCheckable(True)
        self._ram_close_self_action.setChecked(ram_cfg.get("close_self", True))
        self._ram_close_self_action.triggered.connect(self._on_ram_close_self_toggled)
        ram_menu.addAction(self._ram_close_self_action)

        ram_exclude_action = QAction("ניהול חריגים (אפליקציות שלא ינוקו)...", self)
        ram_exclude_action.triggered.connect(self._on_manage_ram_excludes)
        ram_menu.addAction(ram_exclude_action)

        run_ram_clean_action = QAction("נקה זיכרון עכשיו", self)
        run_ram_clean_action.triggered.connect(self._on_run_ram_clean_now)
        ram_menu.addAction(run_ram_clean_action)

        # --- טיימר כיבוי מחשב (תפריט בלבד - לא פקודה קולית) ---
        shutdown_menu = menu.addMenu("טיימר כיבוי מחשב")
        for label, minutes in [("30 דקות", 30), ("60 דקות", 60),
                                ("90 דקות", 90), ("120 דקות", 120)]:
            act = QAction(label, self)
            act.triggered.connect(lambda checked, m=minutes: self._on_shutdown_timer_selected(m))
            shutdown_menu.addAction(act)
        shutdown_menu.addSeparator()
        custom_shutdown_action = QAction("זמן מותאם אישית (דקות)...", self)
        custom_shutdown_action.triggered.connect(self._on_shutdown_timer_custom)
        shutdown_menu.addAction(custom_shutdown_action)
        cancel_shutdown_action = QAction("בטל טיימר כיבוי", self)
        cancel_shutdown_action.triggered.connect(self._on_shutdown_timer_cancel)
        shutdown_menu.addAction(cancel_shutdown_action)
        shutdown_menu.addSeparator()
        # ברירת המחדל עבור פקודה קולית "דאוס כיבוי מחשב" (שונה מ"דאוס
        # כיבוי", שסוגר רק את דאוס) - כמה דקות עד הכיבוי בפועל.
        default_shutdown_voice_action = QAction("קבע ברירת מחדל לכיבוי בקול (דקות)...", self)
        default_shutdown_voice_action.triggered.connect(self._on_shutdown_voice_default_custom)
        shutdown_menu.addAction(default_shutdown_voice_action)

        # --- מצב חסכון (Whisper small, CPU בלבד) ---
        # שני פריטים נפרדים בכוונה: "עכשיו" משנה את המצב *מיידית*
        # (בדיוק כמו הפקודות הקוליות "דאוס מצב חסכון"/"תחזור" או
        # Ctrl+Alt+P), בעוד "הפעל תמיד" רק קובע איך דאוס יתחיל
        # *בפעם הבאה* - שני מושגים שונים, לא קשורים זה לזה אוטומטית.
        self._economy_mode_now_action = QAction("מצב חסכון (עכשיו)", self)
        self._economy_mode_now_action.setCheckable(True)
        self._economy_mode_now_action.setChecked(
            self.config.get("whisper_start_in_economy_mode", False)
        )
        self._economy_mode_now_action.triggered.connect(
            lambda checked: self.economy_mode_now_toggle_requested.emit(checked)
        )
        menu.addAction(self._economy_mode_now_action)

        self._economy_mode_start_action = QAction(
            "הפעל תמיד במצב חסכון (Whisper small, CPU בלבד)", self
        )
        self._economy_mode_start_action.setCheckable(True)
        self._economy_mode_start_action.setChecked(
            self.config.get("whisper_start_in_economy_mode", False)
        )
        self._economy_mode_start_action.triggered.connect(
            lambda checked: self.economy_mode_start_toggle_requested.emit(checked)
        )
        menu.addAction(self._economy_mode_start_action)

        # --- מיקרופון בהקלטת מסך (ברירת מחדל: מופעל) ---
        self._screen_record_mic_action = QAction("כלול מיקרופון בהקלטת מסך", self)
        self._screen_record_mic_action.setCheckable(True)
        self._screen_record_mic_action.setChecked(
            self.config.get("screen_record", {}).get("include_mic", True)
        )
        self._screen_record_mic_action.triggered.connect(self._on_screen_record_mic_toggled)
        menu.addAction(self._screen_record_mic_action)

        # --- הקראת תרגום (קול) ---
        # שימו לב: מסומן "(לא עובד)" בכוונה זמנית - יש תקלה ידועה
        # בהקראה בפועל (pyttsx3), עדיין בבדיקה. הבועה עם התרגום עדיין
        # מוצגת כרגיל גם בלי הקראה.
        self._translate_speak_action = QAction("הקרא תרגומים בקול (לא עובד)", self)
        self._translate_speak_action.setCheckable(True)
        self._translate_speak_action.setChecked(
            self.config.get("translate", {}).get("speak", True)
        )
        self._translate_speak_action.triggered.connect(
            lambda checked: self.translate_speak_toggle_requested.emit(checked)
        )
        menu.addAction(self._translate_speak_action)

        menu.addSeparator()

        # --- העלם את דאוס אוטומטית (כשהעכבר מתקרב) ---
        # מחליף את "לחיצה דרך הגיף" הישנה (בוליאני קבוע) - כשמופעל,
        # דאוס נעלם *דינמית* כשהעכבר מתקרב אליו (אפשר ללחוץ שם בחופשיות
        # על מה שמתחת), ומופיע שוב כשהעכבר מתרחק. ראו _check_cursor_proximity.
        auto_hide_action = QAction("העלם את דאוס אוטומטית (כשהעכבר מתקרב)", self)
        auto_hide_action.setCheckable(True)
        auto_hide_action.setChecked(
            self.config.get("window", {}).get("auto_hide_near_cursor", False)
        )
        auto_hide_action.triggered.connect(self._on_auto_hide_near_cursor_toggled)
        menu.addAction(auto_hide_action)

        menu.addSeparator()

        self._mute_action = QAction("השתק האזנה", self)
        self._mute_action.setCheckable(True)
        self._mute_action.triggered.connect(
            lambda checked: self.mute_toggle_requested.emit()
        )
        menu.addAction(self._mute_action)

        self._autostart_action = QAction("הפעלה אוטומטית עם Windows", self)
        self._autostart_action.setCheckable(True)
        self._autostart_action.triggered.connect(
            lambda checked: self.autostart_toggle_requested.emit(checked)
        )
        menu.addAction(self._autostart_action)

        self._logging_action = QAction("הפעל לוגים מפורטים", self)
        self._logging_action.setCheckable(True)
        self._logging_action.triggered.connect(
            lambda checked: self.logging_toggle_requested.emit(checked)
        )
        menu.addAction(self._logging_action)

        open_log_action = QAction("פתח קובץ לוג", self)
        open_log_action.triggered.connect(self.open_log_requested.emit)
        menu.addAction(open_log_action)

        menu.addSeparator()

        self._auto_speech_action = QAction("דיבור אוטומטי (לחיצה על כפתור השיחה)", self)
        self._auto_speech_action.setCheckable(True)
        self._auto_speech_action.setChecked(
            self.config.get("ai_engine", {}).get("auto_speech", True)
        )
        self._auto_speech_action.triggered.connect(
            lambda checked: self.auto_speech_toggle_requested.emit(checked)
        )
        menu.addAction(self._auto_speech_action)

        hotkey_action = QAction("שנה קיצור הפעלה ל-AI...", self)
        hotkey_action.triggered.connect(self._on_change_hotkey)
        menu.addAction(hotkey_action)

        apps_action = QAction("ניהול אפליקציות (לפקודת \"פתח את\")...", self)
        apps_action.triggered.connect(self._on_manage_apps)
        menu.addAction(apps_action)

        scripts_action = QAction("ניהול סקריפטים (לפקודת \"הפעל סקריפט\")...", self)
        scripts_action.triggered.connect(self._on_manage_scripts)
        menu.addAction(scripts_action)

        macros_action = QAction("ניהול מאקרואים...", self)
        macros_action.triggered.connect(self._on_manage_macros)
        menu.addAction(macros_action)

        guide_action = QAction("מדריך הפעלה", self)
        guide_action.triggered.connect(self._on_show_guide)
        menu.addAction(guide_action)

        commands_guide_action = QAction("מדריך פקודות", self)
        commands_guide_action.triggered.connect(self._on_show_commands_guide)
        menu.addAction(commands_guide_action)

        menu.addSeparator()

        quit_action = QAction("סגור", self)
        quit_action.triggered.connect(self.quit_requested.emit)
        menu.addAction(quit_action)

        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._on_tray_activated)
        self.tray.show()

    def _on_tray_activated(self, reason):
        if reason == QSystemTrayIcon.Trigger:
            self._toggle_visible()

    def set_mute_checked(self, muted: bool):
        if self._mute_action:
            self._mute_action.setChecked(muted)

    def set_autostart_checked(self, enabled: bool):
        if self._autostart_action:
            self._autostart_action.setChecked(enabled)

    def set_logging_checked(self, enabled: bool):
        if self._logging_action:
            self._logging_action.setChecked(enabled)

    def set_auto_speech_checked(self, enabled: bool):
        if self._auto_speech_action:
            self._auto_speech_action.setChecked(enabled)

    def set_economy_mode_now_checked(self, enabled: bool):
        """מסנכרן את תיבת הסימון של 'מצב חסכון (עכשיו)' עם המצב
        האמיתי בפועל - חשוב במיוחד כי המצב האמיתי עשוי להיקבע
        אוטומטית (זיהוי GPU חלש, ראו speech/wake_word.py::_has_strong_gpu)
        ולא רק לפי מה שהיה שמור בקונפיג בזמן בניית התפריט."""
        if self._economy_mode_now_action:
            self._economy_mode_now_action.setChecked(enabled)

    def _toggle_visible(self):
        new_visible = not self.isVisible()
        self.setVisible(new_visible)
        self._user_hidden = not new_visible

    # ------------------------------------------------------------------ #
    # גודל / שקיפות / לחיצה-דרך / קיצור / מדריך
    # ------------------------------------------------------------------ #

    def _on_size_selected(self, size: int):
        self.resize(size, size)
        self.size_change_requested.emit(size)

    def _on_opacity_selected(self, percent: int):
        value = percent / 100.0
        self.setWindowOpacity(value)
        self.opacity_change_requested.emit(value)

    def _on_volume_selected(self, percent: int):
        self.config.setdefault("sounds", {})["volume"] = percent
        self.volume_change_requested.emit(percent)

    def _on_auto_hide_near_cursor_toggled(self, checked: bool):
        self._cursor_auto_hide_enabled = checked
        if checked:
            self._cursor_proximity_timer.start()
        else:
            self._cursor_proximity_timer.stop()
            # אם דאוס היה מוסתר כרגע בגלל קרבת העכבר - מציגים אותו
            # בחזרה מיד כשהאופציה כובה, כדי לא להישאר "תקוע" מוסתר.
            if self._cursor_hide_active:
                self._cursor_hide_active = False
                self.show()
        self.auto_hide_near_cursor_toggle_requested.emit(checked)

    def _on_screen_record_mic_toggled(self, checked: bool):
        screen_record_cfg = self.config.setdefault("screen_record", {})
        screen_record_cfg["include_mic"] = checked
        self.screen_record_mic_toggle_requested.emit(checked)

    def _on_shutdown_voice_default_custom(self):
        current = self.config.get("shutdown_timer", {}).get("voice_default_minutes", 10)
        minutes, ok = QInputDialog.getInt(
            self, "ברירת מחדל לכיבוי מחשב בקול",
            'כמה דקות אחרי "דאוס כיבוי מחשב" עד שהמחשב באמת יכבה:',
            value=current, minValue=1, maxValue=1440,
        )
        if ok:
            self.shutdown_voice_default_requested.emit(minutes)

    def _on_change_hotkey(self):
        current = self.config.get("ai_engine", {}).get("trigger_hotkey", "alt+g")
        text, ok = QInputDialog.getText(
            self,
            "שינוי קיצור הפעלה",
            "הזן קיצור מקשים לפתיחה/הפעלה של ה-AI (לדוגמה: alt+g):",
            text=current,
        )
        if ok and text.strip():
            new_hotkey = text.strip().lower()
            self.config.setdefault("ai_engine", {})["trigger_hotkey"] = new_hotkey
            self.hotkey_change_requested.emit(new_hotkey)

    def _on_manage_apps(self):
        dialog = AppsDialog(self.config.get("apps", {}), self.log, self)
        dialog.apps_saved.connect(self._on_apps_saved)
        dialog.exec()

    def _on_apps_saved(self, apps: dict):
        self.config["apps"] = apps
        self.apps_changed.emit(apps)

    def _on_manage_scripts(self):
        dialog = ScriptsDialog(self.config.get("scripts", {}), self.log, self)
        dialog.scripts_saved.connect(self._on_scripts_saved)
        dialog.exec()

    def _on_scripts_saved(self, scripts: dict):
        self.config["scripts"] = scripts
        self.scripts_changed.emit(scripts)

    def _on_manage_macros(self):
        dialog = MacrosDialog(self.config.get("macros", {}), self.log, self)
        dialog.macros_saved.connect(self._on_macros_saved)
        dialog.exec()

    def _on_macros_saved(self, macros: dict):
        self.config["macros"] = macros
        self.macros_changed.emit(macros)

    def _on_manage_sites(self):
        from ai_engine.commands import get_sites
        dialog = SitesDialog(get_sites(self.config), self.log, self)
        dialog.sites_saved.connect(self._on_sites_saved)
        dialog.exec()

    def _on_sites_saved(self, sites: dict):
        self.config["sites"] = sites
        self.sites_changed.emit(sites)

    def _on_ram_close_camera_toggled(self, checked: bool):
        ram_cfg = self.config.setdefault("ram_clean", {})
        ram_cfg["close_camera"] = checked
        self.ram_clean_settings_changed.emit(dict(ram_cfg))

    def _on_ram_close_self_toggled(self, checked: bool):
        ram_cfg = self.config.setdefault("ram_clean", {})
        ram_cfg["close_self"] = checked
        self.ram_clean_settings_changed.emit(dict(ram_cfg))

    def _on_manage_ram_excludes(self):
        ram_cfg = self.config.get("ram_clean", {})
        dialog = RamCleanDialog(ram_cfg.get("exclude_processes", []), self.log, self)
        dialog.excludes_saved.connect(self._on_ram_excludes_saved)
        dialog.exec()

    def _on_ram_excludes_saved(self, excludes: list):
        ram_cfg = self.config.setdefault("ram_clean", {})
        ram_cfg["exclude_processes"] = excludes
        self.ram_clean_settings_changed.emit(dict(ram_cfg))

    def _on_run_ram_clean_now(self):
        try:
            from ai_engine.ram_cleaner import clean_ram
            clean_ram(self.config, self.log)
        except Exception:
            self.log.exception("נכשל בהרצת ניקוי הזיכרון מתפריט המגש")

    def _on_shutdown_timer_selected(self, minutes: int):
        self.shutdown_timer_requested.emit(minutes)

    def _on_shutdown_timer_custom(self):
        minutes, ok = QInputDialog.getInt(
            self, "טיימר כיבוי מחשב", "בעוד כמה דקות לכבות את המחשב:",
            value=60, minValue=1, maxValue=1440,
        )
        if ok:
            self.shutdown_timer_requested.emit(minutes)

    def _on_shutdown_timer_cancel(self):
        self.shutdown_timer_cancel_requested.emit()

    def _on_show_guide(self):
        # parent=None בכוונה (לא self) - self הוא החלון השקוף, ללא
        # מסגרת, שתמיד-על-הכל (WindowStaysOnTopHint) - דיאלוג שיורש
        # התנהגות כזו "נתקע" מעל הכל כולל תפריט המגש עצמו, ואי אפשר
        # לסגור אותו/לראות דברים מתחתיו כמו שקרה בבאג המקורי. כחלון
        # עצמאי רגיל (עם מסגרת/כפתור סגירה תקין) הוא מתנהג נורמלי.
        box = QMessageBox(None)
        box.setWindowTitle("מדריך הפעלה - Deus")
        box.setTextFormat(Qt.RichText)
        box.setText(_SETUP_GUIDE_HTML)
        box.setStandardButtons(QMessageBox.Ok)
        box.exec()

    def _on_show_commands_guide(self):
        box = QMessageBox(None)  # ראו הערה ב-_on_show_guide
        box.setWindowTitle("מדריך פקודות - Deus")
        box.setTextFormat(Qt.RichText)
        box.setText(_COMMANDS_GUIDE_HTML)
        box.setStandardButtons(QMessageBox.Ok)
        box.exec()
