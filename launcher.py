"""
File:           launcher.py
Description:    Entry point for the BEJSON Diagrammer Flask app. Self-locates
                 its own path, wires up lib/ and src/ on sys.path, ensures the
                 MFDB database exists, and starts the dev server.
Version:        1.0.0
Date:           2026-07-06
Author:         Elton Boehnen
Contact:        eltonboehnen@gmail.com | boehnenelton2024.pages.dev | github.com/boehnenelton
RELATIONAL_ID:  6c1e2a4f-7d3b-4e8a-b1f0-2a9c8d5e6f10
"""

import os
import sys
from pathlib import Path

SCRIPT_PATH = str(Path(__file__).parent.resolve())
for sub in ("lib", "src"):
    p = os.path.join(SCRIPT_PATH, sub)
    if p not in sys.path:
        sys.path.insert(0, p)

from flask import Flask
import diagram_mfdb as db
from routes import bp

app = Flask(
    __name__,
    template_folder=os.path.join(SCRIPT_PATH, "templates"),
    static_folder=os.path.join(SCRIPT_PATH, "static"),
)
app.register_blueprint(bp)

DEBUG_MODE = False  # persistent debug switch — flip to True to enable verbose logging


def main():
    db.ensure_default_diagram()
    if DEBUG_MODE:
        import logging
        logging.basicConfig(level=logging.DEBUG)
    app.run(host="0.0.0.0", port=5000, debug=DEBUG_MODE)


if __name__ == "__main__":
    main()
