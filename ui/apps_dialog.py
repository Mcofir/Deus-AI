"""
חלון הגדרות לניהול האפליקציות שדאוס יודע לפתוח (פקודת "דאוס פתח את X").

כל שורה היא זוג (שם בעברית -> נתיב לקובץ exe). שתי דרכים להוסיף:
  1. ידנית: כפתור "הוסף אפליקציה..." פותח דיאלוג בחירת קובץ, ואז שואל
     איזה שם בעברית לתת לה.
  2. אוטומטית: כפתור "חפש אפליקציות נפוצות אוטומטית" סורק נתיבי התקנה
     סטנדרטיים (ראו ai_engine/app_finder.py) עבור Chrome/Brave/WhatsApp,
     ומוסיף כל מה שנמצא (בלי לדרוס ידנית מה שכבר הוגדר לאותו שם).

השינויים לא נשמרים לדיסק עד לחיצה על "שמור" - זה משדר את הרשימה
המלאה החוצה (Signal), ו-main.py כבר אחראי לשמור אותה בקונפיג.
"""

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QTableWidget,
    QTableWidgetItem,
    QPushButton,
    QFileDialog,
    QInputDialog,
    QMessageBox,
    QHeaderView,
    QLabel,
)

from ai_engine.app_finder import auto_detect_apps


class AppsDialog(QDialog):
    apps_saved = Signal(dict)

    def __init__(self, apps: dict, logger=None, parent=None):
        super().__init__(parent)
        self.log = logger
        self._apps = dict(apps)  # עותק עבודה מקומי - לא נוגעים במקור עד "שמור"

        self.setWindowTitle("ניהול אפליקציות - Deus")
        self.setMinimumSize(480, 360)
        self.setLayoutDirection(Qt.RightToLeft)

        layout = QVBoxLayout(self)

        info_label = QLabel(
            "אפליקציות שדאוס יכול לפתוח בפקודה \"דאוס פתח את <שם>\".\n"
            "אפשר להוסיף ידנית, או לחפש אוטומטית אפליקציות נפוצות."
        )
        info_label.setWordWrap(True)
        layout.addWidget(info_label)

        self.table = QTableWidget(0, 2, self)
        self.table.setHorizontalHeaderLabels(["שם (בעברית)", "נתיב לקובץ"])
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        layout.addWidget(self.table)

        self._reload_table()

        buttons_row = QHBoxLayout()

        add_btn = QPushButton("הוסף אפליקציה ידנית...")
        add_btn.clicked.connect(self._on_add_manual)
        buttons_row.addWidget(add_btn)

        auto_btn = QPushButton("חפש אפליקציות נפוצות אוטומטית")
        auto_btn.clicked.connect(self._on_auto_detect)
        buttons_row.addWidget(auto_btn)

        remove_btn = QPushButton("הסר נבחר")
        remove_btn.clicked.connect(self._on_remove_selected)
        buttons_row.addWidget(remove_btn)

        layout.addLayout(buttons_row)

        save_row = QHBoxLayout()
        save_row.addStretch(1)
        save_btn = QPushButton("שמור")
        save_btn.clicked.connect(self._on_save)
        save_row.addWidget(save_btn)
        cancel_btn = QPushButton("ביטול")
        cancel_btn.clicked.connect(self.reject)
        save_row.addWidget(cancel_btn)
        layout.addLayout(save_row)

    # ------------------------------------------------------------------ #

    def _reload_table(self):
        self.table.setRowCount(0)
        for name, path in sorted(self._apps.items()):
            row = self.table.rowCount()
            self.table.insertRow(row)
            self.table.setItem(row, 0, QTableWidgetItem(name))
            self.table.setItem(row, 1, QTableWidgetItem(path))

    def _on_add_manual(self):
        path, _filter = QFileDialog.getOpenFileName(
            self, "בחר קובץ הפעלה (exe)", "", "Executable (*.exe);;כל הקבצים (*)"
        )
        if not path:
            return

        name, ok = QInputDialog.getText(
            self, "שם האפליקציה", "איך תרצה לקרוא לאפליקציה הזו (בעברית)?"
        )
        if not ok or not name.strip():
            return

        name = name.strip()
        if name in self._apps:
            reply = QMessageBox.question(
                self, "שם כבר קיים",
                f"כבר יש אפליקציה בשם '{name}'. להחליף את הנתיב שלה?",
            )
            if reply != QMessageBox.Yes:
                return

        self._apps[name] = path
        self._reload_table()

    def _on_auto_detect(self):
        detected = auto_detect_apps(self.log)
        if not detected:
            QMessageBox.information(
                self, "לא נמצא כלום",
                "לא אותרו אפליקציות נפוצות (Chrome/Brave/WhatsApp) "
                "בנתיבי ההתקנה הסטנדרטיים. אפשר להוסיף ידנית.",
            )
            return

        added = []
        for name, path in detected.items():
            if name not in self._apps:
                self._apps[name] = path
                added.append(name)

        self._reload_table()
        if added:
            QMessageBox.information(
                self, "נמצאו אפליקציות",
                "נוספו אוטומטית: " + ", ".join(added),
            )
        else:
            QMessageBox.information(
                self, "אין חדש",
                "כל האפליקציות שאותרו כבר מוגדרות אצלך.",
            )

    def _on_remove_selected(self):
        selected_rows = sorted({idx.row() for idx in self.table.selectedIndexes()}, reverse=True)
        for row in selected_rows:
            name_item = self.table.item(row, 0)
            if name_item:
                self._apps.pop(name_item.text(), None)
        self._reload_table()

    def _on_save(self):
        self.apps_saved.emit(self._apps)
        self.accept()
