"""Pantalla de arranque nativa mientras sube el motor local."""

from __future__ import annotations

from app.paths import icon_path, login_bg_path
from app.version import APP_NAME, APP_TITLE, APP_VERSION, AUTHOR


class Splash:
    def __init__(self) -> None:
        self.root = None
        self.status = None
        try:
            import tkinter as tk
            from tkinter import font as tkfont
        except Exception:
            return

        root = tk.Tk()
        self.root = root
        root.overrideredirect(True)
        root.attributes("-topmost", True)
        width, height = 520, 320
        screen_w = root.winfo_screenwidth()
        screen_h = root.winfo_screenheight()
        x = (screen_w - width) // 2
        y = (screen_h - height) // 2
        root.geometry(f"{width}x{height}+{x}+{y}")
        root.configure(bg="#0E2A22")

        canvas = tk.Canvas(root, width=width, height=height, highlightthickness=0, bd=0)
        canvas.pack(fill="both", expand=True)
        self._draw_background(canvas, width, height)

        canvas.create_rectangle(18, 18, width - 18, height - 18, outline="#C4A04A", width=1)
        self._draw_icon(canvas)

        title_font = tkfont.Font(family="Segoe UI", size=22, weight="bold")
        sub_font = tkfont.Font(family="Segoe UI", size=11)
        author_font = tkfont.Font(family="Segoe UI", size=10, weight="bold")
        canvas.create_text(270, 118, text=APP_NAME, fill="#F3FAF6", font=title_font, anchor="w")
        canvas.create_text(270, 150, text=APP_TITLE, fill="#D4E2DA", font=sub_font, anchor="w")
        canvas.create_text(270, 178, text=f"Aplicación de escritorio  ·  v{APP_VERSION}", fill="#9AB3A8", font=sub_font, anchor="w")
        canvas.create_text(270, 214, text=f"Autor  {AUTHOR}", fill="#C4A04A", font=author_font, anchor="w")

        self.status = tk.Label(
            root,
            text="Iniciando motor local…",
            bg="#0E2A22",
            fg="#E8F3EC",
            font=("Segoe UI", 10),
        )
        self.status.place(x=36, y=height - 48, width=width - 72)
        root.update()

    def _draw_background(self, canvas, width: int, height: int) -> None:
        bg = login_bg_path()
        if not bg.exists():
            canvas.configure(bg="#0E2A22")
            return
        try:
            from PIL import Image, ImageTk, ImageEnhance

            image = Image.open(bg).convert("RGB")
            image = image.resize((width, height))
            image = ImageEnhance.Brightness(image).enhance(0.42)
            self._bg_photo = ImageTk.PhotoImage(image)
            canvas.create_image(0, 0, image=self._bg_photo, anchor="nw")
            canvas.create_rectangle(0, 0, width, height, fill="#0E2A22", stipple="gray50")
        except Exception:
            canvas.configure(bg="#0E2A22")

    def _draw_icon(self, canvas) -> None:
        path = icon_path()
        if not path.exists():
            return
        try:
            from PIL import Image, ImageTk

            image = Image.open(path).convert("RGBA").resize((88, 88))
            self._icon_photo = ImageTk.PhotoImage(image)
            canvas.create_image(70, 148, image=self._icon_photo)
        except Exception:
            pass

    def set_status(self, text: str) -> None:
        if self.root is None or self.status is None:
            return
        try:
            self.status.config(text=text)
            self.root.update()
        except Exception:
            pass

    def close(self) -> None:
        if self.root is None:
            return
        try:
            self.root.destroy()
        except Exception:
            pass
        self.root = None
