from flask import Blueprint
from database import get_connection
import json
from datetime import datetime, timezone

verification_bp = Blueprint("verification", __name__)


@verification_bp.route("/device-profiles/<device_id>/verify", methods=["GET"])
def verify_device(device_id):

    conn = get_connection()
    cursor = conn.cursor()

    try:
        # Get device profile
        cursor.execute(
            "SELECT * FROM device_profiles WHERE device_id = ?",
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

        # Convert database row to dictionary
        columns = [description[0] for description in cursor.description]
        profile_data = dict(zip(columns, profile))

        serial_id = profile_data["serial_id"]

        current_config = json.loads(
            profile_data["current_config"] or "{}"
        )

        change_log = json.loads(
            profile_data["change_log"] or "[]"
        )

        stamp_history = json.loads(
            profile_data["stamp_history"] or "[]"
        )

        current_status = profile_data["status"]

        # Mock manufacturer database
        manufacturer_data = {
            "DLTEST001": {
                "brand": "Dell",
                "model": "Latitude 5420",
                "original_config": {
                    "cpu": "Intel Core i5-1145G7",
                    "ram": "16GB",
                    "storage": "512GB SSD",
                    "display": "14 inch"
                }
            },

            "HPTEST001": {
                "brand": "HP",
                "model": "EliteBook 840 G8",
                "original_config": {
                    "cpu": "Intel Core i5-1135G7",
                    "ram": "16GB",
                    "storage": "512GB SSD",
                    "display": "14 inch"
                }
            },

            "LNTEST001": {
                "brand": "Lenovo",
                "model": "ThinkPad T14",
                "original_config": {
                    "cpu": "AMD Ryzen 5 PRO",
                    "ram": "16GB",
                    "storage": "512GB SSD",
                    "display": "14 inch"
                }
            }
        }

        manufacturer = manufacturer_data.get(serial_id)

        if manufacturer is None:
            return {
                "code": 404,
                "data": None,
                "error": "Serial number not found in manufacturer database",
                "status": "error"
            }, 404

        manufacturer_config = manufacturer["original_config"]

        # Compare current configuration with manufacturer configuration
        differences = []

        all_components = set(
            manufacturer_config.keys()
        ).union(current_config.keys())

        for component in all_components:

            manufacturer_value = manufacturer_config.get(component)
            current_value = current_config.get(component)

            if manufacturer_value != current_value:

                differences.append({
                    "component": component,
                    "manufacturer_value": manufacturer_value,
                    "current_value": current_value
                })

        # --------------------------------------------------
        # VERIFICATION RESULT
        # --------------------------------------------------

        if differences:

            verification_status = "VERIFICATION_REVIEW_REQUIRED"

            message = (
                "Configuration differs from manufacturer records. "
                "Review documented hardware changes."
            )

            cursor.execute(
                """
                UPDATE device_profiles
                SET status = ?
                WHERE device_id = ?
                """,
                ("REVIEW_REQUIRED", device_id)
            )

            conn.commit()

        else:

            verification_status = "VERIFIED"

            message = (
                "Device configuration matches manufacturer records."
            )

            # Check whether a valid stamp already exists
            valid_stamp = None

            for stamp in stamp_history:
                if stamp.get("status") == "VALID":
                    valid_stamp = stamp
                    break

            # Create a new stamp only when there is no valid stamp
            if valid_stamp is None:

                stamp = {
                    "stamp_id": (
                        f"STAMP-{device_id}-{len(stamp_history) + 1:04d}"
                    ),
                    "device_id": device_id,
                    "verified_at": datetime.now(
                        timezone.utc
                    ).isoformat(),
                    "verified_by": "VERIFIABLE",
                    "status": "VALID"
                }

                stamp_history.append(stamp)

                cursor.execute(
                    """
                    UPDATE device_profiles
                    SET status = ?,
                        stamp_history = ?
                    WHERE device_id = ?
                    """,
                    (
                        "VERIFIED",
                        json.dumps(stamp_history),
                        device_id
                    )
                )

                conn.commit()

            elif current_status != "VERIFIED":

                cursor.execute(
                    """
                    UPDATE device_profiles
                    SET status = ?
                    WHERE device_id = ?
                    """,
                    ("VERIFIED", device_id)
                )

                conn.commit()

        return {
            "code": 200,

            "data": {
                "device_id": device_id,
                "serial_id": serial_id,
                "brand": profile_data["brand"],
                "manufacturer_model": manufacturer["model"],

                "manufacturer_config": manufacturer_config,

                "current_config": current_config,

                "differences": differences,

                "change_log": change_log,

                "stamp_history": stamp_history,

                "verification_status": verification_status,

                "message": message
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