"""
Library:        lib_bejson_Diagrams_bejson_errors.py
Family:         Diagrams
Description:    Error registry for the Diagrams family. New family created
                 2026-08-12 to give the diagram domain (boards/shapes/
                 connectors/territories, MFDB-132-backed) its own dedicated
                 library surface instead of living inside HTML. Add new
                 E_DIAGRAMS_* constants here as they're introduced; do not
                 define family error codes anywhere else.
Version:        1.0.0
Date:           2026-08-12
Author:         Elton Boehnen
Contact:        eltonboehnen@gmail.com | boehnenelton2024.pages.dev | github.com/boehnenelton
Format_Creator: Elton Boehnen
RELATIONAL_ID:  8c1e4a6d-2f9b-4c3e-9a5d-7b1f0e3c6a42
"""

# ---------------------------------------------------------------------------
# Diagrams (420-449)
# ---------------------------------------------------------------------------
E_DIAGRAMS_NOT_MFDB132_PACKAGE   = 420  # Doc failed mfdb_validator_is_mfdb132_package()
E_DIAGRAMS_MFDB132_INVALID       = 421  # Doc failed mfdb_validator_validate_mfdb132_package()
E_DIAGRAMS_MFDB_DATABASE_INVALID = 422  # Unpacked manifest failed mfdb_validator_validate_database()
E_DIAGRAMS_MISSING_ENTITY        = 423  # Required Board/Connector/Territory entity absent from manifest
E_DIAGRAMS_MISSING_FIELD         = 424  # Required field absent from an entity's Fields[]
E_DIAGRAMS_FK_UNRESOLVED         = 425  # board_membership_fk / conn_from_fk / conn_to_fk / shape_parent_fk points nowhere
E_DIAGRAMS_INVALID_SIZE_CLASS    = 426  # shape_size_class not in the known SIZE_CLASS set
E_DIAGRAMS_INVALID_ROOT          = 427  # mfdb_root_dir has no 104a.mfdb.bejson at its root
E_DIAGRAMS_WRITE_FAILED          = 428  # Atomic write of the .mfdb132.bejson package failed
E_DIAGRAMS_IMPORT_FAILED         = 429  # Unchunk of an .mfdb132.bejson package failed
E_DIAGRAMS_NOT_A_DIAGRAM_PACKAGE = 430  # Package is valid MFDB-132 but not tagged Diagram_Schema

# 431-449 reserved for future Diagrams codes.
