"""
Library:        lib_bejson_Diagrams_diagrams_core.py
Family:         Diagrams
Description:    Core write-path library for the Diagrams family. Owns the
                 canonical Board/Connector/Territory MFDB entity schema used
                 by diagram apps (BEJSON Diagrammer and any future caller)
                 and the single-file MFDB-132 packaging path for diagrams:
                 create a diagram's MFDB root, then export/import it as one
                 portable .mfdb132.bejson document instead of a zipped 1.31
                 MFDB directory.

                 Replaces the old ad-hoc "zip the MFDB directory" export
                 pattern (MFDB 1.31 / MFDBArchive) for diagrams specifically.
                 1.31 zip export remains valid for non-diagram MFDB apps —
                 this family standardizes on 1.32 (MFDB132Archive-style
                 chunk/unchunk to a single BEJSON document) because a
                 diagram is meant to end up as ONE portable file.

                 Field Map Cache Mandate: any row lookups this file performs
                 against loaded BEJSON docs go through
                 lib_bejson_Core_bejson_core.bejson_core_get_field_map() /
                 bejson_core_get_field_index() — never raw positional index
                 arithmetic.

                 Not meant to alter Core family libraries — this file only
                 calls their public API (mfdb_core_create_database,
                 bejson_core_chunking_create_mfdb132_package,
                 bejson_core_chunking_unchunk_mfdb132_package,
                 bejson_core_atomic_write).
Version:        1.0.0
Date:           2026-08-12
Author:         Elton Boehnen
Contact:        eltonboehnen@gmail.com | boehnenelton2024.pages.dev | github.com/boehnenelton
Format_Creator: Elton Boehnen
RELATIONAL_ID:  1d5f8a2c-6e3b-4f97-8a01-9c4d2e7b5f18
"""

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from lib_bejson_Core_bejson_core import (
    bejson_core_atomic_write,
    bejson_core_get_field_map,
    bejson_core_get_field_index,
)
from lib_bejson_Core_mfdb_core import (
    MFDBCoreError,
    mfdb_core_create_database,
)
from lib_bejson_Core_bejson_chunking import (
    bejson_core_chunking_create_mfdb132_package,
    bejson_core_chunking_unchunk_mfdb132_package,
    MFDB_MANIFEST_FILENAME,
)

from lib_bejson_Diagrams_bejson_errors import (
    E_DIAGRAMS_INVALID_ROOT,
    E_DIAGRAMS_WRITE_FAILED,
    E_DIAGRAMS_IMPORT_FAILED,
)


class DiagramsError(Exception):
    def __init__(self, message: str, code: int, context: Optional[dict] = None):
        super().__init__(message)
        self.code = code
        self.context = context or {}


# ---------------------------------------------------------------------------
# Canonical diagram entity schema — the standard every diagram app in the
# ecosystem should start from. Callers may append caller-specific fields
# (e.g. a shape's own private entity, which is app-owned and not defined
# here) but Board/Connector/Territory's own base fields should not drift
# between apps without a matching version bump here.
# ---------------------------------------------------------------------------

BOARD_BASE_FIELDS: List[Dict[str, str]] = [
    {"name": "shape_id",          "type": "string"},
    {"name": "shape_entity_name", "type": "string"},
    {"name": "shape_cx",          "type": "number"},
    {"name": "shape_cy",          "type": "number"},
    {"name": "shape_size_class",  "type": "string"},
    {"name": "shape_kind",        "type": "string"},
    {"name": "shape_parent_fk",   "type": "string"},
    {"name": "shape_collapsed",   "type": "boolean"},
]

CONNECTOR_BASE_FIELDS: List[Dict[str, str]] = [
    {"name": "conn_id",       "type": "string"},
    {"name": "conn_from_fk",  "type": "string"},
    {"name": "conn_to_fk",    "type": "string"},
    {"name": "conn_flow",     "type": "string"},
]

TERRITORY_BASE_FIELDS: List[Dict[str, str]] = [
    {"name": "terr_id",    "type": "string"},
    {"name": "terr_kind",  "type": "string"},
    {"name": "terr_cx",    "type": "number"},
    {"name": "terr_cy",    "type": "number"},
    {"name": "terr_size",  "type": "number"},
    {"name": "terr_label", "type": "string"},
    {"name": "terr_color", "type": "string"},
]

# Base fields every shape's own private entity carries in addition to any
# caller-added custom fields. board_membership_fk is the required foreign
# key pointing back to Board.shape_id.
SHAPE_BASE_FIELDS: List[Dict[str, str]] = [
    {"name": "board_membership_fk", "type": "string"},
    {"name": "shape_label",         "type": "string"},
    {"name": "shape_color",         "type": "string"},
    {"name": "shape_font_color",    "type": "string"},
    {"name": "shape_text",          "type": "string"},
]

KNOWN_SIZE_CLASSES = frozenset({"small", "medium", "large", "huge"})
KNOWN_SHAPE_KINDS   = frozenset({"rect", "circle"})

# Tag written onto every MFDB-132 package produced by this family so
# lib_bejson_Diagrams_diagrams_validator.py can recognize it as a diagram
# package (as opposed to some other, non-diagram MFDB-132 package).
DIAGRAM_SCHEMA_TAG = "BEJSON-Diagram-1"


# ---------------------------------------------------------------------------
# Database creation
# ---------------------------------------------------------------------------

