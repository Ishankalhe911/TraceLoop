from services.ledger_service import create_ledger_entry
from services.subscriber_service import notify_subscribers, log_event

from flask import Blueprint, request
from database import get_connection

recycling_bp = Blueprint("recycling", __name__)


@recycling_bp.route("/devices/<device_id>/recycle", methods=["POST"])
def send_to_recycler(device_id):
    data = request.get_json()

    if not data or "recycler_id" not in data:
        return {
            "status": "error",
            "code": 400,
            "data": None,
            "error": "recycler_id is required"
        }, 400

    try:
        connection = get_connection()
        cursor = connection.cursor()

        cursor.execute(
            """
            SELECT current_owner_id, status
            FROM devices
            WHERE device_id = ?
            """,
            (device_id,)
        )

        device = cursor.fetchone()

        if not device:
            connection.close()
            return {
                "status": "error",
                "code": 404,
                "data": None,
                "error": "Device not found"
            }, 404

        current_owner_id = device[0]
        current_status = device[1]

        if current_owner_id is None:
            connection.close()
            return {
                "status": "error",
                "code": 400,
                "data": None,
                "error": "Device does not have an owner"
            }, 400

        if current_status in [
            "SENT_TO_RECYCLER",
            "RECEIVED_BY_RECYCLER",
            "PROCESSING",
            "RECYCLED"
        ]:
            connection.close()
            return {
                "status": "error",
                "code": 409,
                "data": None,
                "error": "Device is already in the recycling process"
            }, 409

        cursor.execute(
            """
            SELECT id, verification_status
            FROM recyclers
            WHERE id = ?
            """,
            (data["recycler_id"],)
        )

        recycler = cursor.fetchone()

        if not recycler:
            connection.close()
            return {
                "status": "error",
                "code": 404,
                "data": None,
                "error": "Recycler not found"
            }, 404

        if recycler[1] != "VERIFIED":
            connection.close()
            return {
                "status": "error",
                "code": 403,
                "data": None,
                "error": "Recycler is not verified"
            }, 403

        cursor.execute(
            """
            UPDATE devices
            SET status = 'SENT_TO_RECYCLER'
            WHERE device_id = ?
            """,
            (device_id,)
        )

        cursor.execute(
            """
            INSERT INTO recycling_records
            (
                device_id,
                recycler_id,
                recycling_status
            )
            VALUES (?, ?, ?)
            """,
            (
                device_id,
                data["recycler_id"],
                "SENT"
            )
        )
        connection.commit()
        connection.close()
        return {
            "status": "success",
            "code": 200,
            "data": {
                "device_id": device_id,
                "recycler_id": data["recycler_id"],
                "status": "SENT_TO_RECYCLER"
            },
            "error": None
        }

    except Exception as e:
        return {
            "status": "error",
            "code": 500,
            "data": None,
            "error": str(e)
        }, 500


@recycling_bp.route("/devices/<device_id>/receive", methods=["POST"])
def receive_device(device_id):
    data = request.get_json()

    if not data or "recycler_id" not in data:
        return {
            "status": "error",
            "code": 400,
            "data": None,
            "error": "recycler_id is required"
        }, 400

    try:
        connection = get_connection()
        cursor = connection.cursor()

        cursor.execute(
            """
            SELECT status
            FROM devices
            WHERE device_id = ?
            """,
            (device_id,)
        )

        device = cursor.fetchone()

        if not device:
            connection.close()
            return {
                "status": "error",
                "code": 404,
                "data": None,
                "error": "Device not found"
            }, 404

        if device[0] != "SENT_TO_RECYCLER":
            connection.close()
            return {
                "status": "error",
                "code": 400,
                "data": None,
                "error": "Device is not waiting for recycler receipt"
            }, 400

        cursor.execute(
            """
            SELECT verification_status
            FROM recyclers
            WHERE id = ?
            """,
            (data["recycler_id"],)
        )

        recycler = cursor.fetchone()

        if not recycler:
            connection.close()
            return {
                "status": "error",
                "code": 404,
                "data": None,
                "error": "Recycler not found"
            }, 404

        if recycler[0] != "VERIFIED":
            connection.close()
            return {
                "status": "error",
                "code": 403,
                "data": None,
                "error": "Recycler is not verified"
            }, 403

        cursor.execute(
            """
            UPDATE devices
            SET status = 'RECEIVED_BY_RECYCLER'
            WHERE device_id = ?
            """,
            (device_id,)
        )

        cursor.execute(
            """
            UPDATE recycling_records
            SET recycling_status = 'RECEIVED',
                received_date = CURRENT_TIMESTAMP
            WHERE device_id = ?
              AND recycler_id = ?
            """,
            (
                device_id,
                data["recycler_id"]
            )
        )

        connection.commit()
        connection.close()

        return {
            "status": "success",
            "code": 200,
            "data": {
                "device_id": device_id,
                "recycler_id": data["recycler_id"],
                "status": "RECEIVED_BY_RECYCLER"
            },
            "error": None
        }

    except Exception as e:
        return {
            "status": "error",
            "code": 500,
            "data": None,
            "error": str(e)
        }, 500


