"""
Entry point for running the app locally.

Run this file from the project root:

    python run.py

Do NOT run app/__init__.py directly -- it's a package file meant to be
imported, not executed. This file is the one you actually run.
"""
from app import create_app

app = create_app()

if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)
