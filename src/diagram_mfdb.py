"""
File:           diagram_mfdb.py
Description:    MFDB-backed persistence service for the BEJSON Diagrammer.
                 Every shape placed on the board gets its own dedicated MFDB
                 entity file with its own field schema (base fields plus any
                 user-defined custom fields). Each shape entity carries a
                 board_membership_fk field that links it back to its single
                 row in the shared Board registry entity, which is what
                 makes it "a shape on the board." Connectors and Territories
                 remain shared multi-row entities.

                 Supports multiple diagrams: each diagram lives in its own
                 MFDB root folder under data/diagrams/<diagram_id>/. A
                 lightweight, persistent registry (data/recent_diagrams.bejson)
                 remembers every diagram ever created/opened (id, name, root
                 path, last_opened) so the client can offer a "load previous
                 diagram" combo box across server restarts.
Version:        1.2.1
Date:           2026-08-12
Author:         Elton Boehnen
Contact:        eltonboehnen@gmail.com | boehnenelton2024.pages.dev | github.com/boehnenelton
RELATIONAL_ID:  a3c8e2f6-1d9b-4c47-8a5e-6f2b9d4c1e83
CHANGELOG:
  v1.2.1 - Bug fix: delete_shape() never cascaded. Deleting a shape with
           children left those children's shape_parent_fk pointing at a
           row that no longer existed in Board (and left any Connector
           using the deleted shape as an endpoint pointing at nothing too).
           The client already reparented children locally on delete
           (deleteSel() in diagrammer.js), so the dangling reference was
           invisible until export/import, where the new Diagrams validator
           correctly refused to write/read it ("shape_parent_fk '...' does
           not resolve to any Board.shape_id"). delete_shape() now
           reparents former children to the deleted shape's own parent
           (matching the client's existing skip-a-generation behavior) and
           removes any Connector row that used the deleted shape as an
           endpoint.
  v1.2.0 - Replaced export_mfdb_zip() (zipped MFDB 1.31) with
           export_diagram_mfdb132() / import_diagram_mfdb132(), so a diagram
           now packages as a single .mfdb132.bejson document instead of a
           .zip directory dump. Both routes through the new Diagrams library
           family (lib_bejson_Diagrams_diagrams_core.py /
           lib_bejson_Diagrams_diagrams_validator.py), which gates every
           write/read on MFDB-132 + full MFDB-database + diagram-structural
           validation. Board/Connector/Territory/Shape field schemas
           (BOARD_FIELDS etc.) are no longer defined locally — sourced from
           the Diagrams family so every diagram app in the ecosystem shares
           one schema. lib/ updated to the current Core library set
           (bejson_core 2.0.5, bejson_errors 2.4.0, bejson_path_guard 1.1.0,
           mfdb_core 2.3.0, mfdb_validator 2.2.0, etc.) and gained
           lib_bejson_Core_bejson_chunking.py (new dependency for MFDB-132
           packaging).
  v1.1.0 - Refactored the single fixed MFDB_ROOT/MANIFEST_PATH constants into
           a dynamic "current diagram" pointer so the app can switch between
           multiple diagrams at runtime. Added new_diagram(), load_diagram(),
           list_recent_diagrams(), backed by a persistent recent-diagrams
           registry file that survives server restarts.
"""

import os
import re
import sys
import uuid
from pathlib import Path
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

SCRIPT_PATH = str(Path(__file__).parent.parent.resolve())
LIB_PATH = os.path.join(SCRIPT_PATH, "lib")
if LIB_PATH not in sys.path:
    sys.path.insert(0, LIB_PATH)

from lib_bejson_Core_bejson_core import (
    bejson_core_atomic_write,
    bejson_core_load_file,
    bejson_core_update_field,
    bejson_core_get_field_index,
)
from lib_bejson_Core_mfdb_core import (
    MFDBCoreError,
    mfdb_core_create_database,
    mfdb_core_create_entity_file,
    mfdb_core_load_entity,
    mfdb_core_get_entity_doc,
    mfdb_core_add_entity_record,
    mfdb_core_remove_entity_record,
    mfdb_core_update_entity_record,
    mfdb_core_load_manifest,
    _get_manifest_entry,
    _get_entity_path,
    _write_entity_doc,
    _write_manifest_doc,
    _load_json,
)
from lib_bejson_Diagrams_diagrams_core import (
    DiagramsError,
    BOARD_BASE_FIELDS as BOARD_FIELDS,
    CONNECTOR_BASE_FIELDS as CONNECTOR_FIELDS,
    TERRITORY_BASE_FIELDS as TERRITORY_FIELDS,
    SHAPE_BASE_FIELDS,
    diagrams_core_write_package_file,
    diagrams_core_import_package_file,
)

