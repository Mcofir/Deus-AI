"""
חלון הגדרות לניהול המאקרואים שהוקלטו ("דאוס תלמד מאקרו <שם>" ואז
"דאוס עצור"). מציג לכל מאקרו: שם, כמות אירועים שהוקלטו, ומספר החזרות
שיבוצעו בכל הפעלה (ניתן לשינוי כאן - "אינסופי" = ירוץ עד "דאוס עצור").
ניתן גם למחוק מאקרו קיים.

אין כאן אפשרות הוספה/הקלטה - הקלטת מאקרו חדש מתבצעת רק בפקודה קולית
("דאוס תלמד מאקרו <שם>"), לא דרך הדיאלוג הזה.
"""

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QTableWidget,
    QTableWidgetItem,
    QPushButton,
    QSpinBox,
    QCheckBox,
    QWidget,
    QHeaderView,
    QLabel,
)


class MacrosDialog(QDialog):
    macros_saved = Signal(dict)

    def __init__(self, macros: dict, logger=None, parent=None):
        super().__init__(parent)
        self.log = logger
        self._macros = {name: dict(data) for name, data in macros.items()}

        self.setWindowTitle("ניהול מאקרואים - Deus")
        self.setMinimumSize(560, 380)
        self.setLayoutDirection(Qt.RightToLeft)

        layout = QVBoxLayout(self)

        info_label = QLabel(
            "מאקרואים שהוקלטו בפקודה \"דאוס תלמד מאקרו <שם>\" ואז \"דאוס עצור\".\n"
            "הפעלה: \"דאוס תפעיל מקרו <שם>\". קבעו כאן כמה פעמים כל מאקרו "
            "ירוץ בכל הפעלה - סמנו \"אינסופי\" כדי שירוץ עד שנאמר "
            "\"דאוס עצור\" תוך כדי ריצה."
        )
        info_label.setWordWrap(True)
        layout.addWidget(info_label)

        self.table = QTableWidget(0, 4, self)
        self.table.setHorizontalHeaderLabels(["שם", "מס' אירועים", "חזרות", "אינסופי"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        # מאפשרים עריכה רק בעמודת השם (0) בלחיצה כפולה - שאר העמודות
        # (כמות אירועים, חזרות, אינסופי) נשארות לא-עריכות דרך setItem
        # ו-setCellWidget בהמשך.
        self.table.setEditTriggers(QTableWidget.EditTrigger.DoubleClicked)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        layout.addWidget(self.table)

        self._reload_table()

        buttons_row = QHBoxLayout()
        remove_btn = QPushButton("מחק נבחר")
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
        for name, data in sorted(self._macros.items()):
            row = self.table.rowCount()
            self.table.insertRow(row)

            name_item = QTableWidgetItem(name)
            # רק עמודת השם ניתנת לעריכה (בלחיצה כפולה) - שאר העמודות לא
            self.table.setItem(row, 0, name_item)

            events_count = len(data.get("events", []))
            count_item = QTableWidgetItem(str(events_count))
            count_item.setFlags(count_item.flags() & ~Qt.ItemIsEditable)
            self.table.setItem(row, 1, count_item)

            repeat = data.get("repeat", 1)
            infinite = repeat is None or repeat <= 0

            spin = QSpinBox()
            spin.setRange(1, 9999)
            spin.setValue(repeat if not infinite else 1)
            spin.setEnabled(not infinite)
            spin.valueChanged.connect(lambda v, n=name: self._on_repeat_changed(n, v))
            self.table.setCellWidget(row, 2, spin)

            infinite_wrap = QWidget()
            infinite_layout = QHBoxLayout(infinite_wrap)
            infinite_layout.setContentsMargins(0, 0, 0, 0)
            infinite_layout.setAlignment(Qt.AlignCenter)
            chk = QCheckBox()
            chk.setChecked(infinite)
            chk.stateChanged.connect(
                lambda state, n=name, s=spin: self._on_infinite_toggled(n, state, s)
            )
            infinite_layout.addWidget(chk)
            self.table.setCellWidget(row, 3, infinite_wrap)

    def _on_repeat_changed(self, name, value):
        if name in self._macros:
            self._macros[name]["repeat"] = value

    def _on_infinite_toggled(self, name, state, spin_widget):
        is_infinite = bool(state)
        spin_widget.setEnabled(not is_infinite)
        if name in self._macros:
            self._macros[name]["repeat"] = 0 if is_infinite else spin_widget.value()

    def _on_remove_selected(self):
        # מוצא את המאקרו למחיקה לפי שורה (index) ולא לפי שם - כי המשתמש
        # יכול היה לערוך את השם (לחיצה כפולה) לפני הלחיצה על "מחק".
        old_items = list(self._macros.items())
        old_items.sort(key=lambda x: x[0])

        selected_rows = sorted({idx.row() for idx in self.table.selectedIndexes()}, reverse=True)
        for row in selected_rows:
            if row < len(old_items):
                old_name = old_items[row][0]
                self._macros.pop(old_name, None)
        self._reload_table()

    def _on_save(self):
        # בניה מחדש של מילון המאקרו לפי השמות בטבלה - המשתמש יכול היה
        # לשנות שמות בעמודה 0 (לחיצה כפולה -> עריכה).
        # הטבלה תמיד מסודרת באותו סדר כמו sorted(self._macros.items())
        # כי _reload_table() בונה מחדש לפי הסדר הזה, ואין מיון חיצוני.
        old_items = list(self._macros.items())
        old_items.sort(key=lambda x: x[0])

        renamed = {}
        for row in range(self.table.rowCount()):
            name_item = self.table.item(row, 0)
            if name_item is None or row >= len(old_items):
                continue
            new_name = name_item.text().strip()
            if not new_name:
                continue
            _old_name, data = old_items[row]
            # שומרים את הערך העדכני של repeat (שכבר עודכן
            # ב-_macros דרך _on_repeat_changed / _on_infinite_toggled)
            renamed[new_name] = self._macros.get(_old_name, data)

        self.macros_saved.emit(renamed)
        self.accept()
