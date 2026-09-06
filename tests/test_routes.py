"""
File:           tests/test_routes.py
Description:    Integration tests for Flask API routes in BEJSON Diagrammer.
Version:        1.0.0
Date:           2026-08-12
Author:         Elton Boehnen
Contact:        eltonboehnen@gmail.com | boehnenelton2024.pages.dev | github.com/boehnenelton
RELATIONAL_ID:  f1e2d3c4-b5a6-9788-091a-2b3c4d5e6f7a
"""

import os
import io
import sys
import json
import pytest
from pathlib import Path

# Wire up sys.path
PROJECT_ROOT = Path(__file__).parent.parent.resolve()
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
for sub in ("lib", "src"):
    p = os.path.join(PROJECT_ROOT, sub)
    if p not in sys.path:
        sys.path.insert(0, p)

from launcher import app
import diagram_mfdb as db


@pytest.fixture
def client(tmp_path, monkeypatch):
    """Flask test client with isolated diagram data directory."""
    tmp_diagrams = tmp_path / "diagrams"
    tmp_diagrams.mkdir(parents=True, exist_ok=True)
    tmp_recent = tmp_path / "recent_diagrams.bejson"

    monkeypatch.setattr(db, "DIAGRAMS_ROOT", str(tmp_diagrams))
    monkeypatch.setattr(db, "RECENT_REGISTRY_PATH", str(tmp_recent))
    monkeypatch.setattr(db, "_current_diagram_id", None)

    db.new_diagram("Route Test Diagram")

    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c

    db._current_diagram_id = None


def test_index_route(client):
    res = client.get("/")
    assert res.status_code == 200
    assert b"BEJSON Diagrammer" in res.data


def test_api_diagram_get(client):
    res = client.get("/api/diagram")
    assert res.status_code == 200
    data = res.get_json()
    assert data["name"] == "Route Test Diagram"
    assert "shapes" in data
    assert "connectors" in data
    assert "territories" in data


def test_api_shape_lifecycle(client):
    # Create shape
    res = client.post("/api/shape", json={
        "cx": 100, "cy": 100, "sizeClass": "medium",
        "kind": "rect", "label": "API Shape", "color": "#334"
    })
    assert res.status_code == 201
    shape = res.get_json()
    shape_id = shape["shape_id"]
    assert shape["shape_label"] == "API Shape"

    # Update shape
    res_up = client.put(f"/api/shape/{shape_id}", json={
        "label": "Updated API Shape", "cx": 150
    })
    assert res_up.status_code == 200
    updated = res_up.get_json()
    assert updated["shape_label"] == "Updated API Shape"
    assert updated["shape_cx"] == 150

    # Add custom field
    res_field = client.post(f"/api/shape/{shape_id}/field", json={
        "name": "notes", "type": "string", "value": "test note"
    })
    assert res_field.status_code == 200
    with_field = res_field.get_json()
    assert with_field["notes"] == "test note"

    # Delete shape
    res_del = client.delete(f"/api/shape/{shape_id}")
    assert res_del.status_code == 200
    assert res_del.get_json() == {"ok": True}


def test_api_connector_lifecycle(client):
    s1 = client.post("/api/shape", json={"cx": 100, "cy": 100}).get_json()
    s2 = client.post("/api/shape", json={"cx": 300, "cy": 100}).get_json()

    # Create connector
    res = client.post("/api/connector", json={
        "fromFk": s1["shape_id"], "toFk": s2["shape_id"], "flow": "forward"
    })
    assert res.status_code == 201
    conn = res.get_json()
    conn_id = conn["conn_id"]

    # Update connector
    res_up = client.put(f"/api/connector/{conn_id}", json={"flow": "bidirectional"})
    assert res_up.status_code == 200

    # Delete connector
    res_del = client.delete(f"/api/connector/{conn_id}")
    assert res_del.status_code == 200


def test_api_territory_lifecycle(client):
    res = client.post("/api/territory", json={
        "kind": "rect", "cx": 200, "cy": 200, "size": 200,
        "label": "Territory A", "color": "#2a6496"
    })
    assert res.status_code == 201
    terr = res.get_json()
    terr_id = terr["terr_id"]

    res_up = client.put(f"/api/territory/{terr_id}", json={"label": "Territory B"})
    assert res_up.status_code == 200

    res_del = client.delete(f"/api/territory/{terr_id}")
    assert res_del.status_code == 200


def test_api_export_import(client):
    client.post("/api/shape", json={"cx": 100, "cy": 100, "label": "Export Node"})

    # Export
    res_exp = client.get("/api/export")
    assert res_exp.status_code == 200
    assert res_exp.mimetype == "application/json"
    exported_bytes = res_exp.data

    # Import
    data = {"file": (io.BytesIO(exported_bytes), "my_export.mfdb132.bejson")}
    res_imp = client.post("/api/import", data=data, content_type="multipart/form-data")
    assert res_imp.status_code == 201
    imported = res_imp.get_json()
    assert imported["diagram"]["shapes"][0]["shape_label"] == "Export Node"