DIAGRAMS_ROOT = os.path.join(SCRIPT_PATH, "data", "diagrams")
RECENT_REGISTRY_PATH = os.path.join(SCRIPT_PATH, "data", "recent_diagrams.bejson")

# ── Current-diagram pointer — mutable at runtime, switched by new/load ──
_current_diagram_id: Optional[str] = None


def _manifest_path() -> str:
    if _current_diagram_id is None:
        raise MFDBCoreError("No diagram is currently open", 51)
    return os.path.join(DIAGRAMS_ROOT, _current_diagram_id, "104a.mfdb.bejson")


def _diagram_root(diagram_id: str) -> str:
    return os.path.join(DIAGRAMS_ROOT, diagram_id)


def get_current_diagram_id() -> Optional[str]:
    return _current_diagram_id


# Board/Connector/Territory/Shape base field schemas now live in the
# Diagrams library family (lib_bejson_Diagrams_diagrams_core.py), imported
# above as BOARD_FIELDS / CONNECTOR_FIELDS / TERRITORY_FIELDS /
# SHAPE_BASE_FIELDS — this app no longer defines its own copy, so schema
# changes only need to happen in one place across every diagram app.

RESERVED_CUSTOM_NAMES = {f["name"] for f in SHAPE_BASE_FIELDS} | {"board_membership_fk"}


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


def _slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", name.strip().lower()).strip("_")
    return slug or "diagram"


def _now_iso() -> str:
    # Microsecond precision avoids same-second ties clobbering recency
    # ordering when diagrams are created/opened in quick succession.
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


# ---------------------------------------------------------------------------
# Recent-diagrams registry — persists across server restarts
# ---------------------------------------------------------------------------

def _ensure_registry() -> None:
    if os.path.exists(RECENT_REGISTRY_PATH):
        return
    os.makedirs(os.path.dirname(RECENT_REGISTRY_PATH), exist_ok=True)
    doc = {
        "Format": "BEJSON", "Format_Version": "104", "Format_Creator": "Elton Boehnen",
        "Records_Type": ["RecentDiagram"],
        "Fields": [
            {"name": "diagram_id",   "type": "string"},
            {"name": "name",         "type": "string"},
            {"name": "root_path",    "type": "string"},
            {"name": "last_opened",  "type": "string"},
        ],
        "Values": [],
    }
    bejson_core_atomic_write(RECENT_REGISTRY_PATH, doc)


def _load_registry() -> Dict[str, Any]:
    _ensure_registry()
    return bejson_core_load_file(RECENT_REGISTRY_PATH)


def _save_registry(doc: Dict[str, Any]) -> None:
    bejson_core_atomic_write(RECENT_REGISTRY_PATH, doc)


def _upsert_registry(diagram_id: str, name: str, root_path: str) -> None:
    doc = _load_registry()
    idx = bejson_core_get_field_index(doc, "diagram_id")
    name_idx = bejson_core_get_field_index(doc, "name")
    path_idx = bejson_core_get_field_index(doc, "root_path")
    opened_idx = bejson_core_get_field_index(doc, "last_opened")
    row = next((r for r in doc["Values"] if r[idx] == diagram_id), None)
    now = _now_iso()
    if row:
        row[name_idx] = name
        row[path_idx] = root_path
        row[opened_idx] = now
    else:
        doc["Values"].append([diagram_id, name, root_path, now])
    _save_registry(doc)


def list_recent_diagrams() -> List[Dict[str, Any]]:
    doc = _load_registry()
    rows = [dict(zip([f["name"] for f in doc["Fields"]], row)) for row in doc["Values"]]
    rows.sort(key=lambda r: r.get("last_opened") or "", reverse=True)
    return rows


