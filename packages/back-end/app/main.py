from fastapi import FastAPI

app = FastAPI(title="Diagram Reader API")


@app.get("/health")
async def healthcheck():
    return {"status": "ok"}
