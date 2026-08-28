import os

API_BASE_URL = os.getenv("API_BASE_URL", "http://backend:8000").rstrip("/")
API_TIMEOUT_SECONDS = float(os.getenv("API_TIMEOUT_SECONDS", "5"))
