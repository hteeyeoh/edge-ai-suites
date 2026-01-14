from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from backend.config import APP_PORT, UI_DIR
from backend.routes import (
    config_router,
    metrics_router,
    models_router,
    pipelines_router,
    runs_router,
)

app = FastAPI(title="Live Video Captioning API")

# Include all routers
app.include_router(config_router)
app.include_router(metrics_router)
app.include_router(models_router)
app.include_router(pipelines_router)
app.include_router(runs_router)


@app.get("/")
async def root() -> FileResponse:
    return FileResponse(UI_DIR / "index.html")


app.mount("/", StaticFiles(directory=UI_DIR, html=True), name="ui")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=APP_PORT, reload=True)
