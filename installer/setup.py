"""Instalador gráfico SATEC — WAMBOO TIC."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import threading
from pathlib import Path

try:
    import tkinter as tk
    from tkinter import filedialog, messagebox
except Exception as exc:  # pragma: no cover
    print("Se necesita tkinter para el instalador:", exc)
    sys.exit(1)

HERE = Path(__file__).resolve().parent
PAYLOAD = HERE / "payload"
if not PAYLOAD.exists():
    PAYLOAD = HERE.parent

APP_TITLE = "SATEC — Sistema de Asistencia Técnico"
AUTHOR = "WAMBOO TIC"
DEFAULT_DIR = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "WAMBOOTIC" / "SATEC"


def find_python() -> str | None:
    for cmd in ("python", "py"):
        try:
            proc = subprocess.run(
                [cmd, "-c", "import sys; print(sys.executable)"],
                capture_output=True,
                text=True,
                timeout=8,
                check=False,
            )
            if proc.returncode == 0 and proc.stdout.strip():
                return proc.stdout.strip()
        except Exception:
            continue
    return None


def create_shortcut(target: Path, dest: Path, workdir: Path, icon: Path, description: str) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)

    def q(value: Path | str) -> str:
        return str(value).replace("'", "''")

    script = (
        f"$s = (New-Object -ComObject WScript.Shell).CreateShortcut('{q(dest)}'); "
        f"$s.TargetPath = '{q(target)}'; "
        f"$s.WorkingDirectory = '{q(workdir)}'; "
        f"$s.IconLocation = '{q(icon)}'; "
        f"$s.Description = '{q(description)}'; "
        "$s.Save()"
    )
    subprocess.run(
        ["powershell", "-NoProfile", "-Command", script],
        check=False,
        capture_output=True,
    )


class Installer(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title(f"Instalar {APP_TITLE} | {AUTHOR}")
        self.geometry("560x420")
        self.resizable(False, False)
        self.configure(bg="#0E2A22")
        self.path_var = tk.StringVar(value=str(DEFAULT_DIR))
        self.status_var = tk.StringVar(value="Listo para instalar.")
        self._busy = False
        self._build()

    def _build(self) -> None:
        pad = {"padx": 24, "pady": 4}
        tk.Label(self, text="SATEC", fg="#F3FAF6", bg="#0E2A22", font=("Segoe UI", 22, "bold")).pack(anchor="w", **pad)
        tk.Label(self, text=APP_TITLE, fg="#D4E2DA", bg="#0E2A22", font=("Segoe UI", 11)).pack(anchor="w", padx=24)
        tk.Label(self, text=f"Autor  {AUTHOR}  ·  Aplicación de escritorio para PC", fg="#C4A04A", bg="#0E2A22", font=("Segoe UI", 10, "bold")).pack(anchor="w", padx=24, pady=(4, 16))

        tk.Label(self, text="Carpeta de instalación", fg="#E8F3EC", bg="#0E2A22", font=("Segoe UI", 10)).pack(anchor="w", padx=24)
        row = tk.Frame(self, bg="#0E2A22")
        row.pack(fill="x", padx=24, pady=6)
        entry = tk.Entry(row, textvariable=self.path_var, font=("Segoe UI", 10))
        entry.pack(side="left", fill="x", expand=True, ipady=6)
        tk.Button(row, text="Examinar", command=self._browse, font=("Segoe UI", 9)).pack(side="left", padx=(8, 0))

        tk.Label(
            self,
            text="Se creará un acceso directo en el escritorio y en el menú inicio.\nSi ya hay una instalación, se conservará la base de datos.",
            fg="#9AB3A8",
            bg="#0E2A22",
            font=("Segoe UI", 9),
            justify="left",
        ).pack(anchor="w", padx=24, pady=(8, 12))

        self.progress = tk.Label(self, textvariable=self.status_var, fg="#E8F3EC", bg="#0E2A22", font=("Segoe UI", 9))
        self.progress.pack(anchor="w", padx=24, pady=(8, 4))

        btns = tk.Frame(self, bg="#0E2A22")
        btns.pack(fill="x", padx=24, pady=18)
        tk.Button(btns, text="Cancelar", command=self.destroy, font=("Segoe UI", 10), width=12).pack(side="right")
        self.ok_btn = tk.Button(
            btns,
            text="Instalar",
            command=self._start,
            font=("Segoe UI", 10, "bold"),
            bg="#1B8A58",
            fg="white",
            width=14,
        )
        self.ok_btn.pack(side="right", padx=(0, 8))

    def _browse(self) -> None:
        chosen = filedialog.askdirectory(initialdir=self.path_var.get())
        if chosen:
            self.path_var.set(chosen)

    def _start(self) -> None:
        if self._busy:
            return
        python = find_python()
        if not python:
            messagebox.showerror(
                AUTHOR,
                "No se encontró Python 3.12 o superior.\nInstálelo desde https://www.python.org/downloads/\nMarque 'Add python.exe to PATH'.",
            )
            return
        dest = Path(self.path_var.get().strip())
        if not dest:
            messagebox.showerror(AUTHOR, "Elige una carpeta.")
            return
        self._busy = True
        self.ok_btn.config(state="disabled")
        threading.Thread(target=self._install, args=(python, dest), daemon=True).start()

    def _set_status(self, text: str) -> None:
        self.after(0, lambda: self.status_var.set(text))

    def _install(self, python: str, dest: Path) -> None:
        try:
            dest.mkdir(parents=True, exist_ok=True)
            self._set_status("Copiando archivos…")
            skip = {"venv", "__pycache__", "dist", "data", "recursos", ".git", "webview"}
            if PAYLOAD.name == "payload":
                for item in PAYLOAD.iterdir():
                    target = dest / item.name
                    if item.name in skip:
                        continue
                    if item.is_dir():
                        if target.exists():
                            shutil.rmtree(target)
                        shutil.copytree(item, target, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
                    else:
                        shutil.copy2(item, target)
            else:
                for name in ("app", "desktop", "assets", "main.py", "run.py", "requirements.txt", "INICIAR.bat", "INICIAR_CONSOLA.bat", "INICIAR_SERVIDOR.bat", "INSTALAR.bat", "DESINSTALAR.bat", "README.md", "pyproject.toml"):
                    src = PAYLOAD / name
                    if not src.exists():
                        continue
                    target = dest / name
                    if src.is_dir():
                        if target.exists():
                            shutil.rmtree(target)
                        shutil.copytree(src, target, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
                    else:
                        shutil.copy2(src, target)

            (dest / "data").mkdir(exist_ok=True)
            self._set_status("Creando entorno virtual…")
            subprocess.run([python, "-m", "venv", str(dest / "venv")], check=True)
            venv_py = dest / "venv" / "Scripts" / "python.exe"
            self._set_status("Instalando dependencias…")
            subprocess.run([str(venv_py), "-m", "pip", "install", "--upgrade", "pip"], check=True)
            subprocess.run([str(venv_py), "-m", "pip", "install", "-r", str(dest / "requirements.txt")], check=True)
            self._set_status("Creando accesos directos…")
            icon = dest / "assets" / "icon.ico"
            start = dest / "INICIAR.bat"
            desktop = Path.home() / "Desktop"
            if not desktop.exists():
                desktop = Path.home() / "Escritorio"
            create_shortcut(start, desktop / "SATEC WAMBOO TIC.lnk", dest, icon, APP_TITLE)
            start_menu = Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "WAMBOO TIC"
            create_shortcut(start, start_menu / "SATEC.lnk", dest, icon, APP_TITLE)
            self._write_uninstall(dest)
            self._set_status("Instalación lista.")
            self.after(0, lambda: self._done(dest))
        except Exception as exc:
            self._busy = False
            self.after(0, lambda: self.ok_btn.config(state="normal"))
            self.after(0, lambda: messagebox.showerror(AUTHOR, f"Falló la instalación:\n{exc}"))

    def _write_uninstall(self, dest: Path) -> None:
        bat = dest / "DESINSTALAR.bat"
        bat.write_text(
            "\r\n".join(
                [
                    "@echo off",
                    "chcp 65001 >nul",
                    "echo Esto elimina SATEC de este equipo. La carpeta de datos se conserva si elige N.",
                    "pause",
                    f'del "%USERPROFILE%\\Desktop\\SATEC WAMBOO TIC.lnk" >nul 2>&1',
                    f'del "%APPDATA%\\Microsoft\\Windows\\Start Menu\\Programs\\WAMBOO TIC\\SATEC.lnk" >nul 2>&1',
                    f'cd /d "{dest.parent}"',
                    f'rmdir /s /q "{dest.name}"',
                    "echo Desinstalado.",
                    "pause",
                ]
            )
            + "\r\n",
            encoding="utf-8",
        )

    def _done(self, dest: Path) -> None:
        if messagebox.askyesno(AUTHOR, f"SATEC se instaló en:\n{dest}\n\n¿Abrir ahora?"):
            subprocess.Popen(["cmd.exe", "/c", str(dest / "INICIAR.bat")], cwd=str(dest))
        self.destroy()


def main() -> int:
    app = Installer()
    app.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
