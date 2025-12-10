from fastapi import FastAPI

app = FastAPI(
    title="Real Estate Listings API",
    description="A backend API for searching and analyzing real estate listings.",
    version="0.1.0"
)

@app.get("/")
def read_root():
    return {"message": "Welcome to the Real Estate Listings API! 🚀"}