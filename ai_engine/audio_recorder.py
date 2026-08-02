"""
"דאוס הקלט" - מתחיל להקליט מהמיקרופון ברקע, עד "דאוס עצור" (פקודת
העצירה הגנרית, כמו עם תמלול/מאקרו - ראו speech/wake_word.py). בעצירה,
הקובץ נשמר כ-WAV בתיקיית Downloads של המשתמש, עם שם קובץ שכולל את
התאריך והשעה.

משתמש ב-sounddevice (כבר תלות קיימת בפרויקט דרך speech/wake_word.py)
כדי להקליט בזרם רציף לרשימת מקטעים, ובחבילת wave הפנימית של Python
כדי לכתוב את קובץ ה-WAV - בלי תלויות חדשות.
"""

import datetime
import logging
import os
import threading
import wave

import numpy as np

try:
    import sounddevice as sd
    _HAS_SOUNDDEVICE = True
except ImportError:
    _HAS_SOUNDDEVICE = False

_log = logging.getLogger("deus")


def _downloads_dir() -> str:
    home = os.path.expanduser("~")
    downloads = os.path.join(home, "Downloads")
    os.makedirs(downloads, exist_ok=True)
    return downloads


class AudioRecorder:
    """מקליט אודיו מהמיקרופון ברצף עד stop(). לא תלוי במודל Whisper -
    זו הקלטה "גולמית" בלבד, נשמרת ישירות לקובץ."""

    def __init__(self, logger: logging.Logger = None):
        self.log = logger or _log
        self._recording = False
        self._stream = None
        self._frames = []
        self._lock = threading.Lock()
        self.sample_rate = 44100
        self.channels = 1

    def is_recording(self) -> bool:
        return self._recording

    def start(self, sample_rate: int = 44100, channels: int = 1):
        if self._recording:
            return
        if not _HAS_SOUNDDEVICE:
            self.log.warning("חבילת sounddevice לא מותקנת - לא ניתן להקליט")
            return

        self.sample_rate = sample_rate
        self.channels = channels
        self._frames = []
        self._recording = True

        def _callback(indata, frames, time_info, status):
            if status:
                self.log.debug("סטטוס הקלטה: %s", status)
            with self._lock:
                self._frames.append(indata.copy())

        try:
            self._stream = sd.InputStream(
                samplerate=sample_rate, channels=channels,
                dtype="int16", callback=_callback,
            )
            self._stream.start()
            self.log.info("התחלת הקלטת שמע (%dHz, %d ערוצים)", sample_rate, channels)
        except Exception:
            self.log.exception("נכשל בפתיחת זרם ההקלטה")
            self._recording = False
            self._stream = None

    def stop(self):
        """עוצר את ההקלטה, שומר קובץ WAV לתיקיית Downloads עם חותמת
        זמן בשם הקובץ, ומחזיר את הנתיב שנשמר (או None אם לא הוקלט כלום)."""
        if not self._recording:
            return None
        self._recording = False

        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                self.log.exception("שגיאה בסגירת זרם ההקלטה")
            self._stream = None

        with self._lock:
            frames = self._frames
            self._frames = []

        if not frames:
            self.log.warning("לא נאספו נתוני שמע - לא נשמר קובץ")
            return None

        audio_data = np.concatenate(frames, axis=0)

        timestamp = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        out_dir = _downloads_dir()
        out_path = os.path.join(out_dir, f"deus_recording_{timestamp}.wav")

        try:
            with wave.open(out_path, "wb") as wf:
                wf.setnchannels(self.channels)
                wf.setsampwidth(2)  # int16 = 2 bytes
                wf.setframerate(self.sample_rate)
                wf.writeframes(audio_data.tobytes())
            self.log.info("ההקלטה נשמרה: %s", out_path)
            return out_path
        except Exception:
            self.log.exception("נכשל בשמירת קובץ ההקלטה")
            return None
