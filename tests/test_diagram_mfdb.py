"""
File:           tests/test_diagram_mfdb.py
Description:    Unit tests for diagram_mfdb persistence service in BEJSON Diagrammer.
Version:        1.0.0
Date:           2026-08-12
Author:         Elton Boehnen
Contact:        eltonboehnen@gmail.com | boehnenelton2024.pages.dev | github.com/boehnenelton
RELATIONAL_ID:  a8b9c0d1-2e3f-4a5b-6c7d-8e9f0a1b2c3d
"""

import os
import sys
import shutil
import tempfile
import pytest
from pathlib import Path

# Wire up sys.path
PROJECT_ROOT = Path(__file__).parent.parent.resolve()
for sub in ("lib", "src"):
    p = os.path.join(PROJECT_ROOT, sub)
    if p not in sys.path:
        sys.path.insert(0, p)

import diagram_mfdb as db


@pytest.fixture(autouse=True)
def isolate_data_dir(tmp_path, monkeypatch):
    """Isolate data directory so tests do not touch production/data files."""
    tmp_diagrams = tmp_path / "diagrams"
    tmp_diagrams.mkdir(parents=True, exist_ok=True)
    tmp_recent = tmp_path / "recent_diagrams.bejson"

    monkeypatch.setattr(db, "DIAGRAMS_ROOT", str(tmp_diagrams))
    monkeypatch.setattr(db, "RECENT_REGISTRY_PATH", str(tmp_recent))
    monkeypatch.setattr(db, "_current_diagram_id", None)

    # Initialize a clean test diagram
    db.new_diagram("Test Diagram")
    yield
    db._current_diagram_id = None


def test_diagram_lifecycle():
    assert db.get_diagram_name() == "Test Diagram"
    db.set_diagram_name("Updated Diagram")
    assert db.get_diagram_name() == "Updated Diagram"

    recent = db.list_recent_diagrams()
    assert len(recent) == 1
    assert recent[0]["name"] == "Updated Diagram"


def test_shape_crud():
    shape = db.create_shape(
        cx=100.0, cy=200.0, size_class="medium", kind="rect",
        label="Start Node", color="#334", font_color="auto", text="Hello World"
    )
    assert shape is not None
    shape_id = shape["shape_id"]
    assert shape["shape_label"] == "Start Node"
    assert shape["shape_cx"] == 100.0

    # Read
    fetched = db.get_shape(shape_id)
    assert fetched["shape_label"] == "Start Node"

    # Update Board & Own fields
    db.update_shape_board_fields(shape_id, {"shape_cx": 150.0})
    db.update_shape_own_fields(shape_id, {"shape_label": "Renamed Node"})
    updated = db.get_shape(shape_id)
    assert updated["shape_cx"] == 150.0
    assert updated["shape_label"] == "Renamed Node"

    # Custom field
    db.add_custom_field(shape_id, "priority", "integer", 5)
    with_custom = db.get_shape(shape_id)
    assert with_custom["priority"] == 5

    # Delete
    db.delete_shape(shape_id)
    assert db.get_shape(shape_id) is None


def test_shape_cascade_delete():
    parent = db.create_shape(100, 100, "medium", "rect", "Parent", "#334", "auto", "")
    child = db.create_shape(100, 250, "small", "rect", "Child", "#334", "auto", "", parent_fk=parent["shape_id"])
    conn = db.create_connector(parent["shape_id"], child["shape_id"], "forward")

    # Verify initial structure
    assert len(db.list_shapes()) == 2
    assert len(db.list_connectors()) == 1

    # Delete parent: child should be reparented to parent's parent (None), conn removed
    db.delete_shape(parent["shape_id"])

    remaining_shapes = db.list_shapes()
    assert len(remaining_shapes) == 1
    assert remaining_shapes[0]["shape_id"] == child["shape_id"]
    assert remaining_shapes[0]["shape_parent_fk"] == "" or remaining_shapes[0]["shape_parent_fk"] is None
    assert len(db.list_connectors()) == 0


def test_connector_crud():
    s1 = db.create_shape(100, 100, "medium", "rect", "S1", "#334", "auto", "")
    s2 = db.create_shape(300, 100, "medium", "rect", "S2", "#334", "auto", "")

    conn = db.create_connector(s1["shape_id"], s2["shape_id"], "forward")
    conn_id = conn["conn_id"]
    assert conn["conn_flow"] == "forward"

    connectors = db.list_connectors()
    assert len(connectors) == 1

    db.update_connector(conn_id, {"conn_flow": "bidirectional"})
    updated_conn = next(c for c in db.list_connectors() if c["conn_id"] == conn_id)
    assert updated_conn["conn_flow"] == "bidirectional"

    db.delete_connector(conn_id)
    assert len(db.list_connectors()) == 0


def test_territory_crud():
    terr = db.create_territory("rect", 200, 200, 240, "Zone A", "#2a6496")
    terr_id = terr["terr_id"]
    assert terr["terr_label"] == "Zone A"

    territories = db.list_territories()
    assert len(territories) == 1

    db.update_territory(terr_id, {"terr_label": "Zone B", "terr_size": 300})
    updated_terr = next(t for t in db.list_territories() if t["terr_id"] == terr_id)
    assert updated_terr["terr_label"] == "Zone B"
    assert updated_terr["terr_size"] == 300

    db.delete_territory(terr_id)
    assert len(db.list_territories()) == 0


def test_export_import_mfdb132(tmp_path):
    s1 = db.create_shape(100, 100, "medium", "rect", "Alpha", "#334", "auto", "First")
    s2 = db.create_shape(300, 100, "medium", "rect", "Beta", "#334", "auto", "Second", parent_fk=s1["shape_id"])
    db.create_connector(s1["shape_id"], s2["shape_id"], "forward")
    db.create_territory("circle", 200, 100, 300, "Territory 1", "#2e7d32")

    export_file = str(tmp_path / "diagram_export.mfdb132.bejson")
    out_path = db.export_diagram_mfdb132(export_file)
    assert os.path.exists(out_path)

    # Import exported diagram
    imported_info = db.import_diagram_mfdb132(out_path, name_hint="Imported Test")
    assert imported_info["name"] == "Test Diagram"  # Preserved from export
    assert len(imported_info["diagram"]["shapes"]) == 2
    assert len(imported_info["diagram"]["connectors"]) == 1
    assert len(imported_info["diagram"]["territories"]) == 1