def diagrams_core_create_database(root_dir: str, db_name: str,
                                   extra_entities: Optional[List[dict]] = None) -> str:
    """
    Creates a fresh diagram MFDB root with the standard Board/Connector/
    Territory entities. extra_entities (e.g. a shape's own private entity)
    are appended as-is to the entity list handed to mfdb_core_create_database.
    Returns the manifest path.
    """
    entities = [
        {"name": "Board",     "fields": BOARD_BASE_FIELDS,     "primary_key": "shape_id"},
        {"name": "Connector", "fields": CONNECTOR_BASE_FIELDS, "primary_key": "conn_id"},
        {"name": "Territory", "fields": TERRITORY_BASE_FIELDS, "primary_key": "terr_id"},
    ]
    if extra_entities:
        entities.extend(extra_entities)
    return mfdb_core_create_database(root_dir=root_dir, db_name=db_name, entities=entities)


# ---------------------------------------------------------------------------
# Single-file MFDB-132 export / import (replaces zipped 1.31 export)
# ---------------------------------------------------------------------------

def diagrams_core_export_package(mfdb_root_dir: str, db_name: str,
                                  package_version: Optional[str] = None,
                                  prior_package_doc: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """
    Chunks a diagram's MFDB root (manifest + every entity file) into a single
    MFDB-132 Chunked-104a document, then tags it Diagram_Schema so the
    Diagrams validator can recognize it as diagram data specifically (not
    just any MFDB-132 package). Does not validate — call
    lib_bejson_Diagrams_diagrams_validator.diagrams_validator_validate_package()
    on the result, or use diagrams_core_write_package_file() which validates
    before writing.
    """
    root_path = Path(mfdb_root_dir)
    if not (root_path / MFDB_MANIFEST_FILENAME).is_file():
        raise DiagramsError(
            f"No {MFDB_MANIFEST_FILENAME} found at root of {mfdb_root_dir} — "
            "not a valid diagram MFDB root.",
            E_DIAGRAMS_INVALID_ROOT,
        )
    doc = bejson_core_chunking_create_mfdb132_package(
        mfdb_root_dir=mfdb_root_dir,
        db_name=db_name,
        package_version=package_version,
        prior_package_doc=prior_package_doc,
    )
    doc["Diagram_Schema"] = DIAGRAM_SCHEMA_TAG
    return doc


def diagrams_core_write_package_file(mfdb_root_dir: str, db_name: str, dest_path: str,
                                      package_version: Optional[str] = None,
                                      prior_package_doc: Optional[Dict[str, Any]] = None,
                                      validate: bool = True) -> str:
    """
    Builds the single-file MFDB-132 package for a diagram and atomically
    writes it to dest_path (a single .mfdb132.bejson document — the whole
    diagram, one file). Validates (MFDB-132 structure -> unpacked MFDB
    database -> diagram-specific rules) before writing unless validate=False.
    Returns dest_path.
    """
    doc = diagrams_core_export_package(mfdb_root_dir, db_name, package_version, prior_package_doc)

    if validate:
        # Local import: avoids a core<->validator circular import at module
        # load time (validator imports this core file).
        from lib_bejson_Diagrams_diagrams_validator import diagrams_validator_validate_package
        result = diagrams_validator_validate_package(doc)
        if not result.valid:
            raise DiagramsError(
                f"Refusing to write diagram package: {result.errors[0] if result.errors else 'validation failed'}",
                E_DIAGRAMS_WRITE_FAILED,
                {"errors": result.errors, "warnings": result.warnings},
            )

    if not bejson_core_atomic_write(dest_path, doc):
        raise DiagramsError(f"Atomic write failed for {dest_path}", E_DIAGRAMS_WRITE_FAILED)

    return dest_path


def diagrams_core_import_package_file(src_path: str, output_dir: str,
                                       validate: bool = True) -> Tuple[int, Dict[str, Any]]:
    """
    Loads a single-file .mfdb132.bejson diagram package and unchunks it back
    to a standard MFDB root (manifest + data/*.bejson entity files) under
    output_dir. Validates (structure -> database -> diagram rules) before
    unchunking unless validate=False. Returns (files_written, validation_dict).
    """
    p = Path(src_path)
    if not p.is_file():
        raise DiagramsError(f"Package file not found: {src_path}", E_DIAGRAMS_IMPORT_FAILED)

    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        raise DiagramsError(f"Package is not valid JSON: {e}", E_DIAGRAMS_IMPORT_FAILED)

    if validate:
        from lib_bejson_Diagrams_diagrams_validator import diagrams_validator_validate_package
        result = diagrams_validator_validate_package(doc)
        if not result.valid:
            raise DiagramsError(
                f"Refusing to import diagram package: {result.errors[0] if result.errors else 'validation failed'}",
                E_DIAGRAMS_IMPORT_FAILED,
                {"errors": result.errors, "warnings": result.warnings},
            )

    count, package_validation = bejson_core_chunking_unchunk_mfdb132_package(doc, output_dir)
    if not package_validation.get("valid", False):
        raise DiagramsError(
            f"Import unchunked but failed post-restore validation: {package_validation.get('errors')}",
            E_DIAGRAMS_IMPORT_FAILED,
            package_validation,
        )
    return count, package_validation


# ---------------------------------------------------------------------------
# Field-Map-Cache-backed row lookup helper (Core Mandate: field names, not
# positional indices, everywhere outside the low-level Core library itself).
# ---------------------------------------------------------------------------

def diagrams_core_find_row_by_field(doc: Dict[str, Any], field_name: str, value: Any) -> Optional[list]:
    """Returns the first row in doc['Values'] whose field_name column == value, via the Field Map Cache."""
    idx = bejson_core_get_field_index(doc, field_name)
    if idx < 0:
        return None
    for row in doc.get("Values", []):
        if row[idx] == value:
            return row
    return None
