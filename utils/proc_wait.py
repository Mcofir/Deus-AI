"""
המתנה אדפטיבית לכך שתהליך "סיים לעלות" (למשל אחרי הרצת תוכנה חדשה),
במקום שינה קבועה שרק *מנחשת* כמה זמן זה בערך לוקח.

**למה זה קיים** (נבדק בפועל, בשני הקשרים שונים - PowerShell/Claude
Code ופתיחת Chrome מאפס): זמן עליית תהליך משתנה בין מחשבים שונים
(עומס מערכת, מהירות דיסק, כמות תוספים/פרופיל שנטען וכו') - שינה קבועה
שמספיקה במחשב אחד עלולה להיות קצרה מדי במחשב אחר, וגם "לבזבז" זמן
מיותר במחשב מהיר. הפתרון: עוקבים בפועל אחרי צריכת ה-CPU של התהליך -
כל עוד הוא "עובד" (טוען, מריץ סקריפטים וכו') הוא צורך CPU מדיד; ברגע
שהוא מגיע למצב "מוכן" (למשל prompt אינטראקטיבי, או שסיים לרשום את
עצמו) צריכת ה-CPU צונחת כמעט לאפס.

**חשוב - נבדק בפועל**: מדידת CPU בודדת "נמוכה" לא מספיקה - נצפו
ריצות אמיתיות שבהן טעינה (למשל פרופיל PowerShell עם סביבת conda)
עושה "התפרצויות" קצרות של CPU עם רווחים שקטים *באמצע* הטעינה עצמה,
לא רק בסופה. ברירת המחדל (5 מדידות רצופות "שקטות", כל 0.25 שניות =
1.25 שניות רצופות של רוגע) נבדקה בפועל שעוקפת נכון את ה"פערים
המטעים" האלה ומזהה את הסיום האמיתי.
"""

import logging
import time

try:
    import psutil
    _HAS_PSUTIL = True
except ImportError:
    _HAS_PSUTIL = False

_log = logging.getLogger("deus")


def wait_for_process_idle(pid: int, log: logging.Logger = None, max_wait_sec: float = 10.0,
                           poll_interval: float = 0.25, idle_threshold: float = 5.0,
                           consecutive_needed: int = 5) -> bool:
    """ממתין עד שהתהליך pid נראה "רגוע" (CPU נמוך באופן עקבי) - סימן
    שהוא סיים לטעון ומחכה בפועל, במקום שינה קבועה. מחזיר True אם
    זוהתה "רגיעה" מספקת בתוך max_wait_sec, False אם לא (ואז הקוד
    הקורא אמור ליפול לגיבוי - שינה קבועה) או אם psutil לא מותקן."""
    log = log or _log
    if not _HAS_PSUTIL:
        return False
    try:
        proc = psutil.Process(pid)
        proc.cpu_percent(interval=None)  # קריאה ראשונה - "מכינה" את המדידה הבאה, לא מדויקת בעצמה
    except Exception:
        return False

    consecutive_idle = 0
    deadline = time.time() + max_wait_sec
    while time.time() < deadline:
        try:
            cpu = proc.cpu_percent(interval=poll_interval)
        except Exception:
            return False
        if cpu < idle_threshold:
            consecutive_idle += 1
            if consecutive_idle >= consecutive_needed:
                log.debug("תהליך %d נראה רגוע (CPU נמוך) - ממשיך", pid)
                return True
        else:
            consecutive_idle = 0
    return False
