from flask import Blueprint, request
from database import get_connection
import json
from datetime import datetime, timezone


device_profile_bp = Blueprint("device_profile", __name__)


# ============================================================
# CREATE DEVICE PROFILE
# ============================================================

@device_profile_bp.route("/device-profiles", methods=["POST"])
def create_device_profile():

    data = request.get_json()

    if not data:
        return {
            "code": 400,
            "data": None,
            "error": "Request body is required",
            "status": "error"
        }, 400

    required_fields = [
        "device_id",
        "serial_id",
        "brand",
        "original_config"
    ]

    for field in required_fields:
        if field not in data:
            return {
                "code": 400,
                "data": None,
                "error": f"{field} is required",
                "status": "error"
            }, 400

    device_id = data["device_id"]
    serial_id = data["serial_id"]
    brand = data["brand"]
    original_config = data["original_config"]

    conn = get_connection()
    cursor = conn.cursor()

    try:

        # Check whether device exists
        cursor.execute(
            "SELECT device_id FROM devices WHERE device_id = ?",
            (device_id,)
        )

        device = cursor.fetchone()

        if device is None:
            return {
                "code": 404,
                "data": None,
                "error": "Device not found",
                "status": "error"
            }, 404

        # Check whether profile already exists
        cursor.execute(
            "SELECT device_id FROM device_profiles WHERE device_id = ?",
            (device_id,)
        )

        existing_profile = cursor.fetchone()

        if existing_profile is not None:
            return {
                "code": 409,
                "data": None,
                "error": "Device profile already exists",
                "status": "error"
            }, 409

        current_config = original_config
        change_log = []
        stamp_history = []

        cursor.execute(
            """
            INSERT INTO device_profiles
            (
                device_id,
                serial_id,
                brand,
                original_config,
                current_config,
                change_log,
                current_owner_hash,
                status,
                stamp_history
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                device_id,
                serial_id,
                brand,
                json.dumps(original_config),
                json.dumps(current_config),
                json.dumps(change_log),
                None,
                "REGISTERED",
                json.dumps(stamp_history)
            )
        )

        conn.commit()

        return {
            "code": 201,
            "data": {
                "device_id": device_id,
                "serial_id": serial_id,
                "brand": brand,
                "original_config": original_config,
                "current_config": current_config,
                "change_log": change_log,
                "current_owner_hash": None,
                "status": "REGISTERED",
                "stamp_history": stamp_history
            },
            "error": None,
            "status": "success"
        }, 201

    except Exception as e:

        conn.rollback()

        return {
            "code": 500,
            "data": None,
            "error": str(e),
            "status": "error"
        }, 500

    finally:

        conn.close()


# ============================================================
# GET DEVICE PROFILE
# ============================================================

@device_profile_bp.route("/device-profiles/<device_id>", methods=["GET"])
def get_device_profile(device_id):

    conn = get_connection()
    cursor = conn.cursor()

    try:

        cursor.execute(
            """
            SELECT *
            FROM device_profiles
            WHERE device_id = ?
            """,
            (device_id,)
        )

        profile = cursor.fetchone()

        if profile is None:
            return {
                "code": 404,
                "data": None,
                "error": "Device profile not found",
                "status": "error"
            }, 404

        columns = [description[0] for description in cursor.description]
        profile_data = dict(zip(columns, profile))

        # Convert JSON strings back into Python objects
        for field in [
            "original_config",
            "current_config",
            "change_log",
            "stamp_history"
        ]:
            if profile_data[field]:
                profile_data[field] = json.loads(profile_data[field])

        return {
            "code": 200,
            "data": profile_data,
            "error": None,
            "status": "success"
        }, 200

    except Exception as e:

        return {
            "code": 500,
            "data": None,
            "error": str(e),
            "status": "error"
        }, 500

    finally:

        conn.close()


# ============================================================
# ADD HARDWARE CHANGE
# ============================================================

@device_profile_bp.route(
    "/device-profiles/<device_id>/hardware-change",
    methods=["POST"]
)
def add_hardware_change(device_id):

    data = request.get_json()

    if not data:
        return {
            "code": 400,
            "data": None,
            "error": "Request body is required",
            "status": "error"
        }, 400

    required_fields = [
        "component",
        "change_type",
        "declared_by",
        "new_value"
    ]

    for field in required_fields:

        if field not in data:

            return {
                "code": 400,
                "data": None,
                "error": f"{field} is required",
                "status": "error"
            }, 400

    conn = get_connection()
    cursor = conn.cursor()

    try:

        # --------------------------------------------------------
        # Get device profile
        # --------------------------------------------------------

        cursor.execute(
            """
            SELECT *
            FROM device_profiles
            WHERE device_id = ?
            """,
            (device_id,)
        )

        profile = cursor.fetchone()

        if profile is None:

            return {
                "code": 404,
                "data": None,
                "error": "Device profile not found",
                "status": "error"
            }, 404

        columns = [description[0] for description in cursor.description]
        profile_data = dict(zip(columns, profile))

        # --------------------------------------------------------
        # Read current configuration and change log
        # --------------------------------------------------------

        change_log = json.loads(
            profile_data["change_log"] or "[]"
        )

        current_config = json.loads(
            profile_data["current_config"] or "{}"
        )

        # --------------------------------------------------------
        # Read request data
        # --------------------------------------------------------

        component = data["component"]
        change_type = data["change_type"]
        declared_by = data["declared_by"]
        new_value = data["new_value"]

        # --------------------------------------------------------
        # Update existing component
        # Case-insensitive matching
        #
        # Example:
        # Existing key = "ram"
        # Request = "RAM"
        #
        # Result:
        # "ram": "32GB"
        # --------------------------------------------------------

        existing_key = None

        for key in current_config:

            if key.lower() == component.lower():

                existing_key = key
                break

        if existing_key:

            current_config[existing_key] = new_value

        else:

            current_config[component] = new_value

        # --------------------------------------------------------
        # Create hardware change log entry
        # --------------------------------------------------------

        change_entry = {
            "component": component,
            "change_type": change_type,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "declared_by": declared_by
        }

        change_log.append(change_entry)

        # --------------------------------------------------------
        # Read stamp history
        # --------------------------------------------------------

        stamp_history = json.loads(
            profile_data["stamp_history"] or "[]"
        )

        # --------------------------------------------------------
        # Hardware change invalidates previous verification stamp
        # --------------------------------------------------------

        if stamp_history:

            stamp_history[-1]["valid"] = False

        # --------------------------------------------------------
        # Save updated information
        # --------------------------------------------------------

        cursor.execute(
            """
            UPDATE device_profiles
            SET current_config = ?,
                change_log = ?,
                stamp_history = ?
            WHERE device_id = ?
            """,
            (
                json.dumps(current_config),
                json.dumps(change_log),
                json.dumps(stamp_history),
                device_id
            )
        )

        conn.commit()

        # --------------------------------------------------------
        # Response
        # --------------------------------------------------------

        return {
            "code": 200,
            "data": {
                "device_id": device_id,
                "current_config": current_config,
                "change_log": change_log,
                "stamp_history": stamp_history,
                "message": "Hardware change recorded. Re-verification required."
            },
            "error": None,
            "status": "success"
        }, 200

    except Exception as e:

        conn.rollback()

        return {
            "code": 500,
            "data": None,
            "error": str(e),
            "status": "error"
        }, 500

    finally:

        conn.close()