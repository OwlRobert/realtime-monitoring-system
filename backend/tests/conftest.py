import os

# Settings require a JWT secret; supply a throwaway one before app modules load.
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-not-used-outside-tests")
