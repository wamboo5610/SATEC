"""Puente JavaScript ↔ escritorio (diálogos nativos de Windows)."""

from __future__ import annotations

from pathlib import Path


def _window():
    try:
        import webview
    except ImportError:
        return None, None
    if not webview.windows:
        return None, None
    return webview, webview.windows[0]


def _dialog(kind: str):
    webview, _ = _window()
    if webview is None:
        return None
    file_dialog = getattr(webview, "FileDialog", None)
    mapping = {
        "folder": "FOLDER",
        "open": "OPEN",
        "save": "SAVE",
    }
    name = mapping.get(kind, "OPEN")
    value = getattr(file_dialog, name, None) if file_dialog else None
    if value is None:
        legacy = {"folder": "FOLDER_DIALOG", "open": "OPEN_DIALOG", "save": "SAVE_DIALOG"}
        value = getattr(webview, legacy.get(kind, "OPEN_DIALOG"), None)
    return value


class DesktopBridge:
    def browse_folder(self) -> str | None:
        webview, window = _window()
        dialog = _dialog("folder")
        if window is None or dialog is None:
            return None
        result = window.create_file_dialog(dialog)
        if result:
            return str(result[0])
        return None

    def save_file(self, filename: str = "satec_base_datos.zip") -> str | None:
        webview, window = _window()
        dialog = _dialog("save")
        if window is None or dialog is None:
            return None
        documents = str(Path.home() / "Documents")
        result = window.create_file_dialog(
            dialog,
            directory=documents,
            save_filename=filename,
            file_types=("Respaldo SATEC (*.zip)",),
        )
        if not result:
            return None
        path = result[0] if isinstance(result, (list, tuple)) else result
        return str(path)

    def open_file(self) -> str | None:
        webview, window = _window()
        dialog = _dialog("open")
        if window is None or dialog is None:
            return None
        result = window.create_file_dialog(
            dialog,
            file_types=("Respaldo SATEC (*.zip)",),
        )
        if result:
            return str(result[0])
        return None
