"""
File:           routes.py
Description:    Flask blueprint exposing the BEJSON Diagrammer's MFDB-backed
                 API: shape/connector/territory CRUD, full-diagram assembly,
                 live schema introspection, and single-file MFDB-132
                 diagram export/import.
Version:        1.1.0
Date:           2026-08-12
Author:         Elton Boehnen
Contact:        eltonboehnen@gmail.com | boehnenelton2024.pages.dev | github.com/boehnenelton
RELATIONAL_ID:  7b3e9c1a-5d8f-4b06-a92c-1e6f4a8d3b57
CHANGELOG:
  v1.1.0 - /api/export now returns a single .mfdb132.bejson document instead
           of a .zip (MFDB 1.31 -> 1.32). Added POST /api/import accepting
           an uploaded .mfdb132.bejson file, which validates it and opens it
           as a new diagram. db.MFDBCoreError catches widened to also catch
           db.DiagramsError, the error type the Diagrams family raises on
           validation failure.
"""

import os
import tempfile
from flask import Blueprint, jsonify, request, send_file, render_template

import diagram_mfdb as db

# db.DiagramsError is what the Diagrams-family write/import path raises on
# validation failure — handled alongside db.MFDBCoreError everywhere both
# could plausibly surface.
_DB_ERRORS = (db.MFDBCoreError, db.DiagramsError)

bp = Blueprint("diagrammer", __name__)


@bp.route("/")
def index():
    return render_template("index.html")


@bp.route("/api/diagram", methods=["GET"])
def api_get_diagram():
    return jsonify(db.get_full_diagram())


@bp.route("/api/diagrams", methods=["GET"])
def api_list_diagrams():
    return jsonify({
        "current": db.get_current_diagram_id(),
        "recent": db.list_recent_diagrams(),
    })


@bp.route("/api/diagrams/new", methods=["POST"])
def api_new_diagram():
    d = request.get_json(force=True)
    info = db.new_diagram(d.get("name", "Untitled Diagram"))
    return jsonify({**info, "diagram": db.get_full_diagram()}), 201


@bp.route("/api/diagrams/load", methods=["POST"])
def api_load_diagram():
    d = request.get_json(force=True)
    try:
        diagram = db.load_diagram(d["diagramId"])
    except db.MFDBCoreError as e:
        return jsonify({"error": str(e)}), 404
    return jsonify(diagram)


@bp.route("/api/diagram/name", methods=["PUT"])
def api_set_diagram_name():
    data = request.get_json(force=True)
    db.set_diagram_name(data.get("name", "Untitled Diagram"))
    return jsonify({"ok": True})


@bp.route("/api/schema", methods=["GET"])
def api_get_schema():
    return jsonify(db.get_full_schema())


@bp.route("/api/export", methods=["GET"])
def api_export():
    tmp_dir = tempfile.mkdtemp()
    dest_path = os.path.join(tmp_dir, "diagram.mfdb132.bejson")
    try:
        pkg_path = db.export_diagram_mfdb132(dest_path)
    except _DB_ERRORS as e:
        return jsonify({"error": str(e)}), 400
    name = db.get_diagram_name() or "diagram"
    safe_name = "".join(c if c.isalnum() or c in ("-", "_") else "_" for c in name)
    return send_file(pkg_path, as_attachment=True,
                      download_name=f"{safe_name}.mfdb132.bejson",
                      mimetype="application/json")


@bp.route("/api/import", methods=["POST"])
def api_import():
    upload = request.files.get("file")
    if upload is None:
        return jsonify({"error": "No file uploaded (expected multipart field 'file')"}), 400

    tmp_dir = tempfile.mkdtemp()
    src_path = os.path.join(tmp_dir, "upload.mfdb132.bejson")
    upload.save(src_path)

    name_hint = os.path.splitext(upload.filename or "imported_diagram")[0]
    try:
        info = db.import_diagram_mfdb132(src_path, name_hint=name_hint)
    except _DB_ERRORS as e:
        return jsonify({"error": str(e)}), 400
    return jsonify(info), 201


# ── SHAPES ───────────────────────────────────────────────────────────────

@bp.route("/api/shape", methods=["POST"])
def api_create_shape():
    d = request.get_json(force=True)
    try:
        shape = db.create_shape(
            cx=d["cx"], cy=d["cy"], size_class=d.get("sizeClass", "medium"),
            kind=d.get("kind", "rect"), label=d.get("label", ""),
            color=d.get("color", "#334"), font_color=d.get("fontColor", "auto"),
            text=d.get("text", ""), parent_fk=d.get("parentFk"),
            collapsed=d.get("collapsed", False), custom_fields=d.get("customFields", []),
        )
    except (ValueError, db.MFDBCoreError) as e:
        return jsonify({"error": str(e)}), 400
    return jsonify(shape), 201


