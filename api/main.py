from fastapi import FastAPI

app = FastAPI(
    title="Evidence Loop API",
    description="Evidence-backed repository intelligece",
    version="0.1.0",
)

@app.get("/")
def home():
    return {
        "project": "EvidenceLoop",
        "version": "0.1.0",
        "status": "running",
    }

@app.get("/health")
def health():
    return { "status": "healthy" }