# ---------------------------------------------------------------------------
# Diagram lifecycle — new / load / ensure-default
# ---------------------------------------------------------------------------

def new_diagram(name: str = "Untitled Diagram") -> Dict[str, Any]:
    """Creates a brand-new diagram in its own MFDB root, switches to it, registers it."""
    global _current_diagram_id
    base_slug = _slugify(name)
    diagram_id = base_slug
    n = 2
    while os.path.exists(_diagram_root(diagram_id)):
        diagram_id = f"{base_slug}_{n}"
        n += 1

    root = _diagram_root(diagram_id)
    mfdb_core_create_database(
        root_dir=root,
        db_name=name,
        entities=[
            {"name": "Board",      "fields": BOARD_FIELDS,      "primary_key": "shape_id"},
            {"name": "Connector",  "fields": CONNECTOR_FIELDS,  "primary_key": "conn_id"},
            {"name": "Territory",  "fields": TERRITORY_FIELDS,  "primary_key": "terr_id"},
        ],
    )
    _current_diagram_id = diagram_id
    _upsert_registry(diagram_id, name, root)
    return {"diagram_id": diagram_id, "name": name}


def load_diagram(diagram_id: str) -> Dict[str, Any]:
    """Switches the current-diagram pointer to an already-registered diagram."""
    global _current_diagram_id
    root = _diagram_root(diagram_id)
    manifest = os.path.join(root, "104a.mfdb.bejson")
    if not os.path.exists(manifest):
        raise MFDBCoreError(f"Diagram '{diagram_id}' not found on disk", 51)
    _current_diagram_id = diagram_id
    name = get_diagram_name()
    _upsert_registry(diagram_id, name, root)
    return get_full_diagram()


def ensure_default_diagram() -> None:
    """On first boot: reopen the most recently used diagram, or create a fresh one."""
    global _current_diagram_id
    recents = list_recent_diagrams()
    for entry in recents:
        did = entry["diagram_id"]
        if os.path.exists(os.path.join(_diagram_root(did), "104a.mfdb.bejson")):
            _current_diagram_id = did
            return
    new_diagram("Untitled Diagram")


def get_diagram_name() -> str:
    doc = _load_json(_manifest_path())
    return doc.get("DB_Name", "Untitled Diagram")


def set_diagram_name(name: str) -> None:
    doc = _load_json(_manifest_path())
    doc["DB_Name"] = name
    _write_manifest_doc(doc, _manifest_path())
    if _current_diagram_id:
        _upsert_registry(_current_diagram_id, name, _diagram_root(_current_diagram_id))


# ---------------------------------------------------------------------------
# Shapes — each shape is its own MFDB entity file (own fields, own schema)
# ---------------------------------------------------------------------------

