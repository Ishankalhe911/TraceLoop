from flask import Blueprint, request
from database import get_connection

device_bp = Blueprint("device", __name__)


@device_bp.route("/devices", methods=["POST"])
def register_device():
    data = request.get_json()

    required_fields = [
        "device_type",
        "brand",
        "model",
        "serial_number"
    ]

    for field in required_fields:
        if field not in data:
            return {
                "code": 400,
                "data": None,
                "error": f"{field} is required",
                "status": "error"
            }, 400

    device_type = data["device_type"]
    brand = data["brand"]
    model = data["model"]
    serial_number = data["serial_number"]

    conn = get_connection()
    cursor = conn.cursor()

    try:
        # Find the next available TraceLoop device number
        cursor.execute(
            "SELECT COUNT(*) FROM devices"
        )

        count = cursor.fetchone()[0]
        device_id = f"TL{count + 1:06d}"

        # Insert the new device
        cursor.execute(
            """
            INSERT INTO devices
            (device_id, device_type, brand, model, serial_number, status)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                device_id,
                device_type,
                brand,
                model,
                serial_number,
                "REGISTERED"
            )
        )

        conn.commit()

        return {
            "code": 201,
            "data": {
                "device_id": device_id,
                "device_type": device_type,
                "brand": brand,
                "model": model,
                "serial_number": serial_number,
                "status": "REGISTERED"
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


@device_bp.route("/devices/<device_id>", methods=["GET"])
def get_device(device_id):
    conn = get_connection()
    cursor = conn.cursor()

    try:
        cursor.execute(
            """
            SELECT *
            FROM devices
            WHERE device_id = ?
            """,
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

        columns = [description[0] for description in cursor.description]
        device_data = dict(zip(columns, device))

        return {
            "code": 200,
            "data": device_data,
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