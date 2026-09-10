from flask import Blueprint, request
from database import get_connection

ownership_bp = Blueprint("ownership", __name__)


@ownership_bp.route("/devices/<device_id>/assign-owner", methods=["POST"])
def assign_initial_owner(device_id):
    data = request.get_json()

    if not data or "user_id" not in data:
        return {
            "status": "error",
            "code": 400,
            "data": None,
            "error": "user_id is required"
        }, 400

    try:
        connection = get_connection()
        cursor = connection.cursor()

        # Check device
        cursor.execute(
            "SELECT current_owner_id FROM devices WHERE device_id = ?",
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

        # Don't allow changing an existing owner
        if device[0] is not None:
            connection.close()

            return {
                "status": "error",
                "code": 409,
                "data": None,
                "error": "Device already has an owner"
            }, 409

        # Check user
        cursor.execute(
            "SELECT id FROM users WHERE id = ?",
            (data["user_id"],)
        )

        user = cursor.fetchone()

        if not user:
            connection.close()

            return {
                "status": "error",
                "code": 404,
                "data": None,
                "error": "User not found"
            }, 404

        # Assign owner
        cursor.execute(
            """
            UPDATE devices
            SET current_owner_id = ?
            WHERE device_id = ?
            """,
            (data["user_id"], device_id)
        )

        # Record initial ownership
        cursor.execute(
            """
            INSERT INTO ownership_history
            (
                device_id,
                from_user,
                to_user,
                transaction_type
            )
            VALUES (?, ?, ?, ?)
            """,
            (
                device_id,
                None,
                data["user_id"],
                "INITIAL_OWNERSHIP"
            )
        )

        connection.commit()
        connection.close()

        return {
            "status": "success",
            "code": 200,
            "data": {
                "device_id": device_id,
                "owner_id": data["user_id"],
                "transaction_type": "INITIAL_OWNERSHIP"
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


@ownership_bp.route("/devices/<device_id>/history", methods=["GET"])
def ownership_history(device_id):
    try:
        connection = get_connection()
        cursor = connection.cursor()

        # Check whether device exists
        cursor.execute(
            "SELECT device_id FROM devices WHERE device_id = ?",
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

        # Get ownership history
        cursor.execute(
            """
            SELECT
                oh.id,
                oh.from_user,
                oh.to_user,
                oh.transfer_date,
                oh.transaction_type,
                u.name
            FROM ownership_history oh
            JOIN users u ON oh.to_user = u.id
            WHERE oh.device_id = ?
            ORDER BY oh.id ASC
            """,
            (device_id,)
        )

        records = cursor.fetchall()

        connection.close()

        history = []

        for record in records:
            history.append({
                "history_id": record[0],
                "from_user": record[1],
                "to_user": record[2],
                "owner_name": record[5],
                "date": record[3],
                "transaction_type": record[4]
            })

        return {
            "status": "success",
            "code": 200,
            "data": {
                "device_id": device_id,
                "ownership_history": history
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
@ownership_bp.route("/devices/<device_id>/transfer", methods=["POST"])
def transfer_device(device_id):
    data = request.get_json()

    if not data:
        return {
            "status": "error",
            "code": 400,
            "data": None,
            "error": "Request body is required"
        }, 400

    if "from_user" not in data or "to_user" not in data:
        return {
            "status": "error",
            "code": 400,
            "data": None,
            "error": "from_user and to_user are required"
        }, 400

    try:
        connection = get_connection()
        cursor = connection.cursor()

        # Check device
        cursor.execute(
            "SELECT current_owner_id, status FROM devices WHERE device_id = ?",
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
        device_status = device[1]

        # Check current owner
        if current_owner_id != data["from_user"]:
            connection.close()

            return {
                "status": "error",
                "code": 403,
                "data": None,
                "error": "Transfer can only be made by the current owner"
            }, 403

        # Prevent transferring to yourself
        if data["from_user"] == data["to_user"]:
            connection.close()

            return {
                "status": "error",
                "code": 400,
                "data": None,
                "error": "Sender and receiver cannot be the same user"
            }, 400

        # Check receiver
        cursor.execute(
            "SELECT id FROM users WHERE id = ?",
            (data["to_user"],)
        )

        receiver = cursor.fetchone()

        if not receiver:
            connection.close()

            return {
                "status": "error",
                "code": 404,
                "data": None,
                "error": "Receiver not found"
            }, 404

        # Record ownership history
        cursor.execute(
            """
            INSERT INTO ownership_history
            (
                device_id,
                from_user,
                to_user,
                transaction_type
            )
            VALUES (?, ?, ?, ?)
            """,
            (
                device_id,
                data["from_user"],
                data["to_user"],
                "OWNERSHIP_TRANSFER"
            )
        )

        # Update current owner
        cursor.execute(
            """
            UPDATE devices
            SET current_owner_id = ?
            WHERE device_id = ?
            """,
            (
                data["to_user"],
                device_id
            )
        )

        connection.commit()
        connection.close()

        return {
            "status": "success",
            "code": 200,
            "data": {
                "device_id": device_id,
                "from_user": data["from_user"],
                "to_user": data["to_user"],
                "transaction_type": "OWNERSHIP_TRANSFER",
                "status": device_status
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