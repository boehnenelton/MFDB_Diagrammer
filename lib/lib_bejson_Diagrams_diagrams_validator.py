"""
Library:        lib_bejson_Diagrams_diagrams_validator.py
Family:         Diagrams
Description:    Diagram validator, built from scratch on top of the Core
                 family's MFDB-132 and database validation — it does not
                 duplicate that logic, it gates on it. A document is only
                 considered a valid diagram if it clears BOTH stages, in
                 order:

                   Stage 1 (sourced from Core, unmodified):
                     lib_bejson_Core_mfdb_validator.mfdb_validator_is_mfdb132_package()
                     lib_bejson_Core_mfdb_validator.mfdb_validator_validate_mfdb132_package()
                     lib_bejson_Core_mfdb_validator.mfdb_validator_validate_database()
                       (run against the package after unchunking to a scratch
                       workspace, or directly against a live MFDB root)

                   Stage 2 (Diagrams-family rules, stacked on top):
                     - Diagram_Schema header present (this family's own tag)
                     - Board / Connector / Territory entities all present
                       with their required base fields
                     - shape_parent_fk / conn_from_fk / conn_to_fk all
                       resolve to a real Board.shape_id
                     - shape_size_class values fall inside the known set
                       (warning only — the renderer degrades gracefully)

                 A document that fails Stage 1 is never even handed to
                 Stage 2 — "must pass 132 validation first, then the
                 diagram validation" per the family's design brief.
Version:        1.0.0
Date:           2026-08-12
Author:         Elton Boehnen
Contact:        eltonboehnen@gmail.com | boehnenelton2024.pages.dev | github.com/boehnenelton
Format_Creator: Elton Boehnen
RELATIONAL_ID:  4f8b1e6a-9d3c-4a02-8e5f-1b6d3c9a72f0
"""

import json
import shutil
import tempfile
from dataclasses import dataclass, field as dc_field
from pathlib import Path
from typing import Any, Dict, List, Optional

from lib_bejson_Core_bejson_core import bejson_core_get_field_map
from lib_bejson_Core_mfdb_validator import (
    mfdb_validator_is_mfdb132_package,
    mfdb_validator_validate_mfdb132_package,
    validate_mfdb_database,
)
from lib_bejson_Core_bejson_chunking import bejson_core_chunking_unchunk_chunked_104

from lib_bejson_Diagrams_diagrams_core import (
    DIAGRAM_SCHEMA_TAG,
    KNOWN_SIZE_CLASSES,
    BOARD_BASE_FIELDS,
    CONNECTOR_BASE_FIELDS,
    TERRITORY_BASE_FIELDS,
)
from lib_bejson_Diagrams_bejson_errors import (
    E_DIAGRAMS_NOT_MFDB132_PACKAGE,
    E_DIAGRAMS_MFDB132_INVALID,
    E_DIAGRAMS_MFDB_DATABASE_INVALID,
    E_DIAGRAMS_MISSING_ENTITY,
    E_DIAGRAMS_MISSING_FIELD,
    E_DIAGRAMS_FK_UNRESOLVED,
    E_DIAGRAMS_NOT_A_DIAGRAM_PACKAGE,
)


class DiagramValidationError(Exception):
    def __init__(self, message: str, code: int, context: Optional[dict] = None):
        super().__init__(message)
        self.code = code
        self.context = context or {}


@dataclass
class DiagramValidationResult:
    valid: bool = True
    errors: List[str] = dc_field(default_factory=list)
    warnings: List[str] = dc_field(default_factory=list)

    def add_error(self, message: str, location: str = ""):
        self.valid = False
        self.errors.append(f"ERROR | Location: {location} | Message: {message}" if location else f"ERROR | Message: {message}")

    def add_warning(self, message: str, location: str = ""):
        self.warnings.append(f"WARNING | Location: {location} | Message: {message}" if location else f"WARNING | Message: {message}")


def _docs_by_entity(manifest_path: str) -> Dict[str, Dict[str, Any]]:
    """Loads manifest + every entity file it lists, keyed by entity_name."""
    manifest_doc = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    fm = bejson_core_get_field_map(manifest_doc)
    manifest_dir = Path(manifest_path).parent
    out: Dict[str, Dict[str, Any]] = {}
    for row in manifest_doc.get("Values", []):
        entity_name = row[fm["entity_name"]]
        file_path = row[fm["file_path"]]
        entity_path = (manifest_dir / file_path).resolve()
        if entity_path.is_file():
            out[entity_name] = json.loads(entity_path.read_text(encoding="utf-8"))
    return out


def _rows_as_dicts(doc: Dict[str, Any]) -> List[Dict[str, Any]]:
    fm = bejson_core_get_field_map(doc)
    names_by_index = {i: n for n, i in fm.items()}
    return [
        {names_by_index[i]: v for i, v in enumerate(row) if i in names_by_index}
        for row in doc.get("Values", [])
    ]


