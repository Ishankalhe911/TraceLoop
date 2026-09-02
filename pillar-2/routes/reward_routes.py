from flask import Blueprint, request
from database import get_connection

reward_bp = Blueprint("reward", __name__)
@reward_bp.route("/rewards", methods=["POST"])
def add_reward_points():
    try:
        data = request.get_json()
        user_id = data.get("user_id")
        points = data.get("points")
        action = data.get("action")
        device_id = data.get("device_id")

        if not user_id or not points or not action:
            return {
                "status": "error",
                "code": 400,
                "data": None,
                "error": "user_id, points and action are required"
            }, 400

        connection = get_connection()
        cursor = connection.cursor()

        cursor.execute(
            """
            INSERT INTO reward_points
            (user_id, points, action, device_id)
            VALUES (?, ?, ?, ?)
            """,
            (user_id, points, action, device_id)
        )

        connection.commit()
        reward_id = cursor.lastrowid
        connection.close()

        return {
            "status": "success",
            "code": 201,
            "data": {
                "reward_id": reward_id,
                "user_id": user_id,
                "points": points,
                "action": action,
                "device_id": device_id
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
@reward_bp.route("/rewards/<int:user_id>", methods=["GET"])
def get_reward_points(user_id):
    try:
        connection = get_connection()
        cursor = connection.cursor()

        cursor.execute(
            """
            SELECT COALESCE(SUM(points), 0)
            FROM reward_points
            WHERE user_id = ?
            """,
            (user_id,)
        )

        total_points = cursor.fetchone()[0]
        connection.close()

        return {
            "status": "success",
            "code": 200,
            "data": {
                "user_id": user_id,
                "total_points": total_points
            },
            "error": None
        }, 200

    except Exception as e:
        return {
            "status": "error",
            "code": 500,
            "data": None,
            "error": str(e)
        }, 500