from flask import Blueprint, request, jsonify

from services.ledger_service import create_ledger_entry


ledger_bp = Blueprint("ledger", __name__, url_prefix="/ledger")


@ledger_bp.route("/entry", methods=["POST"])
def create_entry():
    data = request.get_json() or {}

    event_type = data.get("event_type")
    device_id = data.get("device_id")
    event_data = data.get("data", {})

    if not event_type or not device_id:
        return jsonify({
            "code": 400,
            "data": None,
            "error": "event_type and device_id are required",
            "status": "error"
        }), 400

    entry = create_ledger_entry(
        event_type,
        device_id,
        event_data
    )

    return jsonify({
        "code": 201,
        "data": entry,
        "error": None,
        "status": "success"
    }), 201
