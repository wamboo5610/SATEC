"""SISAT — aplicación de escritorio para PC. Autor: WAMBOO TIC."""

from __future__ import annotations

import os
import socket
import sys
import threading
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.chdir(ROOT)
os.environ["SISAT_DESKTOP"] = "1"

LOG_PATH = ROOT / "data" / "sisat.log"
ENGINE_ERROR: list[str] = []


def _ensure_stdio() -> None:
    """pythonw.exe deja stdout/stderr en None y Uvicorn se cae al llamar isatty()."""
    need_stdout = sys.stdout is None or not hasattr(sys.stdout, "isatty")
    need_stderr = sys.stderr is None or not hasattr(sys.stderr, "isatty")
    if not need_stdout and not need_stderr:
        return
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    stream = open(LOG_PATH, "a", encoding="utf-8", buffering=1)
    if need_stdout:
        sys.stdout = stream
    if need_stderr:
        sys.stderr = stream


_ensure_stdio()

from app.paths import get_data_dir, icon_path  # noqa: E402
from app.version import APP_ID, APP_TITLE, APP_VERSION, AUTHOR, WINDOW_TITLE  # noqa: E402


PREFERRED_PORT = 8000


def _set_app_id() -> None:
    if sys.platform != "win32":
        return
    try:
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
    except Exception:
        pass


def _port_open(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.25)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def _is_sisat(port: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/auth/status", timeout=0.6) as response:
            return response.status == 200
    except Exception:
        return False


def pick_port() -> tuple[int, bool]:
    """Devuelve (puerto, ya_estaba_corriendo)."""
    if _port_open(PREFERRED_PORT):
        if _is_sisat(PREFERRED_PORT):
            return PREFERRED_PORT, True
        for port in range(PREFERRED_PORT + 1, PREFERRED_PORT + 21):
            if not _port_open(port):
                return port, False
        raise RuntimeError("No hay un puerto libre entre 8000 y 8020")
    return PREFERRED_PORT, False


def wait_ready(port: int, timeout: float = 20.0, thread: threading.Thread | None = None) -> bool:
    deadline = time.time() + timeout
    url = f"http://127.0.0.1:{port}/api/auth/status"
    started = time.time()
    while time.time() < deadline:
        if ENGINE_ERROR:
            return False
        if thread is not None and time.time() - started > 1.2 and not thread.is_alive():
            return False
        try:
            with urllib.request.urlopen(url, timeout=0.7):
                return True
        except Exception:
            time.sleep(0.12)
    return False


def start_engine(port: int) -> None:
    _ensure_stdio()
    os.environ["SISAT_PORT"] = str(port)
    try:
        from app import auth
        from app import database as db
        from app.main import app as fastapi_app
        import uvicorn

        db.init_db()
        auth.init_auth()
        config = uvicorn.Config(
            fastapi_app,
            host="0.0.0.0",
            port=port,
            log_level="warning",
            access_log=False,
            use_colors=False,
        )
        uvicorn.Server(config).run()
    except Exception:
        import traceback

        ENGINE_ERROR.append(traceback.format_exc())
        try:
            LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
            with LOG_PATH.open("a", encoding="utf-8") as handle:
                handle.write("\n--- error al arrancar el motor ---\n")
                handle.write(ENGINE_ERROR[-1])
        except Exception:
            pass


def _error_dialog(message: str) -> None:
    try:
        import tkinter as tk
        from tkinter import messagebox

        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        messagebox.showerror(f"{APP_TITLE} — {AUTHOR}", message)
        root.destroy()
    except Exception:
        print(message, file=sys.stderr)


def _apply_window_icon(window) -> None:
    ico = icon_path()
    if sys.platform != "win32" or not ico.exists() or ico.suffix.lower() != ".ico":
        return
    hwnd = None
    native = getattr(window, "native", None)
    for attr in ("Handle", "handle", "hwnd"):
        value = getattr(native, attr, None)
        if value is None:
            continue
        try:
            hwnd = int(value)
            break
        except (TypeError, ValueError):
            try:
                hwnd = int(value.ToInt64())
                break
            except Exception:
                continue
    if not hwnd:
        return
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        IMAGE_ICON = 1
        LR_LOADFROMFILE = 0x0010
        WM_SETICON = 0x0080
        LoadImageW = user32.LoadImageW
        LoadImageW.argtypes = [
            wintypes.HINSTANCE,
            wintypes.LPCWSTR,
            wintypes.UINT,
            ctypes.c_int,
            ctypes.c_int,
            wintypes.UINT,
        ]
        LoadImageW.restype = wintypes.HANDLE
        handle = LoadImageW(None, str(ico), IMAGE_ICON, 0, 0, LR_LOADFROMFILE)
        if handle:
            user32.SendMessageW(hwnd, WM_SETICON, 0, handle)
            user32.SendMessageW(hwnd, WM_SETICON, 1, handle)
    except Exception:
        pass


def _keep_server() -> int:
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        return 0


def open_desktop(port: int) -> int:
    url = f"http://127.0.0.1:{port}/login"
    try:
        import webview
    except ImportError:
        _error_dialog(
            "Falta el componente de ventana (pywebview).\n"
            "Ejecute INSTALAR.bat e intente de nuevo."
        )
        return 1

    from desktop.bridge import DesktopBridge

    storage = get_data_dir() / "webview"
    storage.mkdir(parents=True, exist_ok=True)
    window = webview.create_window(
        WINDOW_TITLE,
        url,
        width=1440,
        height=880,
        min_size=(1100, 700),
        confirm_close=True,
        text_select=True,
        js_api=DesktopBridge(),
    )

    def on_shown():
        _apply_window_icon(window)

    try:
        window.events.shown += on_shown
    except Exception:
        pass

    try:
        webview.start(
            gui="edgechromium",
            private_mode=False,
            storage_path=str(storage),
        )
        return 0
    except Exception:
        try:
            webview.start(private_mode=False, storage_path=str(storage))
            return 0
        except Exception as exc:
            import webbrowser

            webbrowser.open(url)
            _error_dialog(
                "No se pudo abrir la ventana de escritorio.\n"
                f"{exc}\n\nSe abrió el panel en el navegador como respaldo.\n"
                "Deje esta instancia en ejecución para seguir usando el sistema."
            )
            return _keep_server()


def main() -> int:
    _set_app_id()
    from desktop.splash import Splash

    splash = Splash()
    try:
        splash.set_status("Buscando puerto local…")
        port, already = pick_port()
        os.environ["SISAT_PORT"] = str(port)
        if not already:
            splash.set_status("Arrancando motor de asistencia…")
            thread = threading.Thread(target=start_engine, args=(port,), daemon=True)
            thread.start()
            splash.set_status("Esperando al panel de control…")
            if not wait_ready(port, thread=thread):
                splash.close()
                detail = ""
                if ENGINE_ERROR:
                    last = ENGINE_ERROR[-1].strip().splitlines()
                    detail = "\n\n" + "\n".join(last[-6:])
                _error_dialog(
                    "No se pudo iniciar el motor local de SISAT."
                    f"{detail}\n\n"
                    "Si ya hay otra ventana abierta, ciérrela e intente de nuevo.\n"
                    f"Registro: {LOG_PATH}"
                )
                return 1
        else:
            splash.set_status("Conectando a la instancia que ya está abierta…")
            time.sleep(0.35)
        splash.set_status("Abriendo ventana de escritorio…")
        time.sleep(0.2)
        splash.close()
        return open_desktop(port)
    except Exception as exc:
        splash.close()
        _error_dialog(f"Error al iniciar SISAT:\n{exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
