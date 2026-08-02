"""
חלון הגדרות לניהול הסקריפטים שדאוס יודע להפעיל בפקודה קולית
("דאוס הפעל סקריפט <שם>"). דומה במבנה שלו לחלון ניהול האפליקציות
(ui/apps_dialog.py) - כל שורה היא זוג (שם בעברית -> נתיב לקובץ
סקריפט: .exe / .bat / .cmd / .py / .ps1).

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


class ScriptsDialog(QDialog):
    scripts_saved = Signal(dict)

    def __init__(self, scripts: dict, logger=None, parent=None):
        super().__init__(parent)
        self.log = logger
        self._scripts = dict(scripts)  # עותק עבודה מקומי - לא נוגעים במקור עד "שמור"

        self.setWindowTitle("ניהול סקריפטים - Deus")
        self.setMinimumSize(480, 360)
        self.setLayoutDirection(Qt.RightToLeft)

        layout = QVBoxLayout(self)

        info_label = QLabel(
            "סקריפטים שדאוס יכול להפעיל בפקודה \"דאוס הפעל סקריפט <שם>\".\n"
            "נתמכים: קבצי .exe/.bat/.cmd (מופעלים ישירות), .py (עם python), "
            "ו-.ps1 (עם PowerShell)."
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

        add_btn = QPushButton("הוסף סקריפט...")
        add_btn.clicked.connect(self._on_add)
        buttons_row.addWidget(add_btn)

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
        for name, path in sorted(self._scripts.items()):
            row = self.table.rowCount()
            self.table.insertRow(row)
            self.table.setItem(row, 0, QTableWidgetItem(name))
            self.table.setItem(row, 1, QTableWidgetItem(path))

    def _on_add(self):
        path, _filter = QFileDialog.getOpenFileName(
            self, "בחר קובץ סקריפט", "",
            "סקריפטים (*.exe *.bat *.cmd *.py *.ps1);;כל הקבצים (*)"
        )
        if not path:
            return

        name, ok = QInputDialog.getText(
            self, "שם הסקריפט", "איך תרצה לקרוא לסקריפט הזה (בעברית)?"
        )
        if not ok or not name.strip():
            return

        name = name.strip()
        if name in self._scripts:
            reply = QMessageBox.question(
                self, "שם כבר קיים",
                f"כבר יש סקריפט בשם '{name}'. להחליף את הנתיב שלו?",
            )
            if reply != QMessageBox.Yes:
                return

        self._scripts[name] = path
        self._reload_table()

    def _on_remove_selected(self):
        selected_rows = sorted({idx.row() for idx in self.table.selectedIndexes()}, reverse=True)
        for row in selected_rows:
            name_item = self.table.item(row, 0)
            if name_item:
                self._scripts.pop(name_item.text(), None)
        self._reload_table()

    def _on_save(self):
        self.scripts_saved.emit(self._scripts)
        self.accept()