@bp.route("/api/shape/<shape_id>", methods=["PUT"])
def api_update_shape(shape_id):
    d = request.get_json(force=True)
    board_updates = {}
    own_updates = {}
    board_key_map = {
        "cx": "shape_cx", "cy": "shape_cy", "sizeClass": "shape_size_class",
        "kind": "shape_kind", "parentFk": "shape_parent_fk", "collapsed": "shape_collapsed",
    }
    own_key_map = {
        "label": "shape_label", "color": "shape_color",
        "fontColor": "shape_font_color", "text": "shape_text",
    }
    for k, v in d.items():
        if k in board_key_map:
            board_updates[board_key_map[k]] = v
        elif k in own_key_map:
            own_updates[own_key_map[k]] = v
        elif k not in ("customFields",):
            own_updates[k] = v  # allow direct custom-field-name updates

    try:
        if board_updates:
            db.update_shape_board_fields(shape_id, board_updates)
        if own_updates:
            db.update_shape_own_fields(shape_id, own_updates)
        for cf in d.get("customFields", []):
            if "type" in cf:
                db.add_custom_field(shape_id, cf["name"], cf["type"], cf.get("value"))
            else:
                db.update_shape_own_fields(shape_id, {cf["name"]: cf.get("value")})
    except (ValueError, db.MFDBCoreError) as e:
        return jsonify({"error": str(e)}), 400

    return jsonify(db.get_shape(shape_id))


@bp.route("/api/shape/<shape_id>/field", methods=["POST"])
def api_add_custom_field(shape_id):
    d = request.get_json(force=True)
    try:
        db.add_custom_field(shape_id, d["name"], d.get("type", "string"), d.get("value"))
    except (ValueError, db.MFDBCoreError) as e:
        return jsonify({"error": str(e)}), 400
    return jsonify(db.get_shape(shape_id))


@bp.route("/api/shape/<shape_id>", methods=["DELETE"])
def api_delete_shape(shape_id):
    try:
        db.delete_connectors_touching(shape_id)
        db.delete_shape(shape_id)
    except db.MFDBCoreError as e:
        return jsonify({"error": str(e)}), 404
    return jsonify({"ok": True})


# ── CONNECTORS ───────────────────────────────────────────────────────────

@bp.route("/api/connector", methods=["POST"])
def api_create_connector():
    d = request.get_json(force=True)
    conn = db.create_connector(d["fromFk"], d["toFk"], d.get("flow", "none"))
    return jsonify(conn), 201


@bp.route("/api/connector/<conn_id>", methods=["PUT"])
def api_update_connector(conn_id):
    d = request.get_json(force=True)
    updates = {}
    if "flow" in d:
        updates["conn_flow"] = d["flow"]
    try:
        db.update_connector(conn_id, updates)
    except db.MFDBCoreError as e:
        return jsonify({"error": str(e)}), 404
    return jsonify({"ok": True})


@bp.route("/api/connector/<conn_id>", methods=["DELETE"])
def api_delete_connector(conn_id):
    db.delete_connector(conn_id)
    return jsonify({"ok": True})


# ── TERRITORIES ──────────────────────────────────────────────────────────

@bp.route("/api/territory", methods=["POST"])
def api_create_territory():
    d = request.get_json(force=True)
    terr = db.create_territory(
        d.get("kind", "rect"), d["cx"], d["cy"], d.get("size", 240),
        d.get("label", "Territory"), d.get("color", "#2a6496"),
    )
    return jsonify(terr), 201


@bp.route("/api/territory/<terr_id>", methods=["PUT"])
def api_update_territory(terr_id):
    d = request.get_json(force=True)
    key_map = {"cx": "terr_cx", "cy": "terr_cy", "size": "terr_size",
               "label": "terr_label", "color": "terr_color", "kind": "terr_kind"}
    updates = {key_map[k]: v for k, v in d.items() if k in key_map}
    try:
        db.update_territory(terr_id, updates)
    except db.MFDBCoreError as e:
        return jsonify({"error": str(e)}), 404
    return jsonify({"ok": True})


@bp.route("/api/territory/<terr_id>", methods=["DELETE"])
def api_delete_territory(terr_id):
    db.delete_territory(terr_id)
    return jsonify({"ok": True})
