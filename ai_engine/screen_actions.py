"""
זיהוי תמונה ולחיצה אוטומטית על אייקונים בחלון ה-AI (למשל כפתור מיקרופון/
שיחה קולית, או כפתור מעבר להקלדה, בג'מיני), כדי לאפשר "דיבור אוטומטי" -
התחלת שיחה קולית בלי לגעת בעכבר, וגם מעבר להקלדה על ידי פקודה קולית.

איך זה עובד:
  1. לוקחים צילום מסך של המסך הראשי.
  2. מחפשים בתוכו את תמונת האייקון (יש קובץ נפרד ל-Dark Mode ול-Light Mode,
     כי הצבעים שונים) בעזרת Template Matching (OpenCV).
  3. אם נמצאה התאמה מעל סף ביטחון מסוים - מזיזים את העכבר ולוחצים
     פיזית במרכז האייקון שנמצא.

שמות הקבצים בתיקיית האייקונים (ברירת מחדל: assets/icons): כל קובץ
ששמו מתחיל ב-icon_name נחשב "וריאציה" של אותו אייקון ונבדק (למשל
עבור "mic": mic_light.png + mic_dark.png; עבור "pause": pause1.png +
pause2.png אם צילמתם כמה מצבים/הקשרים שונים של אותו כפתור) - אין
חובה במוסכמת שמות קבועה (_light/_dark), כל סיומת אחרי השם עובדת.
דוגמאות בשימוש היום:
    mic_light.png, mic_dark.png           - כפתור המיקרופון/שיחה קולית
    keyboard_light.png, keyboard_dark.png - כפתור המעבר להקלדה
    pause1.png, pause2.png                - כפתור ה"פאוז" שמופיע לפעמים
                                             אחרי הלחיצה על המיקרופון
                                             (ראו launcher.py::_try_auto_speech_click)

טיפ לצילום האייקונים: לצלם/לחתוך תמונה צמודה (crop הדוק) רק לכפתור עצמו,
בלי הרבה רקע מיותר סביבו - כך ההתאמה תהיה הרבה יותר מדויקת ומהירה.

אם לא נמצאה התאמה (למשל כי הצ'אט כבר היה במצב שיחה מהפעם הקודמת, ואין
כפתור מיקרופון להציג) - זה בסדר גמור: הפונקציה פשוט מחזירה False,
והלוג מתעד את זה בלי לזרוק שגיאה.

הערה חשובה על נתיבים בעברית:
    cv2.imread לא תומך בנתיבים שיש בהם תווים לא-ASCII (כמו עברית) ב-
    Windows - הוא נכשל *בשקט* ומחזיר None, בלי לזרוק שגיאה (מה שגורם
    ל"קובץ לא נמצא" גם כשהקובץ קיים, רק כי הנתיב הכולל - למשל תיקיית
    הפרויקט - מכיל עברית). לכן במקום cv2.imread משתמשים כאן ב-np.fromfile
    (שכן תומך ב-Unicode) ואז ב-cv2.imdecode, שעוקף את הבעיה הזו לגמרי.

תלויות (לא חובה - אם חסרות, הפיצ'ר פשוט מדלג בשקט):
    pip install opencv-python mss pyautogui numpy
"""

import glob
import logging
import os

try:
    import numpy as np
    import cv2
    import mss
    import pyautogui
    _HAS_VISION = True
except ImportError:
    _HAS_VISION = False

_log = logging.getLogger("deus")


def _imread_unicode(path: str):
    """קורא תמונה מהדיסק בעזרת np.fromfile + cv2.imdecode במקום
    cv2.imread, כדי לתמוך בנתיבים עם תווים לא-ASCII (עברית וכו') -
    cv2.imread נכשל בשקט על נתיבים כאלה ב-Windows ומחזיר None."""
    try:
        data = np.fromfile(path, dtype=np.uint8)
        if data.size == 0:
            return None
        return cv2.imdecode(data, cv2.IMREAD_COLOR)
    except Exception:
        _log.exception("שגיאה בקריאת קובץ התמונה (imdecode): %s", path)
        return None


def _grab_screen(region=None):
    """מצלם את המסך הראשי, או רק אזור מסוים ממנו אם region ניתן
    (left, top, right, bottom - בפיקסלים, כמו שמחזיר win32gui.GetWindowRect).
    ראו click_icon: הגבלת אזור החיפוש (למשל רק לחלון הדפדפן) חשובה
    בעיקר לאייקונים "כלליים" כמו פאוז (שתי פסים אנכיים) - שעלולים
    להתאים בטעות לאלמנטים לא-קשורים במקום אחר במסך (למשל פקדי מדיה
    צפים של Windows עצמו)."""
    with mss.mss() as sct:
        if region is not None:
            left, top, right, bottom = region
            monitor = {"left": left, "top": top, "width": right - left, "height": bottom - top}
        else:
            monitor = sct.monitors[1]  # המסך הראשי
        shot = sct.grab(monitor)
        img = np.array(shot)  # BGRA
        return cv2.cvtColor(img, cv2.COLOR_BGRA2BGR), monitor


