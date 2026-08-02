"""
הקלטה, שמירה, ושחזור (הפעלה) של מאקרואים - רצפים של פעולות עכבר
ומקלדת אמיתיות שהמשתמש מבצע, שנשמרים תחת שם (בעברית) ואפשר להפעיל
מאוחר יותר בפקודה קולית.

זרימת השימוש (דרך פקודות קוליות, מטופל ב-speech/wake_word.py):
  1. "דאוס תלמד מאקרו <שם>"  -> MacroRecorder.start()
  2. ...המשתמש מבצע פעולות עכבר/מקלדת כרגיל...
  3. "דאוס עצור"              -> MacroRecorder.stop() ושמירה תחת <שם>
  4. "דאוס תפעיל מקרו <שם>"   -> MacroPlayer.play(events, repeat)
     ("דאוס עצור" תוך כדי ריצה עוצר גם הפעלה שרצה ברגע זה)

תלויות: בנוסף לחבילת keyboard שכבר קיימת בפרויקט, נדרשת גם חבילת
mouse (pip install mouse) כדי להקליט/לשחזר תזוזות ולחיצות עכבר.
אם mouse לא מותקן - הקלטה/הפעלה עדיין יעבדו, אבל רק עבור מקלדת
(אירועי עכבר פשוט לא נאספים/מבוצעים, בלי לקרוס).
"""

import logging
import threading
import time

try:
    import keyboard
    _HAS_KEYBOARD = True
except ImportError:
    _HAS_KEYBOARD = False

try:
    import mouse
    _HAS_MOUSE = True
except ImportError:
    _HAS_MOUSE = False

_log = logging.getLogger("deus")

# חוסם פערי המתנה חריגים בין אירועים בזמן שחזור (למשל אם המשתמש עצר
# להפסקת קפה באמצע ההקלטה) - כדי שהפעלת המאקרו לא "תיתקע" לדקות.
_MAX_STEP_DELAY_SEC = 5.0


class MacroRecorder:
    """מקליט אירועי מקלדת ועכבר עם חותמת זמן יחסית, עד קריאה ל-stop()."""

    def __init__(self, logger: logging.Logger = None):
        self.log = logger or _log
        self._recording = False
        self._events = []
        self._start_time = None
        self._kb_hook = None
        self._mouse_hook = None

    def is_recording(self) -> bool:
        return self._recording

    def start(self):
        if self._recording:
            return
        self._events = []
        self._start_time = time.time()
        self._recording = True

        if _HAS_KEYBOARD:
            self._kb_hook = keyboard.hook(self._on_keyboard_event)
        else:
            self.log.warning("חבילת keyboard לא מותקנת - אירועי מקלדת לא יוקלטו")

        if _HAS_MOUSE:
            self._mouse_hook = mouse.hook(self._on_mouse_event)
        else:
            self.log.warning("חבילת mouse לא מותקנת - אירועי עכבר לא יוקלטו")

        self.log.info("התחלת הקלטת מאקרו")

    def _on_keyboard_event(self, event):
        # מתעלמים מהאירועים של מילת ההפעלה/פקודת העצירה עצמן היה מסובך
        # לסנן באופן אמין (הן נאמרות בקול, לא מוקלדות) - לכן לא נדרש טיפול
        # מיוחד כאן: אירועי מקלדת שנרשמים הם רק הקשות מקלדת אמיתיות.
        self._events.append({
            "t": time.time() - self._start_time,
            "kind": "key",
            "event_type": event.event_type,  # "down" / "up"
            "name": event.name,
        })

    def _on_mouse_event(self, event):
        t = time.time() - self._start_time
        if isinstance(event, mouse.MoveEvent):
            self._events.append({"t": t, "kind": "move", "x": event.x, "y": event.y})
        elif isinstance(event, mouse.ButtonEvent):
            self._events.append({
                "t": t, "kind": "button",
                "button": event.button, "event_type": event.event_type,
            })
        elif isinstance(event, mouse.WheelEvent):
            self._events.append({"t": t, "kind": "wheel", "delta": event.delta})

    def stop(self) -> list:
        """עוצר את ההקלטה ומחזיר את רשימת האירועים שנאספה (רשימה ריקה
        אם לא הייתה הקלטה פעילה)."""
        if not self._recording:
            return []
        self._recording = False

        if self._kb_hook is not None:
            try:
                keyboard.unhook(self._kb_hook)
            except Exception:
                pass
            self._kb_hook = None

        if self._mouse_hook is not None:
            try:
                mouse.unhook(self._mouse_hook)
            except Exception:
                pass
            self._mouse_hook = None

        self.log.info("הקלטת מאקרו הסתיימה - %d אירועים", len(self._events))
        return self._events