def create_shape(
    cx: float, cy: float, size_class: str, kind: str,
    label: str, color: str, font_color: str, text: str,
    parent_fk: Optional[str] = None, collapsed: bool = False,
    custom_fields: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """
    Registers a Board row for this shape, then creates a brand-new entity
    file dedicated to this shape alone, carrying its own schema (base fields
    plus any caller-supplied custom fields) and a board_membership_fk back
    to the Board row.
    """
    mp = _manifest_path()
    shape_id = _new_id("shape")
    entity_name = f"Shape_{shape_id}"

    mfdb_core_add_entity_record(
        mp, "Board",
        [shape_id, entity_name, cx, cy, size_class, kind, parent_fk, bool(collapsed)],
    )

    fields = list(SHAPE_BASE_FIELDS)
    values = [shape_id, label, color, font_color, text]
    for cf in (custom_fields or []):
        cf_name = cf["name"]
        if cf_name in RESERVED_CUSTOM_NAMES:
            raise ValueError(f"Custom field name '{cf_name}' collides with a reserved base field")
        fields.append({"name": cf_name, "type": cf.get("type", "string")})
        values.append(cf.get("value"))

    mfdb_core_create_entity_file(
        mp, entity_name, fields,
        description=f"Private entity for board shape {shape_id}",
        primary_key="board_membership_fk",
        file_path_rel=f"data/{entity_name.lower()}.bejson",
    )
    mfdb_core_add_entity_record(mp, entity_name, values)

    return get_shape(shape_id)


def get_shape(shape_id: str) -> Optional[Dict[str, Any]]:
    mp = _manifest_path()
    board_rows = mfdb_core_load_entity(mp, "Board")
    board_row = next((r for r in board_rows if r["shape_id"] == shape_id), None)
    if not board_row:
        return None
    shape_rows = mfdb_core_load_entity(mp, board_row["shape_entity_name"])
    own = shape_rows[0] if shape_rows else {}
    merged = dict(board_row)
    merged.update(own)
    merged["shape_id"] = shape_id  # own's board_membership_fk shares this value; keep canonical id
    return merged


def list_shapes() -> List[Dict[str, Any]]:
    mp = _manifest_path()
    board_rows = mfdb_core_load_entity(mp, "Board")
    out = []
    for row in board_rows:
        shape_rows = mfdb_core_load_entity(mp, row["shape_entity_name"])
        own = shape_rows[0] if shape_rows else {}
        merged = dict(row)
        merged.update(own)
        merged["shape_id"] = row["shape_id"]
        out.append(merged)
    return out


def update_shape_board_fields(shape_id: str, updates: Dict[str, Any]) -> None:
    """Update Board-owned fields: shape_cx, shape_cy, shape_size_class, shape_kind, shape_parent_fk, shape_collapsed."""
    mp = _manifest_path()
    board_doc = mfdb_core_get_entity_doc(mp, "Board")
    idx = next((i for i, row in enumerate(board_doc["Values"])
                if row[bejson_core_get_field_index(board_doc, "shape_id")] == shape_id), None)
    if idx is None:
        raise MFDBCoreError(f"Shape {shape_id} not found in Board", 51)
    for field_name, value in updates.items():
        mfdb_core_update_entity_record(mp, "Board", idx, field_name, value)


def update_shape_own_fields(shape_id: str, updates: Dict[str, Any]) -> None:
    """Update fields that live in the shape's own private entity (label/color/text/custom fields)."""
    mp = _manifest_path()
    entry = _board_entry_for(shape_id)
    entity_name = entry["shape_entity_name"]
    doc = mfdb_core_get_entity_doc(mp, entity_name)
    if not doc["Values"]:
        raise MFDBCoreError(f"Shape entity {entity_name} has no record", 51)
    for field_name, value in updates.items():
        mfdb_core_update_entity_record(mp, entity_name, 0, field_name, value)


def add_custom_field(shape_id: str, field_name: str, field_type: str, value: Any) -> None:
    """
    Schema-migration on a single shape's private entity: add a new column
    (Fields entry) and set its value on the shape's one-and-only row.
    """
    if field_name in RESERVED_CUSTOM_NAMES:
        raise ValueError(f"Custom field name '{field_name}' collides with a reserved base field")
    mp = _manifest_path()
    entry = _board_entry_for(shape_id)
    entity_name = entry["shape_entity_name"]
    entity_path = _get_entity_path(mp, entity_name)
    doc = bejson_core_load_file(entity_path)
    if any(f["name"] == field_name for f in doc["Fields"]):
        raise ValueError(f"Field '{field_name}' already exists on {entity_name}")
    doc["Fields"].append({"name": field_name, "type": field_type})
    for row in doc["Values"]:
        row.append(value)
    _write_entity_doc(doc, entity_path)


def delete_shape(shape_id: str) -> None:
    """
    Remove the Board row and delete + deregister the shape's private entity
    file. Cascades to keep the graph valid: any other shape whose
    shape_parent_fk pointed at this shape is reparented to this shape's own
    parent (skip a generation — matches what deleteSel() already does
    client-side), and any Connector row using this shape as an endpoint is
    deleted. Without this, a deleted shape left dangling shape_parent_fk /
    conn_from_fk / conn_to_fk references server-side (even though the
    client hid them locally) — invisible until export/import, where the
    Diagrams validator now correctly rejects them: a foreign key is
    expected to resolve to a still-existing Board.shape_id.
    """
    mp = _manifest_path()
    entry = _board_entry_for(shape_id)
    entity_name = entry["shape_entity_name"]

    board_doc = mfdb_core_get_entity_doc(mp, "Board")
    board_fm = bejson_core_get_field_index(board_doc, "shape_id")
    idx = next((i for i, row in enumerate(board_doc["Values"])
                if row[board_fm] == shape_id), None)
    if idx is not None:
        mfdb_core_remove_entity_record(mp, "Board", idx)

    # Cascade: reparent any children of the deleted shape to the deleted
    # shape's own parent (skip a generation, same as the client already
    # does locally in deleteSel()) rather than promoting them all the way
    # to root — keeps server state consistent with what the UI shows.
    grandparent_fk = entry.get("shape_parent_fk") or ""
    board_doc = mfdb_core_get_entity_doc(mp, "Board")
    id_idx = bejson_core_get_field_index(board_doc, "shape_id")
    parent_idx = bejson_core_get_field_index(board_doc, "shape_parent_fk")
    for i, row in enumerate(board_doc["Values"]):
        if row[parent_idx] == shape_id:
            mfdb_core_update_entity_record(mp, "Board", i, "shape_parent_fk", grandparent_fk)

    # Cascade: drop any connector that used this shape as an endpoint.
    conn_doc = mfdb_core_get_entity_doc(mp, "Connector")
    from_idx = bejson_core_get_field_index(conn_doc, "conn_from_fk")
    to_idx = bejson_core_get_field_index(conn_doc, "conn_to_fk")
    dead_conn_indices = [i for i, row in enumerate(conn_doc["Values"])
                          if row[from_idx] == shape_id or row[to_idx] == shape_id]
    for i in sorted(dead_conn_indices, reverse=True):
        mfdb_core_remove_entity_record(mp, "Connector", i)

    try:
        entity_path = _get_entity_path(mp, entity_name)
        if os.path.exists(entity_path):
            os.remove(entity_path)
    except MFDBCoreError:
        pass

    manifest_doc = _load_json(mp)
    en_idx = bejson_core_get_field_index(manifest_doc, "entity_name")
    manifest_doc["Values"] = [row for row in manifest_doc["Values"] if row[en_idx] != entity_name]
    _write_manifest_doc(manifest_doc, mp)


def _board_entry_for(shape_id: str) -> Dict[str, Any]:
    mp = _manifest_path()
    board_rows = mfdb_core_load_entity(mp, "Board")
    row = next((r for r in board_rows if r["shape_id"] == shape_id), None)
    if not row:
        raise MFDBCoreError(f"Shape {shape_id} not found", 51)
    return row


# ---------------------------------------------------------------------------
# Connectors — shared entity, one row per connector
# ---------------------------------------------------------------------------

def create_connector(from_fk: str, to_fk: str, flow: str) -> Dict[str, Any]:
    mp = _manifest_path()
    conn_id = _new_id("conn")
    mfdb_core_add_entity_record(mp, "Connector", [conn_id, from_fk, to_fk, flow])
    return {"conn_id": conn_id, "conn_from_fk": from_fk, "conn_to_fk": to_fk, "conn_flow": flow}


def list_connectors() -> List[Dict[str, Any]]:
    return mfdb_core_load_entity(_manifest_path(), "Connector")


def update_connector(conn_id: str, updates: Dict[str, Any]) -> None:
    mp = _manifest_path()
    doc = mfdb_core_get_entity_doc(mp, "Connector")
    idx = next((i for i, row in enumerate(doc["Values"])
                if row[bejson_core_get_field_index(doc, "conn_id")] == conn_id), None)
    if idx is None:
        raise MFDBCoreError(f"Connector {conn_id} not found", 51)
    for field_name, value in updates.items():
        mfdb_core_update_entity_record(mp, "Connector", idx, field_name, value)


def delete_connector(conn_id: str) -> None:
    mp = _manifest_path()
    doc = mfdb_core_get_entity_doc(mp, "Connector")
    idx = next((i for i, row in enumerate(doc["Values"])
                if row[bejson_core_get_field_index(doc, "conn_id")] == conn_id), None)
    if idx is not None:
        mfdb_core_remove_entity_record(mp, "Connector", idx)


def delete_connectors_touching(shape_id: str) -> None:
    mp = _manifest_path()
    doc = mfdb_core_get_entity_doc(mp, "Connector")
    from_idx = bejson_core_get_field_index(doc, "conn_from_fk")
    to_idx = bejson_core_get_field_index(doc, "conn_to_fk")
    touching = [i for i, row in enumerate(doc["Values"]) if row[from_idx] == shape_id or row[to_idx] == shape_id]
    for i in sorted(touching, reverse=True):
        mfdb_core_remove_entity_record(mp, "Connector", i)


# ---------------------------------------------------------------------------
# Territories — shared entity, one row per territory
# ---------------------------------------------------------------------------

def create_territory(kind: str, cx: float, cy: float, size: float, label: str, color: str) -> Dict[str, Any]:
    mp = _manifest_path()
    terr_id = _new_id("terr")
    mfdb_core_add_entity_record(mp, "Territory", [terr_id, kind, cx, cy, size, label, color])
    return {"terr_id": terr_id, "terr_kind": kind, "terr_cx": cx, "terr_cy": cy,
            "terr_size": size, "terr_label": label, "terr_color": color}


def list_territories() -> List[Dict[str, Any]]:
    return mfdb_core_load_entity(_manifest_path(), "Territory")


def update_territory(terr_id: str, updates: Dict[str, Any]) -> None:
    mp = _manifest_path()
    doc = mfdb_core_get_entity_doc(mp, "Territory")
    idx = next((i for i, row in enumerate(doc["Values"])
                if row[bejson_core_get_field_index(doc, "terr_id")] == terr_id), None)
    if idx is None:
        raise MFDBCoreError(f"Territory {terr_id} not found", 51)
    for field_name, value in updates.items():
        mfdb_core_update_entity_record(mp, "Territory", idx, field_name, value)


def delete_territory(terr_id: str) -> None:
    mp = _manifest_path()
    doc = mfdb_core_get_entity_doc(mp, "Territory")
    idx = next((i for i, row in enumerate(doc["Values"])
                if row[bejson_core_get_field_index(doc, "terr_id")] == terr_id), None)
    if idx is not None:
        mfdb_core_remove_entity_record(mp, "Territory", idx)


# ---------------------------------------------------------------------------
# Whole-diagram assembly / schema introspection
# ---------------------------------------------------------------------------

def get_full_diagram() -> Dict[str, Any]:
    return {
        "diagramId": _current_diagram_id,
        "name": get_diagram_name(),
        "shapes": list_shapes(),
        "connectors": list_connectors(),
        "territories": list_territories(),
    }


def get_full_schema() -> Dict[str, Any]:
    """Returns the manifest plus every entity's raw BEJSON doc, for the Live Schema viewer."""
    mp = _manifest_path()
    manifest_doc = _load_json(mp)
    entities = {}
    for entry in mfdb_core_load_manifest(mp):
        entity_name = entry["entity_name"]
        try:
            entities[entity_name] = mfdb_core_get_entity_doc(mp, entity_name)
        except MFDBCoreError:
            continue
    return {"manifest": manifest_doc, "entities": entities}


def export_diagram_mfdb132(dest_path: str) -> str:
    """
    Packages the current diagram's entire MFDB root (manifest + every entity
    file) into a single portable .mfdb132.bejson document for download —
    replaces the old zipped-1.31 export. Validated (MFDB-132 structure ->
    MFDB database -> diagram rules, via the Diagrams family validator)
    before the file is written; raises DiagramsError on failure.
    """
    name = get_diagram_name()
    return diagrams_core_write_package_file(_diagram_root(_current_diagram_id), name, dest_path)


def import_diagram_mfdb132(src_path: str, name_hint: Optional[str] = None) -> Dict[str, Any]:
    """
    Imports a single-file .mfdb132.bejson diagram package, unpacking it into
    a brand-new diagram root (never overwrites an existing diagram), then
    switches to it and registers it in the recent-diagrams registry.
    Validated before unpacking; raises DiagramsError on failure.
    """
    global _current_diagram_id
    base_slug = _slugify(name_hint or "imported_diagram")
    diagram_id = base_slug
    n = 2
    while os.path.exists(_diagram_root(diagram_id)):
        diagram_id = f"{base_slug}_{n}"
        n += 1

    root = _diagram_root(diagram_id)
    diagrams_core_import_package_file(src_path, root)

    _current_diagram_id = diagram_id
    name = get_diagram_name()
    _upsert_registry(diagram_id, name, root)
    return {"diagram_id": diagram_id, "name": name, "diagram": get_full_diagram()}