def _match_template(screen_bgr, template_path, threshold):
    if not os.path.exists(template_path):
        return None

    template = _imread_unicode(template_path)
    if template is None:
        _log.warning("לא ניתן לקרוא את קובץ האייקון: %s", template_path)
        return None

    th, tw = template.shape[:2]
    if th > screen_bgr.shape[0] or tw > screen_bgr.shape[1]:
        return None

    result = cv2.matchTemplate(screen_bgr, template, cv2.TM_CCOEFF_NORMED)
    _min_val, max_val, _min_loc, max_loc = cv2.minMaxLoc(result)

    if max_val < threshold:
        return None

    center_x = max_loc[0] + tw // 2
    center_y = max_loc[1] + th // 2
    return center_x, center_y, max_val


def click_icon(icon_name: str, config: dict, logger: logging.Logger = None, region=None) -> bool:
    """
    מחפש אייקון בשם icon_name (למשל "mic", "keyboard" או "pause"), בכל
    הגרסאות/וריאציות הקיימות שלו בתיקיית האייקונים (כל קובץ ששמו מתחיל
    ב-icon_name - למשל mic_light.png+mic_dark.png, או pause1.png+
    pause2.png אם יש כמה צילומים של אותו כפתור בהקשרים שונים), ולוחץ
    פיזית במרכז ההתאמה הכי טובה מביניהן אם היא מעל סף הביטחון. מחזיר
    True אם בוצעה לחיצה בפועל.

    region (אופציונלי): (left, top, right, bottom) - מגביל את החיפוש
    לאזור מסוים במסך (למשל רק תוך גבולות חלון הדפדפן) במקום המסך כולו.
    **חשוב במיוחד לאייקונים "כלליים" כמו פאוז** (שתי פסים אנכיים בריבוע
    מעוגל) - נבדק בפועל: אייקון כזה יכול להתאים בטעות (התאמה מושלמת,
    ציון 1.00!) לאלמנטים לא-קשורים בכלל במסך, למשל פקד המדיה הצף
    שווינדוס עצמו מציג כשמנוגן שמע/וידאו - מה שעלול לגרום ללחיצה
    "לחפש נכון" אבל *ללחוץ במקום הלא נכון לגמרי* (ולפגוע בטעות במשהו
    אחר שרץ במחשב, כמו לעצור מוזיקה שמתנגנת). בלי region, ההתאמה
    נבדקת מול המסך *כולו*.
    """
    log = logger or _log

    if not _HAS_VISION:
        log.warning(
            "זיהוי תמונה לא זמין (חסרות חבילות opencv-python / mss / pyautogui / "
            "numpy) - מדלג על לחיצה אוטומטית על '%s'. אפשר להתקין עם: "
            "pip install opencv-python mss pyautogui numpy", icon_name
        )
        return False

    cop_cfg = config.get("ai_engine", {})
    icons_dir = cop_cfg.get("icons_dir", "assets/icons")
    threshold = cop_cfg.get("icon_confidence_threshold", 0.8)

    base_dir = config.get("_base_dir", "")
    icons_dir_abs = icons_dir if os.path.isabs(icons_dir) else os.path.join(base_dir, icons_dir)

    candidates = sorted(glob.glob(os.path.join(icons_dir_abs, f"{icon_name}*.png")))

    try:
        screen_bgr, monitor = _grab_screen(region)
    except Exception:
        log.exception("נכשל בצילום מסך לצורך זיהוי אייקון '%s'", icon_name)
        return False

    best = None
    for path in candidates:
        match = _match_template(screen_bgr, path, threshold)
        if match and (best is None or match[2] > best[2]):
            best = match

    if best is None:
        log.info(
            "לא נמצא אייקון '%s' על המסך (סף=%.2f). ייתכן שהחלון עדיין נטען, "
            "או שהצ'אט כבר במצב הזה מהפעם הקודמת - וזה בסדר.", icon_name, threshold
        )
        return False

    x, y, score = best
    x += monitor["left"]
    y += monitor["top"]

    try:
        pyautogui.moveTo(x, y)
        pyautogui.click(x, y)
        log.info("נלחץ אייקון '%s' במיקום (%d, %d), ציון התאמה=%.2f", icon_name, x, y, score)
        return True
    except Exception:
        log.exception("נכשל בלחיצה על אייקון '%s'", icon_name)
        return False
