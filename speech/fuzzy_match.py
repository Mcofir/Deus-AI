"""
עזרי נרמול והתאמה מקורבת (fuzzy) לזיהוי מילת ההפעלה "Deus".

וויספר (וכל STT) לא תמיד יתמלל את "דאוס"/"Deus" בצורה מדויקת. לפעמים
הוא כותב אותה בעברית ("דאוז", "דיוס", "דא וס"...), ולפעמים - כי זו מילה
שנשמעת "לועזית" - הוא כותב אותה באותיות לטיניות כמו "Deus" או "Daus".
לכן:
  1. מנרמלים את הטקסט: מסירים ניקוד וסימני פיסוק, אבל משאירים גם אותיות
     עבריות וגם אותיות לטיניות (במקום לזרוק את הלטיניות כמו שהיה קודם),
     כדי לא לפספס תמלול באנגלית של המילה.
  2. משווים במרחק עריכה (Levenshtein) יחסי, כדי לתפוס טעויות קטנות.
"""

import re
import unicodedata

try:
    from rapidfuzz import fuzz
    _HAS_RAPIDFUZZ = True
except ImportError:
    _HAS_RAPIDFUZZ = False
    import difflib

# תווי ניקוד עברי (U+0591-U+05C7)
_NIQQUD_RE = re.compile(r"[\u0591-\u05C7]")
# גרש/גרשיים בעברית (כמו ב-"צ'אט", "ג'ינס") ומרכאות/אפוסטרוף לטיניים -
# מוסרים *לגמרי* (בלי רווח במקום!) לפני שאר הנרמול. אחרת "צ'ת" היה
# הופך ל-"צ ת" (שתי "מילים" נפרדות) בגלל ה-regex הכללי למטה שממיר כל
# תו לא-עברי/לטיני לרווח - מה שגרם להתאמות פקודה/מילת הפעלה עם גרש
# להיכשל אוטומטית (מספר המילים כבר לא תואם את מה שנאמר בפועל).
_APOSTROPHE_RE = re.compile(r"[\u05F3\u2018\u2019'`]")
# משאירים רק אותיות עבריות, אותיות לטיניות ורווחים
_NON_HEBREW_OR_LATIN_RE = re.compile(r"[^\u05D0-\u05EAa-z\s]")


