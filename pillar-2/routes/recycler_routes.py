from flask import Blueprint, request
from database import get_connection

recycler_bp = Blueprint("recycler", __name__)


@recycler_bp.route("/recyclers", methods=["POST"])
def register_recycler():
    data = request.get_json()

    if not data:
        return {
            "status": "error",
            "code": 400,
            "data": None,
            "error": "Request body is required"
        }, 400

    required_fields = [
        "name",
        "organization",
        "contact",
        "license_number"
    ]

    for field in required_fields:
        if field not in data or not data[field]:
            return {
                "status": "error",
                "code": 400,
                "data": None,
                "error": f"{field} is required"
            }, 400

    try:
        connection = get_connection()
        cursor = connection.cursor()

        # Check duplicate license
        cursor.execute(
            "SELECT id FROM recyclers WHERE license_number = ?",
            (data["license_number"],)
        )

        existing = cursor.fetchone()

        if existing:
            connection.close()

            return {
                "status": "error",
                "code": 409,
                "data": None,
                "error": "Recycler with this license number already exists"
            }, 409

        # Register recycler
        cursor.execute(
            """
            INSERT INTO recyclers
            (
                name,
                organization,
                contact,
                license_number,
                verification_status
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                data["name"],
                data["organization"],
                data["contact"],
                data["license_number"],
                "PENDING"
            )
        )

        connection.commit()

        recycler_id = cursor.lastrowid

        connection.close()

        return {
            "status": "success",
            "code": 201,
            "data": {
                "recycler_id": recycler_id,
                "name": data["name"],
                "organization": data["organization"],
                "contact": data["contact"],
                "license_number": data["license_number"],
                "verification_status": "PENDING"
            },
            "error": None
        }, 201

    except Exception as e:

        return {
            "status": "error",
            "code": 500,
            "data": None,
            "error": str(e)
        }, 500


@recycler_bp.route("/recyclers/<int:recycler_id>/verify", methods=["POST"])
def verify_recycler(recycler_id):
    try:
        connection = get_connection()
        cursor = connection.cursor()

        # Check recycler
        cursor.execute(
            """
            SELECT id, verification_status
            FROM recyclers
            WHERE id = ?
            """,
            (recycler_id,)
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

        # Check if already verified
        if recycler[1] == "VERIFIED":
            connection.close()

            return {
                "status": "success",
                "code": 200,
                "data": {
                    "recycler_id": recycler_id,
                    "verification_status": "VERIFIED"
                },
                "error": None
            }

        # Verify recycler
        cursor.execute(
            """
            UPDATE recyclers
            SET verification_status = 'VERIFIED'
            WHERE id = ?
            """,
            (recycler_id,)
        )

        connection.commit()
        connection.close()

        return {
            "status": "success",
            "code": 200,
            "data": {
                "recycler_id": recycler_id,
                "verification_status": "VERIFIED"
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