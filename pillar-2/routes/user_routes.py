from flask import Blueprint, request
from database import get_connection

user_bp = Blueprint("user", __name__)


@user_bp.route("/users", methods=["POST"])
def register_user():

    data = request.get_json()

    if not data:
        return {
            "status": "error",
            "code": 400,
            "data": None,
            "error": "Request body is required"
        }, 400

    required_fields = ["name", "email", "role"]

    for field in required_fields:
        if field not in data or not data[field]:
            return {
                "status": "error",
                "code": 400,
                "data": None,
                "error": f"{field} is required"
            }, 400

    allowed_roles = ["USER", "BUYER", "SELLER", "RECYCLER", "ADMIN"]

    if data["role"].upper() not in allowed_roles:
        return {
            "status": "error",
            "code": 400,
            "data": None,
            "error": "Invalid role"
        }, 400

    try:
        connection = get_connection()
        cursor = connection.cursor()

        cursor.execute(
            """
            INSERT INTO users (name, email, phone, role)
            VALUES (?, ?, ?, ?)
            """,
            (
                data["name"],
                data["email"],
                data.get("phone"),
                data["role"].upper()
            )
        )

        connection.commit()

        user_id = cursor.lastrowid

        connection.close()

        return {
            "status": "success",
            "code": 201,
            "data": {
                "user_id": user_id,
                "name": data["name"],
                "email": data["email"],
                "phone": data.get("phone"),
                "role": data["role"].upper()
            },
            "error": None
        }, 201

    except Exception as e:

        if "UNIQUE constraint failed" in str(e):
            return {
                "status": "error",
                "code": 409,
                "data": None,
                "error": "Email already registered"
            }, 409

        return {
            "status": "error",
            "code": 500,
            "data": None,
            "error": str(e)
        }, 500