def normalize_text(text: str) -> str:
    """מסיר ניקוד וסימני פיסוק, הופך אותיות לטיניות לאותיות קטנות,
    ומצמצם רווחים. שומר גם עברית וגם לטינית. מסיר גרש/אפוסטרוף בלי
    להפוך אותם לרווח (כדי ש"צ'אט" יישאר מילה אחת: "צאט")."""
    if not text:
        return ""
    text = unicodedata.normalize("NFC", text)
    text = _NIQQUD_RE.sub("", text)
    text = text.lower()
    text = _APOSTROPHE_RE.sub("", text)
    text = _NON_HEBREW_OR_LATIN_RE.sub(" ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


# שם ישן, לתאימות לאחור עם קוד קיים שקורא לפונקציה בשם הזה
normalize_hebrew = normalize_text


def similarity(a: str, b: str) -> float:
    """מחזיר ציון דמיון בין 0 ל-1 בין שתי מחרוזות מנורמלות."""
    a, b = normalize_text(a), normalize_text(b)
    if not a or not b:
        return 0.0
    if _HAS_RAPIDFUZZ:
        return fuzz.ratio(a, b) / 100.0
    return difflib.SequenceMatcher(None, a, b).ratio()


def contains_wake_word(transcript: str, wake_words: list[str], threshold: float = 0.72):
    """
    בודק אם התמלול מכיל אחת ממילות ההפעלה (או פקודות אחרות כמו "הקלדה"),
    גם אם לא בצורה מדויקת, וגם אם וויספר תימלל אותה באותיות לטיניות
    ("Deus"/"Daus") במקום בעברית.
    בודק גם התאמה מלאה וגם התאמה מול כל תת-רצף (חלון הזזה) של המשפט,
    כדי לתפוס מקרים כמו "אה דאוס תגיד לי" שבהם יש מילים נוספות סביב.

    מחזיר: (True/False, מילת_ההפעלה_שהתאימה, ציון_הדמיון)
    """
    norm_transcript = normalize_text(transcript)
    if not norm_transcript:
        return False, None, 0.0

    words = norm_transcript.split(" ")
    best_score = 0.0
    best_word = None

    for wake_word in wake_words:
        norm_wake = normalize_text(wake_word)
        if not norm_wake:
            continue
        wake_len = len(norm_wake.split(" "))

        # 1. השוואה מול המשפט השלם
        score_full = similarity(norm_transcript, norm_wake)
        if score_full > best_score:
            best_score, best_word = score_full, wake_word

        # 2. חלון הזזה בגודל מילות ההפעלה, כדי לתפוס אותה בתוך משפט ארוך יותר
        for i in range(len(words) - wake_len + 1):
            window = " ".join(words[i:i + wake_len])
            score = similarity(window, norm_wake)
            if score > best_score:
                best_score, best_word = score, wake_word

    return best_score >= threshold, best_word, best_score


def locate_wake_word(raw_words: list[str], wake_words: list[str], threshold: float = 0.72):
    """
    כמו contains_wake_word, אבל עובד על רשימת מילים *גולמית* (לא טקסט
    מנורמל) ומחזיר גם את טווח האינדקסים שבו נמצאה ההתאמה - כדי לאפשר
    לפצל את הטקסט המקורי סביב מילת ההפעלה (למשל: הכל *לפני* המילה הוא
    טקסט לתמלול, הכל *אחרי* המילה הוא פקודה).

    מחזיר: (found, matched_word, score, start_idx, end_idx)
    כאשר raw_words[start_idx:end_idx] הוא החלון שהתאים הכי טוב.
    אם found=False, start_idx/end_idx הם None.
    """
    norm_words = [normalize_text(w) for w in raw_words]
    best_score, best_word = 0.0, None
    best_start, best_len = None, 1

    for wake_word in wake_words:
        norm_wake = normalize_text(wake_word)
        if not norm_wake:
            continue
        wake_len = len(norm_wake.split(" "))

        for i in range(len(norm_words) - wake_len + 1):
            window = " ".join(w for w in norm_words[i:i + wake_len] if w)
            if not window:
                continue
            score = similarity(window, norm_wake)
            if score > best_score:
                best_score, best_word = score, wake_word
                best_start, best_len = i, wake_len

    if best_score >= threshold and best_start is not None:
        return True, best_word, best_score, best_start, best_start + best_len
    return False, None, best_score, None, None


# --------------------------------------------------------------------- #
# התאמה "לפי שורש" - מתן סלחנות נוספת לפקודות
# --------------------------------------------------------------------- #
#
# הרעיון: וויספר עשוי לתמלל את אותה כוונה במגוון ניסוחים/נטיות שונות
# ("פתח", "תפתח", "תפתח לי", "פתחי") - ולא תמיד כדאי/אפשרי לרשום את כל
# הצורות בקונפיג. כדי שהפקודה עדיין "תרגיש טבעית" ולא תדרוש שינון של
# ניסוח מדויק, מוסיפים כאן שכבת השוואה "רכה" נוספת שמסירה תחיליות/
# סופיות עבריות נפוצות (בניינים, גוף, זמן, ריבוי) לפני ההשוואה - כך
# ש"תפתחי" ו-"פתח" למשל יתקרבו משמעותית יותר זה לזה.
#
# זו שכבה *משלימה* בלבד (לוקחים max מול ההשוואה הרגילה) - אף פעם לא
# פוגעת בציון, רק עשויה לשפר אותו עבור נטיות שונות של אותו שורש.

_HEB_PREFIXES = ("וש", "כש", "מש", "ש", "ו", "ה", "ל", "ב", "כ", "מ")
_HEB_SUFFIXES = ("תיים", "כם", "כן", "הם", "הן", "נו", "ני", "תי", "תם",
                  "תן", "ים", "ות", "ה", "י", "ו")


def _strip_affixes(word: str) -> str:
    """מסיר תחילית עברית נפוצה אחת (הארוכה ביותר שמתאימה) וסיומת עברית
    נפוצה אחת מהמילה, כדי לקבל קירוב גס ל'שורש' שלה. לא בלשנית מדויקת
    בכוונה (זו לא הצמדה מורפולוגית אמיתית) - רק עוזרת להתאים ניסוחים/
    נטיות שונות של אותה מילה, לצורך fuzzy matching בלבד."""
    if len(word) <= 2:
        return word

    stripped = word
    for prefix in _HEB_PREFIXES:
        if stripped.startswith(prefix) and len(stripped) - len(prefix) >= 2:
            stripped = stripped[len(prefix):]
            break

    for suffix in _HEB_SUFFIXES:
        if stripped.endswith(suffix) and len(stripped) - len(suffix) >= 2:
            stripped = stripped[: -len(suffix)]
            break

    return stripped or word


def similarity_loose(a: str, b: str) -> float:
    """כמו similarity, אבל לוקח את המקסימום מול השוואה נוספת בין הצורות
    ה'משורשרות' (אחרי הסרת תחילית/סיומת עברית נפוצה) - כדי לתפוס ניסוחים
    שונים של אותה מילה/פקודה (זמנים, גוף, יחיד/רבים) גם כשהם לא קרובים
    מספיק במרחק עריכה רגיל."""
    base = similarity(a, b)
    a_norm, b_norm = normalize_text(a), normalize_text(b)
    if not a_norm or not b_norm:
        return base

    a_stemmed = " ".join(_strip_affixes(w) for w in a_norm.split(" "))
    b_stemmed = " ".join(_strip_affixes(w) for w in b_norm.split(" "))
    if a_stemmed == a_norm and b_stemmed == b_norm:
        return base

    if _HAS_RAPIDFUZZ:
        stemmed_score = fuzz.ratio(a_stemmed, b_stemmed) / 100.0
    else:
        stemmed_score = difflib.SequenceMatcher(None, a_stemmed, b_stemmed).ratio()

    return max(base, stemmed_score)


def match_prefix(words: list[str], phrase: str, threshold: float = 0.72):
    """
    בודק אם *תחילת* רשימת המילים (words - גולמית, כמו שיצאה מוויספר)
    תואמת (בקירוב) לביטוי phrase, שיכול להיות בן כמה מילים (למשל
    "פתח את" או "חפש בגוגל"). ההתאמה מתבצעת על המילים הראשונות בלבד
    (בדיוק כמספר המילים ב-phrase), לא בחלון הזזה על כל המשפט - כי
    פקודה תמיד מגיעה מיד אחרי מילת ההפעלה.

    מחזיר: (found, score, remainder_words) - remainder_words היא
    רשימת המילים שנשארו אחרי הביטוי (הארגומנט של הפקודה, אם יש).
    """
    phrase_words = [w for w in normalize_text(phrase).split(" ") if w]
    n = len(phrase_words)
    if n == 0 or len(words) < n:
        return False, 0.0, words

    window_norm = " ".join(normalize_text(w) for w in words[:n])
    phrase_norm = " ".join(phrase_words)
    # similarity_loose (ולא similarity הרגיל) - כדי שניסוחים/נטיות שונות
    # של אותה פקודה ("פתח" מול "תפתח לי") עדיין יתאימו בטבעיות, בלי
    # שהמשתמש יצטרך לשנן את הניסוח המדויק שרשום בקונפיג.
    score = similarity_loose(window_norm, phrase_norm)

    # תיקון בטיחות חשוב: מרחק עריכה (Levenshtein/ratio) על מילים
    # *קצרות* לא אמין - עם א"ב עברי קטן, מילים קצרות ולא-קשורות לגמרי
    # (למשל "ביבי" מול "כיבוי") יכולות בקלות לקבל ציון דמיון גבוה
    # במקרה, ולגרום להפעלה שגויה של פקודה - חמור במיוחד עבור פקודות
    # הרסניות כמו כיבוי. לכן, ככל שהביטוי (phrase_norm, בלי רווחים)
    # קצר יותר, דורשים סף גבוה יותר - מילה נכונה שנאמרה בבירור עדיין
    # תקבל ציון קרוב ל-1.0 ותעבור בקלות; רק "כמעט-התאמות" מקריות
    # של מילים קצרות ולא-קשורות ייחסמו.
    phrase_len_no_spaces = len(phrase_norm.replace(" ", ""))
    if phrase_len_no_spaces <= 3:
        effective_threshold = max(threshold, 0.95)
    elif phrase_len_no_spaces <= 5:
        effective_threshold = max(threshold, 0.88)
    else:
        effective_threshold = threshold

    if score >= effective_threshold:
        return True, score, words[n:]
    return False, score, words
