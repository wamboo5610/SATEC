"""Instalador EXE de SATEC — doble clic, conserva la base de datos."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import threading
import urllib.request
import zipfile
from pathlib import Path

try:
    import tkinter as tk
    from tkinter import filedialog, messagebox, scrolledtext
except Exception as exc:  # pragma: no cover
    print("Se necesita tkinter:", exc)
    sys.exit(1)

APP_NAME = "SATEC"
AUTHOR = "WAMBOO TIC"
PY_EMBED_URL = "https://www.python.org/ftp/python/3.12.10/python-3.12.10-embed-amd64.zip"
GET_PIP_URL = "https://bootstrap.pypa.io/get-pip.py"

BG = "#F4F8F6"
PAPER = "#FFFFFF"
INK = "#16382C"
MUTED = "#5E746B"
ACCENT = "#1B8A58"
LINE = "#D4E2DA"
GOLD = "#B8860B"

TERMS = """TÉRMINOS Y CONDICIONES DE USO — SATEC
Software WAMBOO TIC  ·  WAMBOO GROUP

Al instalar y usar SATEC (Sistema de Reporte de Asistencia y Control) usted acepta lo siguiente:

1. Licencia de uso
Se le concede una licencia no exclusiva para instalar y usar SATEC en equipos de su entidad o empresa. El software es propiedad de WAMBOO TIC. No está permitido revenderlo, alquilarlo ni presentarlo como propio.

2. Uso autorizado
SATEC está destinado al control de asistencia laboral con relojes biométricos y reportes asociados. El usuario es responsable de usarlo conforme a la ley y a las políticas internas de su institución.

3. Datos
Las marcaciones, empleados y cuentas se guardan en este equipo (base de datos local). WAMBOO TIC no recibe automáticamente esos datos. Usted es responsable de respaldarlos y de proteger el acceso (usuarios y contraseñas).

4. Actualizaciones
El programa puede avisar cuando exista una versión nueva. Instalar una actualización no borra la base de datos, salvo que usted elija restaurar un respaldo.

5. Garantía
SATEC se entrega «tal cual». WAMBOO TIC no garantiza un funcionamiento ininterrumpido ni se hace responsable por pérdida de datos, interrupciones o uso indebido. Se recomienda exportar respaldos con regularidad.

6. Soporte
Para soporte técnico contacte a WAMBOO TIC.