def _check_required_fields(doc: Dict[str, Any], required: List[Dict[str, str]],
                            entity_label: str, result: DiagramValidationResult) -> None:
    have = set(bejson_core_get_field_map(doc).keys())
    for f in required:
        if f["name"] not in have:
            result.add_error(f"Entity '{entity_label}' missing required field '{f['name']}'", entity_label)


def _validate_diagram_structure(manifest_path: str, result: DiagramValidationResult) -> None:
    """Stage 2: diagram-specific structural rules against an unpacked MFDB root."""
    entities = _docs_by_entity(manifest_path)

    for entity_name, required_fields in (
        ("Board", BOARD_BASE_FIELDS),
        ("Connector", CONNECTOR_BASE_FIELDS),
        ("Territory", TERRITORY_BASE_FIELDS),
    ):
        if entity_name not in entities:
            result.add_error(f"Diagram is missing required entity '{entity_name}'", "Structure")
            continue
        _check_required_fields(entities[entity_name], required_fields, entity_name, result)

    if not result.valid:
        return  # Entity/field structure is broken — FK/value checks would just cascade-fail.

    board_rows = _rows_as_dicts(entities["Board"])
    shape_ids = {r.get("shape_id") for r in board_rows if r.get("shape_id")}

    for r in board_rows:
        sc = r.get("shape_size_class")
        if sc and sc not in KNOWN_SIZE_CLASSES:
            result.add_warning(f"shape_size_class '{sc}' not in known set {sorted(KNOWN_SIZE_CLASSES)}", f"Board:{r.get('shape_id')}")
        pfk = r.get("shape_parent_fk")
        if pfk and pfk not in shape_ids:
            result.add_error(f"shape_parent_fk '{pfk}' does not resolve to any Board.shape_id", f"Board:{r.get('shape_id')}")

    for r in _rows_as_dicts(entities["Connector"]):
        for fk_field in ("conn_from_fk", "conn_to_fk"):
            fk = r.get(fk_field)
            if fk and fk not in shape_ids:
                result.add_error(f"{fk_field} '{fk}' does not resolve to any Board.shape_id", f"Connector:{r.get('conn_id')}")


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------

def diagrams_validator_validate_database_dir(manifest_path: str) -> DiagramValidationResult:
    """
    Validates a live, unpacked diagram MFDB root (not a package). Stage 1 =
    validate_mfdb_database(); Stage 2 = diagram structural rules. Use this
    to sanity-check a diagram directory before ever packaging it.
    """
    result = DiagramValidationResult()
    core_res = validate_mfdb_database(manifest_path)
    if not core_res.valid:
        for e in core_res.errors:
            result.add_error(e, "MFDB Database (Stage 1)")
        return result
    for w in core_res.warnings:
        result.add_warning(w, "MFDB Database (Stage 1)")

    _validate_diagram_structure(manifest_path, result)
    return result


def diagrams_validator_validate_package(doc: Dict[str, Any]) -> DiagramValidationResult:
    """
    Validates a single-file MFDB-132 diagram package doc. Must clear
    Stage 1 (MFDB-132 structural validation, then full MFDB database
    validation against the unchunked contents) before Stage 2 (diagram
    structural rules) is even attempted.
    """
    result = DiagramValidationResult()

    if not mfdb_validator_is_mfdb132_package(doc):
        result.add_error("Document is not a recognized MFDB-132 package.", "Stage 1: MFDB-132")
        return result

    pkg_check = mfdb_validator_validate_mfdb132_package(doc)
    if not pkg_check["valid"]:
        for e in pkg_check["errors"]:
            result.add_error(e, "Stage 1: MFDB-132")
        return result

    if doc.get("Diagram_Schema") != DIAGRAM_SCHEMA_TAG:
        result.add_error(
            f"Package is valid MFDB-132 but is not tagged as a diagram (Diagram_Schema != '{DIAGRAM_SCHEMA_TAG}').",
            "Stage 1: Diagram Tag",
        )
        return result

    # Unchunk to a disposable scratch workspace purely to run full database
    # validation (Stage 1's second half) and the Stage 2 structural checks.
    scratch_dir = tempfile.mkdtemp(prefix="diagrams_validate_")
    try:
        count = bejson_core_chunking_unchunk_chunked_104(doc, scratch_dir)
        manifest_path = str(Path(scratch_dir) / "104a.mfdb.bejson")
        if count == 0 or not Path(manifest_path).is_file():
            result.add_error("Package unchunked but manifest is missing.", "Stage 1: MFDB Database")
            return result

        db_res = validate_mfdb_database(manifest_path)
        if not db_res.valid:
            for e in db_res.errors:
                result.add_error(e, "Stage 1: MFDB Database")
            return result
        for w in db_res.warnings:
            result.add_warning(w, "Stage 1: MFDB Database")

        _validate_diagram_structure(manifest_path, result)
    finally:
        shutil.rmtree(scratch_dir, ignore_errors=True)

    return result