class MacroPlayer:
    """מפעיל (משחזר) מאקרו שהוקלט. הריצה מתבצעת ב-thread נפרד כדי לא
    לחסום את ת'רד הרקע של זיהוי הדיבור - כך ש"דאוס עצור" יכול להתקבל
    ולעצור ריצה ארוכה/אינסופית תוך כדי שהיא רצה."""

    def __init__(self, logger: logging.Logger = None):
        self.log = logger or _log
        self._stop_flag = threading.Event()
        self._thread = None
        self._playing = False

    def is_playing(self) -> bool:
        return self._playing

    def stop(self):
        self._stop_flag.set()

    def play(self, events: list, repeat=1):
        """מפעיל ברקע. repeat: מספר חזרות, או 0/None/שלילי = אינסופי
        (עד קריאה ל-stop())."""
        if self._playing:
            self.log.warning("מאקרו כבר רץ - מתעלם מבקשת הפעלה נוספת")
            return
        if not events:
            self.log.warning("אין אירועים במאקרו - אין מה להפעיל")
            return

        self._stop_flag.clear()
        self._thread = threading.Thread(target=self._run, args=(events, repeat), daemon=True)
        self._thread.start()

    def _run(self, events, repeat):
        self._playing = True
        try:
            count = 0
            infinite = repeat is None or repeat <= 0
            while (infinite or count < repeat) and not self._stop_flag.is_set():
                self._play_once(events)
                count += 1
            self.log.info("סיום הפעלת המאקרו (%d חזרות בוצעו)", count)
        finally:
            self._playing = False

    def _play_once(self, events):
        last_t = 0.0
        for ev in events:
            if self._stop_flag.is_set():
                return
            delay = ev["t"] - last_t
            # רצפת השהיה מינימלית לפני *לחיצת* עכבר (לא תזוזה): נצפה
            # בפועל שלחיצות בהמשך רצף ארוך (למשל המקום ה-4/5 מתוך 5)
            # נטו "לפספס" את המיקום - סביר שה-OS/האפליקציה עדיין צריכים
            # רגע לעבד את שינוי מיקום העכבר/hover לפני שהלחיצה בפועל
            # "נתפסת" נכון, במיוחד כשבהקלטה המקורית התזוזה הפיזית
            # והלחיצה קרו כמעט בו-זמנית (טבעי ביד אנושית אמיתית, אבל
            # פחות אמין בשחזור אוטומטי מיידי). לא נוגעים בעיכובים בין
            # שאר סוגי האירועים - זה היה מאט מאקרואים עם הרבה אירועי
            # תזוזה מוקלטים בלי סיבה טובה.
            if ev.get("kind") == "button" and ev.get("event_type") == "down":
                delay = max(delay, 0.04)
            if delay > 0:
                time.sleep(min(delay, _MAX_STEP_DELAY_SEC))
            last_t = ev["t"]
            self._execute(ev)

    def _execute(self, ev):
        kind = ev.get("kind")
        try:
            if kind == "key" and _HAS_KEYBOARD:
                name = ev["name"]
                if len(name) == 1:
                    # מקש-תו-יחיד (אות/ספרה/סימן) - שולחים את התו עצמו
                    # ישירות (keyboard.write, לא press/release) כדי
                    # שהתוצאה תמיד תהיה התו הנכון, בלי תלות בשפת הקלט
                    # הפעילה כרגע בוינדוס. keyboard.press(name) לוחץ על
                    # *מקש פיזי לפי מיקום* (ממופה פעם אחת לפי מיקום
                    # קבוע, בלי קשר לשפה הפעילה בזמן ההפעלה בפועל) -
                    # זו בדיוק הסיבה שטקסט שהוקלד באנגלית במהלך הקלטה
                    # חזר בעברית בהפעלה: אם שפת הקלט הפעילה בזמן
                    # ההפעלה היא עברית, אותו מקש פיזי (מיקום) מפיק אות
                    # עברית במקום את האות האנגלית שהוקלדה בפועל.
                    # keyboard.write() לעומת זאת בודק את השפה הפעילה
                    # *בזמן אמת*, ואם התו המבוקש לא קיים בה - נופל
                    # אוטומטית להזרקת Unicode (שמפיקה תמיד את התו
                    # המדויק שנשלח, בלי תלות בשפה). מתעלמים מאירוע ה-
                    # "up" המקביל - הקלדת תו כבר שלמה ב-"down" בלבד,
                    # אין צורך ב"שחרור" נפרד לתו יחיד.
                    if ev["event_type"] == "down":
                        keyboard.write(name)
                elif ev["event_type"] == "down":
                    keyboard.press(name)
                else:
                    keyboard.release(name)
            elif kind == "move" and _HAS_MOUSE:
                mouse.move(ev["x"], ev["y"], absolute=True, duration=0)
                # אבחון: מוודאים בפועל שהעכבר הגיע בדיוק ליעד המבוקש -
                # לא רק מניחים שזה הצליח כי לא נזרקה שגיאה. אם בעתיד
                # ידווח שוב על לחיצות שמפספסות את המיקום, זה ייתן ראיה
                # קונקרטית בלוג: האם mouse.move עצמו לא "תפס" (למשל
                # בעיית קואורדינטות/DPI/מסך מרובה), או שהעכבר כן הגיע
                # למקום הנכון וה"פספוס" הוא בפועל בממשק היעד עצמו
                # (שהזיז/שינה תוכן בין ההקלטה להפעלה) - שני מנגנונים
                # שונים לגמרי, עם תיקונים שונים לגמרי.
                try:
                    actual_x, actual_y = mouse.get_position()
                    if abs(actual_x - ev["x"]) > 2 or abs(actual_y - ev["y"]) > 2:
                        self.log.warning(
                            "מאקרו: תזוזת עכבר לא הגיעה בדיוק ליעד - התבקש (%d,%d), בפועל (%d,%d)",
                            ev["x"], ev["y"], actual_x, actual_y,
                        )
                except Exception:
                    pass
            elif kind == "button" and _HAS_MOUSE:
                if ev["event_type"] == "down":
                    try:
                        cur_x, cur_y = mouse.get_position()
                        self.log.debug("מאקרו: לחיצת עכבר בפועל במיקום (%d,%d)", cur_x, cur_y)
                    except Exception:
                        pass
                    mouse.press(button=ev["button"])
                else:
                    mouse.release(button=ev["button"])
            elif kind == "wheel" and _HAS_MOUSE:
                mouse.wheel(ev["delta"])
        except Exception:
            self.log.exception("שגיאה בביצוע אירוע מאקרו: %s", ev)