Si no acepta estos términos, cancele la instalación."""


def app_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS)
    return Path(__file__).resolve().parent


def payload_dir() -> Path:
    here = app_dir()
    for candidate in (here / "payload", here.parent / "payload", here.parent):
        if (candidate / "main.py").exists() and (candidate / "app").exists():
            return candidate
    raise FileNotFoundError("No se encontró el contenido de SATEC en el instalador.")


def read_identity(payload: Path) -> tuple[str, str]:
    path = payload / "app" / "version.py"
    name, version = APP_NAME, "2.0.0"
    if path.exists():
        text = path.read_text(encoding="utf-8")
        m = re.search(r'APP_NAME\s*=\s*["\']([^"\']+)["\']', text)
        if m:
            name = m.group(1)
        m = re.search(r'APP_VERSION\s*=\s*["\']([^"\']+)["\']', text)
        if m:
            version = m.group(1)
    return name, version


def default_dest() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(base) / "WAMBOOTIC" / "SATEC"


def run(cmd: list[str], cwd: Path | None = None) -> None:
    flags = 0
    if sys.platform == "win32":
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    proc = subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
        creationflags=flags,
    )
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip()
        raise RuntimeError(err[-800:] or f"Error al ejecutar: {cmd[0]}")


def find_system_python() -> str | None:
    commands = []
    py = shutil.which("py")
    if py:
        for args in (["-3.12"], ["-3"]):
            try:
                proc = subprocess.run(
                    [py, *args, "-c", "import sys; print(sys.executable)"],
                    capture_output=True,
                    text=True,
                    timeout=8,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
                if proc.returncode == 0 and proc.stdout.strip():
                    return proc.stdout.strip()
            except Exception:
                pass
    for name in ("python", "python3"):
        found = shutil.which(name)
        if found:
            commands.append(found)
    for exe in commands:
        try:
            proc = subprocess.run(
                [exe, "-c", "import sys; raise SystemExit(0 if sys.version_info >= (3, 12) else 1)"],
                capture_output=True,
                timeout=8,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            if proc.returncode == 0:
                return exe
        except Exception:
            continue
    return None


def configure_embed_runtime(runtime: Path) -> None:
    """El Python embebido ignora el directorio de trabajo: hay que poner SATEC en sys.path."""
    runtime.mkdir(parents=True, exist_ok=True)
    dest = str(runtime.resolve().parent)
    pth = next(runtime.glob("python*._pth"), None)
    if pth:
        pth.write_text(
            "python312.zip\n"
            ".\n"
            "..\n"
            f"{dest}\n"
            "Lib\n"
            "Lib\\site-packages\n"
            "import site\n",
            encoding="utf-8",
        )
    (runtime / "sitecustomize.py").write_text(
        "import sys\n"
        "from pathlib import Path\n"
        "_root = str(Path(__file__).resolve().parent.parent)\n"
        "if _root not in sys.path:\n"
        "    sys.path.insert(0, _root)\n",
        encoding="utf-8",
    )
    site_packages = runtime / "Lib" / "site-packages"
    site_packages.mkdir(parents=True, exist_ok=True)
    (site_packages / "satec.pth").write_text(dest + "\n", encoding="utf-8")


def engine_check_code(dest: Path) -> str:
    root = json.dumps(str(dest))
    return (
        "import sys; "
        f"sys.path.insert(0, {root}); "
        "from app import database, auth; "
        "from app.version import APP_NAME, APP_VERSION; "
        "database.init_db(); auth.init_auth(); "
        "print(APP_NAME, APP_VERSION)"
    )


def install_embed_python(runtime: Path, log) -> Path:
    log("Descargando Python portátil…")
    runtime.mkdir(parents=True, exist_ok=True)
    zip_path = Path(os.environ.get("TEMP", ".")) / "satec-python-embed.zip"
    urllib.request.urlretrieve(PY_EMBED_URL, zip_path)
    with zipfile.ZipFile(zip_path) as zf:
        zf.extractall(runtime)
    zip_path.unlink(missing_ok=True)
    configure_embed_runtime(runtime)
    get_pip = runtime / "get-pip.py"
    urllib.request.urlretrieve(GET_PIP_URL, get_pip)
    py = runtime / "python.exe"
    log("Instalando pip…")
    run([str(py), str(get_pip), "--no-warn-script-location"])
    log("Instalando herramientas de empaquetado…")
    run([str(py), "-m", "pip", "install", "--upgrade", "pip", "setuptools", "wheel"])
    configure_embed_runtime(runtime)
    return py


def resolve_python(dest: Path, log) -> Path:
    venv_py = dest / "venv" / "Scripts" / "python.exe"
    if venv_py.exists():
        return venv_py
    runtime_py = dest / "runtime" / "python.exe"
    if runtime_py.exists():
        configure_embed_runtime(dest / "runtime")
        return runtime_py
    system = find_system_python()
    if system:
        log("Creando entorno virtual…")
        run([system, "-m", "venv", str(dest / "venv")])
        return dest / "venv" / "Scripts" / "python.exe"
    return install_embed_python(dest / "runtime", log)


def copy_payload(src: Path, dest: Path, log) -> None:
    log(f"Copiando archivos a {dest}…")
    dest.mkdir(parents=True, exist_ok=True)
    skip = {"data", "venv", "runtime", "__pycache__", "dist", "webview", ".git"}
    for item in src.iterdir():
        if item.name in skip or item.name.endswith(".pyc"):
            continue
        target = dest / item.name
        if item.is_dir():
            if target.exists():
                shutil.rmtree(target)
            shutil.copytree(item, target, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        else:
            shutil.copy2(item, target)
    (dest / "data").mkdir(exist_ok=True)
    log("Base de datos conservada (si ya existía).")


def make_shortcut(target: Path, link: Path, workdir: Path, icon: Path, description: str) -> None:
    link.parent.mkdir(parents=True, exist_ok=True)

    def q(value: Path | str) -> str:
        return str(value).replace("'", "''")

    script = (
        f"$s = (New-Object -ComObject WScript.Shell).CreateShortcut('{q(link)}'); "
        f"$s.TargetPath = '{q(target)}'; "
        f"$s.WorkingDirectory = '{q(workdir)}'; "
        f"$s.IconLocation = '{q(icon)}'; "
        f"$s.Description = '{q(description)}'; "
        "$s.Save()"
    )
    subprocess.run(
        ["powershell", "-NoProfile", "-Command", script],
        capture_output=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


class SetupApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.payload = payload_dir()
        self.app_name, self.version = read_identity(self.payload)
        self.title(f"Instalar {self.app_name} {self.version}  |  {AUTHOR}")
        self.geometry("640x580")
        self.minsize(620, 540)
        self.configure(bg=BG)
        self.path_var = tk.StringVar(value=str(default_dest()))
        self.accept_var = tk.BooleanVar(value=False)
        self.desktop_var = tk.BooleanVar(value=True)
        self.startmenu_var = tk.BooleanVar(value=True)
        self._busy = False
        self._page = 0
        self._build()
        self._show(0)

    def _label(self, parent, text, **kwargs) -> tk.Label:
        opts = {"fg": INK, "bg": BG, "font": ("Segoe UI", 10), "anchor": "w"}
        opts.update(kwargs)
        return tk.Label(parent, text=text, **opts)

    def _btn(self, parent, text, command, primary=False) -> tk.Button:
        if primary:
            return tk.Button(
                parent, text=text, command=command, font=("Segoe UI", 10, "bold"),
                bg=ACCENT, fg="white", activebackground="#15764A", activeforeground="white",
                relief="flat", padx=16, pady=7, cursor="hand2",
            )
        return tk.Button(
            parent, text=text, command=command, font=("Segoe UI", 10),
            bg=PAPER, fg=INK, activebackground=LINE, relief="solid",
            bd=1, padx=14, pady=6, cursor="hand2",
        )

    def _build(self) -> None:
        header = tk.Frame(self, bg=PAPER, highlightbackground=LINE, highlightthickness=1)
        header.pack(fill="x")
        inner = tk.Frame(header, bg=PAPER)
        inner.pack(fill="x", padx=24, pady=14)
        tk.Label(inner, text=self.app_name, fg=ACCENT, bg=PAPER, font=("Segoe UI", 20, "bold")).pack(anchor="w")
        tk.Label(
            inner,
            text=f"Sistema de Reporte de Asistencia y Control   v{self.version}",
            fg=MUTED, bg=PAPER, font=("Segoe UI", 10),
        ).pack(anchor="w")
        tk.Label(inner, text=f"Software {AUTHOR}", fg=GOLD, bg=PAPER, font=("Segoe UI", 9, "bold")).pack(anchor="w", pady=(2, 0))

        self.body = tk.Frame(self, bg=BG)
        self.body.pack(fill="both", expand=True)

        self.page_terms = tk.Frame(self.body, bg=BG)
        self.page_opts = tk.Frame(self.body, bg=BG)
        self.page_prog = tk.Frame(self.body, bg=BG)

        self._build_terms()
        self._build_opts()
        self._build_prog()

        self.footer = tk.Frame(self, bg=PAPER, highlightbackground=LINE, highlightthickness=1)
        self.footer.pack(fill="x", side="bottom")
        bar = tk.Frame(self.footer, bg=PAPER)
        bar.pack(fill="x", padx=20, pady=12)
        self.cancel_btn = self._btn(bar, "Cancelar", self.destroy)
        self.cancel_btn.pack(side="left")
        self.next_btn = self._btn(bar, "Siguiente", self._next, primary=True)
        self.next_btn.pack(side="right")
        self.back_btn = self._btn(bar, "Atrás", self._back)
        self.back_btn.pack(side="right", padx=(0, 8))

    def _build_terms(self) -> None:
        p = self.page_terms
        self._label(p, "Términos y condiciones", font=("Segoe UI", 13, "bold")).pack(anchor="w", padx=24, pady=(18, 8))
        self._label(p, "Lea el siguiente acuerdo. Debe aceptarlo para continuar.", fg=MUTED).pack(anchor="w", padx=24)
        box = scrolledtext.ScrolledText(
            p, font=("Segoe UI", 9), bg=PAPER, fg=INK, relief="solid", bd=1,
            wrap="word", padx=10, pady=10,
        )
        box.pack(fill="both", expand=True, padx=24, pady=10)
        box.insert("1.0", TERMS)
        box.configure(state="disabled")
        accept = tk.Checkbutton(
            p, text="Acepto los términos y condiciones de uso de SATEC",
            variable=self.accept_var, command=self._sync_buttons,
            bg=BG, fg=INK, selectcolor=PAPER, activebackground=BG,
            font=("Segoe UI", 10, "bold"), anchor="w",
        )
        accept.pack(anchor="w", padx=24, pady=(4, 12))

    def _build_opts(self) -> None:
        p = self.page_opts
        self._label(p, "Opciones de instalación", font=("Segoe UI", 13, "bold")).pack(anchor="w", padx=24, pady=(18, 8))
        self._label(p, "Carpeta de instalación", fg=MUTED).pack(anchor="w", padx=24)
        row = tk.Frame(p, bg=BG)
        row.pack(fill="x", padx=24, pady=6)
        tk.Entry(row, textvariable=self.path_var, font=("Segoe UI", 10), relief="solid", bd=1).pack(
            side="left", fill="x", expand=True, ipady=7, ipadx=8
        )
        self._btn(row, "Examinar", self._browse).pack(side="left", padx=(8, 0))

        card = tk.Frame(p, bg=PAPER, highlightbackground=LINE, highlightthickness=1)
        card.pack(fill="x", padx=24, pady=(16, 8))
        inner = tk.Frame(card, bg=PAPER)
        inner.pack(fill="x", padx=16, pady=12)
        tk.Label(inner, text="Accesos directos", bg=PAPER, fg=INK, font=("Segoe UI", 10, "bold")).pack(anchor="w")
        tk.Checkbutton(
            inner, text="Crear icono en el escritorio",
            variable=self.desktop_var, bg=PAPER, fg=INK, selectcolor=PAPER,
            activebackground=PAPER, font=("Segoe UI", 10), anchor="w",
        ).pack(anchor="w", pady=(8, 2))
        tk.Checkbutton(
            inner, text="Crear acceso en el menú Inicio",
            variable=self.startmenu_var, bg=PAPER, fg=INK, selectcolor=PAPER,
            activebackground=PAPER, font=("Segoe UI", 10), anchor="w",
        ).pack(anchor="w", pady=2)
        self._label(
            p,
            "Si SATEC ya estaba instalado en esta carpeta, se conserva la base de datos.",
            fg=MUTED, wraplength=560,
        ).pack(anchor="w", padx=24, pady=(12, 8))

    def _build_prog(self) -> None:
        p = self.page_prog
        self._label(p, "Instalando SATEC", font=("Segoe UI", 13, "bold")).pack(anchor="w", padx=24, pady=(18, 8))
        self._label(p, "Espere mientras se copian los archivos y se configuran los componentes.", fg=MUTED).pack(anchor="w", padx=24)
        self.log_box = scrolledtext.ScrolledText(
            p, height=14, font=("Consolas", 9), bg=PAPER, fg=INK, relief="solid", bd=1
        )
        self.log_box.pack(fill="both", expand=True, padx=24, pady=12)
        self.log_box.configure(state="disabled")

    def _show(self, index: int) -> None:
        self._page = index
        for page in (self.page_terms, self.page_opts, self.page_prog):
            page.pack_forget()
        (self.page_terms, self.page_opts, self.page_prog)[index].pack(fill="both", expand=True)
        self._sync_buttons()

    def _sync_buttons(self) -> None:
        self.back_btn.configure(state="normal" if self._page == 1 and not self._busy else "disabled")
        if self._page == 0:
            self.next_btn.configure(text="Siguiente", state="normal" if self.accept_var.get() else "disabled")
        elif self._page == 1:
            self.next_btn.configure(text="Instalar", state="normal")
        else:
            self.next_btn.configure(text="Instalar", state="disabled")
        if self._busy:
            self.next_btn.configure(state="disabled")
            self.back_btn.configure(state="disabled")
            self.cancel_btn.configure(state="disabled")

    def _back(self) -> None:
        if self._page == 1:
            self._show(0)

    def _next(self) -> None:
        if self._page == 0:
            if not self.accept_var.get():
                messagebox.showinfo(self.app_name, "Debe aceptar los términos y condiciones para continuar.")
                return
            self._show(1)
            return
        if self._page == 1:
            self._start()

    def _browse(self) -> None:
        chosen = filedialog.askdirectory(initialdir=self.path_var.get())
        if chosen:
            self.path_var.set(chosen)

    def log(self, text: str) -> None:
        def _append() -> None:
            self.log_box.configure(state="normal")
            self.log_box.insert("end", text + "\n")
            self.log_box.see("end")
            self.log_box.configure(state="disabled")

        self.after(0, _append)

    def _start(self) -> None:
        if self._busy:
            return
        if not self.accept_var.get():
            messagebox.showinfo(self.app_name, "Debe aceptar los términos y condiciones para instalar.")
            return
        dest = Path(self.path_var.get().strip())
        if not dest:
            messagebox.showerror(self.app_name, "Elige una carpeta.")
            return
        self._busy = True
        self._show(2)
        self.log("Iniciando instalación…")
        threading.Thread(target=self._install, args=(dest,), daemon=True).start()

    def _install(self, dest: Path) -> None:
        try:
            copy_payload(self.payload, dest, self.log)
            python = resolve_python(dest, self.log)
            self.log("Instalando herramientas de empaquetado…")
            run([str(python), "-m", "pip", "install", "--upgrade", "pip", "setuptools", "wheel"])
            self.log("Instalando componentes de SATEC (internet)…")
            try:
                run([str(python), "-m", "pip", "install", "-r", str(dest / "requirements.txt")])
            except RuntimeError as exc:
                if "setuptools" in str(exc).lower() or "build_meta" in str(exc).lower():
                    self.log("Faltaba setuptools. Reintentando…")
                    run([str(python), "-m", "pip", "install", "--upgrade", "setuptools", "wheel"])
                    run([str(python), "-m", "pip", "install", "-r", str(dest / "requirements.txt")])
                else:
                    raise
            if (dest / "runtime" / "python.exe").exists():
                configure_embed_runtime(dest / "runtime")
            self.log("Comprobando motor…")
            run([str(python), "-c", engine_check_code(dest)], cwd=dest)
            icon = dest / "assets" / "icon.ico"
            iniciar = dest / "INICIAR.bat"
            if self.desktop_var.get():
                desktop = Path(os.environ.get("USERPROFILE", str(Path.home()))) / "Desktop"
                if not desktop.exists():
                    desktop = Path.home() / "Escritorio"
                make_shortcut(iniciar, desktop / f"{self.app_name} {AUTHOR}.lnk", dest, icon, f"{self.app_name} - {AUTHOR}")
                self.log("Icono de escritorio creado.")
            else:
                self.log("Sin icono en el escritorio (opción desmarcada).")
            if self.startmenu_var.get():
                start = Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / AUTHOR
                make_shortcut(iniciar, start / f"{self.app_name}.lnk", dest, icon, f"{self.app_name} - {AUTHOR}")
                self.log("Acceso en el menú Inicio creado.")
            self.log("Instalación lista.")
            self.after(0, lambda: self._done(dest))
        except Exception as exc:
            self._busy = False
            self.after(0, self._sync_buttons)
            self.after(0, lambda: self.cancel_btn.configure(state="normal"))
            msg = str(exc)
            if "setuptools" in msg.lower() or "build_meta" in msg.lower():
                msg = (
                    "Faltaban herramientas de instalación en esta PC.\n"
                    "Cierre este instalador, borre la carpeta:\n"
                    f"{dest}\n"
                    "y vuelva a ejecutar SATEC-Instalador.exe."
                )
            elif "No module named 'app'" in msg:
                msg = (
                    "El motor no encontró los archivos de SATEC.\n"
                    "Cierre este instalador, borre la carpeta:\n"
                    f"{dest}\n"
                    "y vuelva a ejecutar el instalador nuevo (SATEC-Instalador.exe)."
                )
            self.after(0, lambda: messagebox.showerror(self.app_name, f"No se pudo instalar:\n{msg}"))

    def _done(self, dest: Path) -> None:
        self.cancel_btn.configure(state="normal", text="Cerrar")
        if messagebox.askyesno(
            self.app_name,
            f"{self.app_name} se instaló en:\n{dest}\n\n"
            "Usuario: admin\nContraseña: admin123\nCámbiela al entrar.\n\n¿Abrir ahora?",
        ):
            subprocess.Popen(["cmd.exe", "/c", str(dest / "INICIAR.bat")], cwd=str(dest))
        self.destroy()


def main() -> int:
    try:
        payload_dir()
    except FileNotFoundError as exc:
        try:
            messagebox.showerror(APP_NAME, str(exc))
        except Exception:
            print(exc)
        return 1
    SetupApp().mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
