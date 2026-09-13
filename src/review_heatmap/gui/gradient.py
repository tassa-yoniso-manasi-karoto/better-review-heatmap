"""Edit baseline gradient colors using Qt's color picker.

See LICENSE for the add-on's license and additional terms.
"""

from copy import deepcopy

from aqt.qt import (
    QColor, QColorDialog, QDialog, QDialogButtonBox, QFormLayout,
    QDoubleSpinBox, QGroupBox, QHBoxLayout, QIcon, QPushButton, Qt, QVBoxLayout,
)

from ..metrics import DEFAULT_BASELINE_GRADIENT, gradient_stops, gradient_opacity, point_opacity


class GradientDialog(QDialog):
    def __init__(self, config, parent=None):
        super().__init__(parent)
        self.config = config
        self.setWindowTitle("Heatmap gradient colors")
        self.gradient = deepcopy(
            config["local"].get("baseline_gradient", DEFAULT_BASELINE_GRADIENT)
        )
        # Normalize malformed JSON using the same fallback as the renderer.
        self.gradient = {
            side: [
                {"workload_ratio": ratio, "hsl": self._hsl(QColor("#" + color)),
                 "opacity": gradient_opacity(self.gradient, side, ratio) * 100}
                for ratio, color in gradient_stops(self.gradient, side)
            ]
            for side in ("below", "above")
        }
        layout = QVBoxLayout(self)
        self.groups = QVBoxLayout()
        layout.addLayout(self.groups)
        self._build_points()
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        restore = buttons.addButton(QDialogButtonBox.StandardButton.RestoreDefaults)
        restore.clicked.connect(self._restore_defaults)
        buttons.rejected.connect(self.close)
        layout.addWidget(buttons)

    @staticmethod
    def _hsl(color):
        return [round(max(0, color.hslHueF()) * 360, 6),
                round(color.hslSaturationF() * 100, 6),
                round(color.lightnessF() * 100, 6)]

    @staticmethod
    def _color(point):
        h, s, l = point["hsl"]
        return QColor.fromHslF((h % 360) / 360, s / 100, l / 100)

    def _style_button(self, button, point):
        color = self._color(point)
        alpha = round(float(point.get("opacity", 100)) * 255 / 100)
        foreground = "black" if color.lightnessF() > 0.55 else "white"
        button.setStyleSheet(
            f"background-color: rgba({color.red()}, {color.green()}, {color.blue()}, {alpha}); "
            f"color: {foreground}; padding: 6px;"
        )
        h, s, l = point["hsl"]
        button.setText(f"H {h:.1f}°   S {s:.1f}%   L {l:.1f}%")

    def _build_points(self):
        for side, title in (("below", "Below baseline"), ("above", "At or above baseline")):
            group = QGroupBox(title, self)
            form = QFormLayout(group)
            for point in self.gradient[side]:
                point.setdefault("opacity", point_opacity(point, side))
                button = QPushButton(group)
                self._style_button(button, point)
                button.clicked.connect(
                    lambda checked=False, p=point, b=button: self._pick(p, b)
                )
                opacity = QDoubleSpinBox(group)
                opacity.setRange(0, 100)
                opacity.setDecimals(1)
                opacity.setSuffix("%")
                opacity.setAlignment(Qt.AlignmentFlag.AlignRight)
                opacity.setFixedWidth(72)
                opacity.setValue(point_opacity(point, side))
                opacity.setKeyboardTracking(False)
                opacity.valueChanged.connect(
                    lambda value, p=point, b=button: self._set_opacity(p, value, b)
                )
                row = QHBoxLayout()
                row.setSpacing(4)
                row.addWidget(button, 1)
                row.addWidget(opacity)
                restore = QPushButton(group)
                restore.setIcon(QIcon("review_heatmap:icons/restore.svg"))
                restore.setToolTip("Restore this color and opacity")
                restore.setAccessibleName("Restore this gradient point")
                restore.setFixedWidth(30)
                restore.clicked.connect(
                    lambda checked=False, s=side, p=point, b=button, o=opacity:
                    self._restore_point(s, p, b, o)
                )
                row.addWidget(restore)
                form.addRow(f"{int(point['workload_ratio'] * 100)}%", row)
            self.groups.addWidget(group)

    def _save(self):
        local = deepcopy(self.config["local"])
        local["baseline_gradient"] = deepcopy(self.gradient)
        self.config["local"] = local
        self.config.save("local")

    def _set_opacity(self, point, value, button):
        point["opacity"] = value
        self._style_button(button, point)
        self._save()

    def _restore_point(self, side, point, button, opacity):
        defaults = self.config["local"].get(
            "baseline_gradient_default", DEFAULT_BASELINE_GRADIENT
        )
        ratio = point["workload_ratio"]
        default = next(
            (candidate for candidate in defaults[side]
             if float(candidate["workload_ratio"]) == float(ratio)),
            DEFAULT_BASELINE_GRADIENT[side][0],
        )
        point.clear()
        point.update(deepcopy(default))
        point.setdefault("opacity", point_opacity(point, side))
        self._style_button(button, point)
        opacity.blockSignals(True)
        opacity.setValue(point["opacity"])
        opacity.blockSignals(False)
        self._save()

    def _restore_defaults(self):
        self.gradient = deepcopy(
            self.config["local"].get("baseline_gradient_default", DEFAULT_BASELINE_GRADIENT)
        )
        while self.groups.count():
            widget = self.groups.takeAt(0).widget()
            widget.hide()
            widget.deleteLater()
        self._build_points()
        self._save()

    def _pick(self, point, button):
        original = list(point["hsl"])
        picker = QColorDialog(self._color(point), self)
        picker.setWindowTitle("Gradient point color")

        def changed(color):
            point["hsl"] = self._hsl(color)
            self._style_button(button, point)
            self._save()

        picker.currentColorChanged.connect(changed)
        if picker.exec() != QDialog.DialogCode.Accepted:
            point["hsl"] = original
            self._style_button(button, point)
            self._save()
