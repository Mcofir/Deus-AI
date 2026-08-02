"""
"דאוס קליקר" - לוחץ אוטומטית (לחיצה שמאלית) במיקום העכבר הנוכחי כל
חצי שנייה, עד "דאוס עצור" (פקודת העצירה הגנרית - ראו speech/wake_word.py,
בדיוק כמו עם תמלול/מאקרו/הקלטה).

מנסה להשתמש בחבילת mouse (כבר תלות אופציונלית קיימת בפרויקט דרך
ai_engine/macros.py) ואם היא לא מותקנת נופל לחבילת pyautogui (גם היא
כבר תלות אופציונלית קיימת דרך ai_engine/screen_actions.py). אם אף
אחת מהן לא מותקנת - מדלג בשקט עם אזהרה בלוג, בלי לקרוס.
"""

import logging
import threading
import time

try:
    import mouse
    _HAS_MOUSE = True
except ImportError:
    _HAS_MOUSE = False

try:
    import pyautogui
    _HAS_PYAUTOGUI = True
except ImportError:
    _HAS_PYAUTOGUI = False

_log = logging.getLogger("deus")

_DEFAULT_INTERVAL_SEC = 0.5


class ClickerLoop:
    """לוחץ ברקע במיקום הנוכחי של העכבר כל interval_sec שניות, עד stop()."""

    def __init__(self, logger: logging.Logger = None):
        self.log = logger or _log
        self._stop_flag = threading.Event()
        self._thread = None
        self._running = False

    def is_running(self) -> bool:
        return self._running

    def start(self, interval_sec: float = _DEFAULT_INTERVAL_SEC):
        if self._running:
            return
        if not _HAS_MOUSE and not _HAS_PYAUTOGUI:
            self.log.warning(
                "אין חבילת mouse או pyautogui מותקנת - לא ניתן להפעיל קליקר. "
                "אפשר להתקין עם: pip install mouse (או pyautogui)"
            )
            return

        self._stop_flag.clear()
        self._thread = threading.Thread(
            target=self._run, args=(interval_sec,), daemon=True
        )
        self._thread.start()

    def stop(self):
        self._stop_flag.set()

    def _click_once(self):
        try:
            if _HAS_MOUSE:
                mouse.click(button="left")
            elif _HAS_PYAUTOGUI:
                pyautogui.click()
        except Exception:
            self.log.exception("שגיאה בביצוע לחיצת קליקר")

    def _run(self, interval_sec: float):
        self._running = True
        count = 0
        try:
            self.log.info("התחלת קליקר (כל %.2f שניות)", interval_sec)
            while not self._stop_flag.is_set():
                self._click_once()
                count += 1
                self._stop_flag.wait(interval_sec)
        finally:
            self._running = False
            self.log.info("קליקר נעצר (%d לחיצות בוצעו)", count)
