#!/usr/bin/env python3
"""GTK3 ground-truth test application for live X11/AT-SPI2 verification.

Licensed under Apache-2.0.
"""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from typing import Any

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, GLib, Gtk


class GtkTestHarnessWindow(Gtk.Window):
    """GTK3 test window exposing realistic interactive widgets and screen coordinates."""

    def __init__(self, state_file: str | None = None):
        super().__init__(title="JIT-MCP GTK3 Test Harness")
        self.state_file = state_file
        self.set_default_size(320, 400)
        self.set_position(Gtk.WindowPosition.CENTER)

        # State tracking
        self.proceed_clicks: int = 0
        self.canvas_strokes_count: int = 0
        self.canvas_points: list[list[int]] = []
        self._is_drawing: bool = False

        # Main layout container
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.set_border_width(12)
        self.add(box)

        # 1. Gtk.Entry
        self.entry = Gtk.Entry()
        self.entry.set_placeholder_text("Enter text here")
        entry_acc = self.entry.get_accessible()
        if entry_acc:
            entry_acc.set_name("Query Entry")
        self.entry.connect("changed", self._on_entry_changed)
        box.pack_start(self.entry, False, False, 0)

        # 2. Gtk.Button (initially sensitive=False)
        self.btn_proceed = Gtk.Button(label="Proceed")
        self.btn_proceed.set_sensitive(False)
        btn_acc = self.btn_proceed.get_accessible()
        if btn_acc:
            btn_acc.set_name("Proceed")
        self.btn_proceed.connect("clicked", self._on_proceed_clicked)
        box.pack_start(self.btn_proceed, False, False, 0)

        # 3. Gtk.CheckButton
        self.checkbox = Gtk.CheckButton(label="Enable Feature")
        chk_acc = self.checkbox.get_accessible()
        if chk_acc:
            chk_acc.set_name("Enable Feature")
        self.checkbox.connect("toggled", self._on_checkbox_toggled)
        box.pack_start(self.checkbox, False, False, 0)

        # 4. Gtk.ComboBoxText
        self.combo = Gtk.ComboBoxText()
        for opt in ["Alpha", "Beta", "Gamma"]:
            self.combo.append_text(opt)
        self.combo.set_active(0)
        combo_acc = self.combo.get_accessible()
        if combo_acc:
            combo_acc.set_name("Combo Box")
        self.combo.connect("changed", self._on_combo_changed)
        box.pack_start(self.combo, False, False, 0)

        # 5. Gtk.DrawingArea
        self.canvas = Gtk.DrawingArea()
        self.canvas.set_size_request(240, 140)
        canvas_acc = self.canvas.get_accessible()
        if canvas_acc:
            canvas_acc.set_name("Drawing Canvas")
        self.canvas.add_events(
            Gdk.EventMask.BUTTON_PRESS_MASK
            | Gdk.EventMask.BUTTON_RELEASE_MASK
            | Gdk.EventMask.POINTER_MOTION_MASK
        )
        self.canvas.connect("button-press-event", self._on_canvas_press)
        self.canvas.connect("button-release-event", self._on_canvas_release)
        self.canvas.connect("motion-notify-event", self._on_canvas_motion)
        self.canvas.connect("draw", self._on_canvas_draw)
        box.pack_start(self.canvas, True, True, 0)

        # Track registered widgets for ground-truth coordinates
        self.registered_widgets: dict[str, Gtk.Widget] = {
            "Query Entry": self.entry,
            "Proceed": self.btn_proceed,
            "Enable Feature": self.checkbox,
            "Combo Box": self.combo,
            "Drawing Canvas": self.canvas,
        }

        self.connect("destroy", Gtk.main_quit)
        GLib.timeout_add(150, self._periodic_save)

    def _on_entry_changed(self, entry: Gtk.Entry) -> None:
        text = entry.get_text()
        self.btn_proceed.set_sensitive(bool(text.strip()))
        self.save_state()

    def _on_proceed_clicked(self, button: Gtk.Button) -> None:
        self.proceed_clicks += 1
        self.save_state()

    def _on_checkbox_toggled(self, button: Gtk.CheckButton) -> None:
        self.save_state()

    def _on_combo_changed(self, combo: Gtk.ComboBoxText) -> None:
        self.save_state()

    def _on_canvas_press(self, widget: Gtk.DrawingArea, event: Gdk.EventButton) -> bool:
        self._is_drawing = True
        self.canvas_strokes_count += 1
        self.canvas_points.append([int(event.x), int(event.y)])
        widget.queue_draw()
        return True

    def _on_canvas_release(self, widget: Gtk.DrawingArea, event: Gdk.EventButton) -> bool:
        self._is_drawing = False
        self.canvas_points.append([int(event.x), int(event.y)])
        widget.queue_draw()
        self.save_state()
        return True

    def _on_canvas_motion(self, widget: Gtk.DrawingArea, event: Gdk.EventMotion) -> bool:
        if self._is_drawing:
            self.canvas_points.append([int(event.x), int(event.y)])
            widget.queue_draw()
        return True

    def _on_canvas_draw(self, widget: Gtk.DrawingArea, cr: Any) -> bool:
        # Background
        cr.set_source_rgb(0.95, 0.95, 0.95)
        cr.paint()

        # Stroke path
        if len(self.canvas_points) > 1:
            cr.set_source_rgb(0.1, 0.5, 0.8)
            cr.set_line_width(2.0)
            x0, y0 = self.canvas_points[0]
            cr.move_to(x0, y0)
            for pt in self.canvas_points[1:]:
                cr.line_to(pt[0], pt[1])
            cr.stroke()
        return False

    def _compute_widget_screen_coords(
        self, widget: Gtk.Widget
    ) -> dict[str, Any] | None:
        gdk_win = self.get_window()
        if not gdk_win:
            return None

        rel = widget.translate_coordinates(self, 0, 0)
        if rel is None:
            return None
        rel_x, rel_y = rel

        root = gdk_win.get_root_coords(rel_x, rel_y)
        if not root:
            return None
        root_x, root_y = root

        alloc = widget.get_allocation()
        width = alloc.width
        height = alloc.height
        cx = root_x + width // 2
        cy = root_y + height // 2

        return {
            "x": int(root_x),
            "y": int(root_y),
            "width": int(width),
            "height": int(height),
            "cx": int(cx),
            "cy": int(cy),
            "sensitive": bool(widget.get_sensitive()),
        }

    def get_state(self) -> dict[str, Any]:
        """Compute the current snapshot of widget coordinates and event counts."""
        widgets_dict: dict[str, Any] = {}
        for name, widget in self.registered_widgets.items():
            coords = self._compute_widget_screen_coords(widget)
            if coords is not None:
                widgets_dict[name] = coords

        active_text = self.combo.get_active_text() or ""
        return {
            "pid": os.getpid(),
            "widgets": widgets_dict,
            "events": {
                "proceed_clicks": self.proceed_clicks,
                "entry_text": self.entry.get_text(),
                "checkbox_active": bool(self.checkbox.get_active()),
                "combo_active_text": active_text,
                "canvas_strokes_count": self.canvas_strokes_count,
                "canvas_points": list(self.canvas_points),
            },
        }

    def save_state(self) -> None:
        """Atomically persist state snapshot to self.state_file."""
        if not self.state_file:
            return
        state = self.get_state()
        dir_name = os.path.dirname(os.path.abspath(self.state_file))
        os.makedirs(dir_name, exist_ok=True)
        tmp_fd, tmp_path = tempfile.mkstemp(dir=dir_name, prefix=".gtk_state_")
        try:
            with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
                json.dump(state, f, indent=2)
            os.replace(tmp_path, self.state_file)
        except OSError:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)

    def _periodic_save(self) -> bool:
        self.save_state()
        return True


def main() -> None:
    parser = argparse.ArgumentParser(description="GTK3 Test Harness for JIT-MCP")
    parser.add_argument("--state-file", type=str, help="Path to write state JSON")
    parser.add_argument(
        "--quit-after",
        type=float,
        default=None,
        help="Timeout in seconds before exiting automatically",
    )
    args = parser.parse_args()

    win = GtkTestHarnessWindow(state_file=args.state_file)
    win.show_all()

    # Initial state write
    GLib.idle_add(win.save_state)

    if args.quit_after:
        timeout_ms = int(args.quit_after * 1000)
        GLib.timeout_add(timeout_ms, Gtk.main_quit)

    Gtk.main()


if __name__ == "__main__":
    main()