@recycling_bp.route("/devices/<device_id>/complete-recycling", methods=["POST"])
def complete_recycling(device_id):
    data = request.get_json()

    if not data or "recycler_id" not in data:
        return {
            "status": "error",
            "code": 400,
            "data": None,
            "error": "recycler_id is required"
        }, 400

    try:
        connection = get_connection()
        cursor = connection.cursor()

        cursor.execute(
            """
            SELECT status
            FROM devices
            WHERE device_id = ?
            """,
            (device_id,)
        )

        device = cursor.fetchone()

        if not device:
            connection.close()
            return {
                "status": "error",
                "code": 404,
                "data": None,
                "error": "Device not found"
            }, 404

        if device[0] != "RECEIVED_BY_RECYCLER":
            connection.close()
            return {
                "status": "error",
                "code": 400,
                "data": None,
                "error": "Device has not been received by recycler"
            }, 400

        cursor.execute(
            """
            SELECT verification_status
            FROM recyclers
            WHERE id = ?
            """,
            (data["recycler_id"],)
        )

        recycler = cursor.fetchone()

        if not recycler:
            connection.close()
            return {
                "status": "error",
                "code": 404,
                "data": None,
                "error": "Recycler not found"
            }, 404

        if recycler[0] != "VERIFIED":
            connection.close()
            return {
                "status": "error",
                "code": 403,
                "data": None,
                "error": "Recycler is not verified"
            }, 403

        cursor.execute(
            """
            SELECT COUNT(*) FROM recycling_records
            """
        )

        count = cursor.fetchone()[0] + 1

        certificate_id = f"TRC-CERT-{count:05d}"

        cursor.execute(
            """
            UPDATE devices
            SET status = 'RECYCLED'
            WHERE device_id = ?
            """,
            (device_id,)
        )

        cursor.execute(
            """
            UPDATE recycling_records
            SET recycling_status = 'RECYCLED',
                completion_date = CURRENT_TIMESTAMP,
                certificate_id = ?
            WHERE device_id = ?
              AND recycler_id = ?
            """,
            (
                certificate_id,
                device_id,
                data["recycler_id"]
            )
        )

        connection.commit()

        # Create ledger entry for completed recycling
        ledger_entry = create_ledger_entry(
            "DEVICE_RECYCLED",
            device_id,
            {
                "certificate_id": certificate_id,
                "recycler_id": data["recycler_id"]
            }
        )
        subscriber_notifications = notify_subscribers(
            "DEVICE_RECYCLED",
            {
                "device_id": device_id,
                "certificate_id": certificate_id,
                "recycler_id": data["recycler_id"]
            }
        )
        connection.close()

        return {
            "status": "success",
            "code": 200,
            "data": {
                "device_id": device_id,
                "recycler_id": data["recycler_id"],
                "status": "RECYCLED",
                "certificate_id": certificate_id,
                "ledger_entry": ledger_entry,
                "subscriber_notifications": subscriber_notifications
            },
            "error": None
        }

    except Exception as e:
        return {
            "status": "error",
            "code": 500,
            "data": None,
            "error": str(e)
        }, 500
