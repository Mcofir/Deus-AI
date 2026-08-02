"""
הגדרת לוגים מרכזית.

המטרה: כשמשהו לא עובד (המילה לא זוהתה, המודל לא נטען, Copilot לא נפתח וכו'),
אפשר לפתוח את קובץ הלוג ולראות בדיוק מה קרה - בלי צורך בקונסולה פתוחה.

הערה חשובה (תיקון): הלוגר נקרא כאן "deus" בדיוק כמו בכל שאר הקבצים
(launcher.py, screen_actions.py, autostart.py, ברירת המחדל ב-wake_word.py).
ב-Python, logging.getLogger("deus") תמיד מחזיר את אותו אובייקט singleton
בדיוק, לא משנה מאיזה מודול קוראים לו - כך שכל המודולים "משתפים" בפועל
את אותו לוגר, עם אותו FileHandler שמוגדר כאן. קודם הלוגר כאן נקרא בטעות
"daus" (שם אחר!) - מה שגרם לכך שכל הלוגים מ-launcher.py/screen_actions.py/
autostart.py (שקוראים ל-getLogger("deus")) נכתבו ללוגר *נפרד לגמרי*,
בלי שום handler מחובר אליו - ולכן מעולם לא הגיעו לקובץ הלוג.

מיקום קובץ הלוג: %LOCALAPPDATA%\\Deus\\daus.log (נופל חזרה לתיקיית
האפליקציה אם אין LOCALAPPDATA, למשל בזמן פיתוח על מערכת שאינה Windows).
"""

import logging
import os
import sys


def get_log_path() -> str:
    local_appdata = os.environ.get("LOCALAPPDATA")
    if local_appdata:
        log_dir = os.path.join(local_appdata, "Deus")
    else:
        log_dir = os.path.join(os.path.dirname(os.path.abspath(sys.argv[0])), "logs")

    os.makedirs(log_dir, exist_ok=True)
    return os.path.join(log_dir, "daus.log")


def setup_logging(enabled: bool, level: str = "INFO") -> logging.Logger:
    logger = logging.getLogger("deus")
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    logger.handlers.clear()

    if not enabled:
        logger.addHandler(logging.NullHandler())
        return logger

    log_path = get_log_path()
    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s", datefmt="%H:%M:%S"
    )

    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setFormatter(fmt)
    logger.addHandler(file_handler)

    # כשרצים עם python.exe (לא frozen exe) יש קונסולה - נדפיס גם אליה,
    # נוח לפיתוח. ב-exe עם --windowed אין stdout אז זה פשוט יידחה בשקט.
    try:
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setFormatter(fmt)
        logger.addHandler(console_handler)
    except Exception:  # noqa: BLE001
        pass

    logger.info("=== Deus starting, log file: %s ===", log_path)
    return logger


def set_logging_enabled(logger: logging.Logger, enabled: bool):
    """מאפשר להדליק/לכבות לוגים בזמן ריצה מתוך תפריט המגש, בלי הפעלה מחדש."""
    if enabled and not any(isinstance(h, logging.FileHandler) for h in logger.handlers):
        log_path = get_log_path()
        fmt = logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s: %(message)s", datefmt="%H:%M:%S"
        )
        file_handler = logging.FileHandler(log_path, encoding="utf-8")
        file_handler.setFormatter(fmt)
        logger.addHandler(file_handler)
        logger.info("=== לוגים הופעלו, קובץ: %s ===", log_path)
    elif not enabled:
        for h in list(logger.handlers):
            if isinstance(h, logging.FileHandler):
                logger.info("=== לוגים כבויים ===")
                logger.removeHandler(h)
                h.close()
