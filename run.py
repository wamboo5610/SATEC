import uvicorn

from app.version import APP_TITLE, APP_VERSION, AUTHOR

if __name__ == "__main__":
    print(f"{APP_TITLE} v{APP_VERSION} — {AUTHOR}")
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=False)