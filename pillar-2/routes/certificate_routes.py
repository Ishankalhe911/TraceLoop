from flask import Blueprint
from database import get_connection

certificate_bp = Blueprint("certificate", __name__)
@certificate_bp.route("/certificates/<certificate_id>", methods=["GET"])
def verify_certificate(certificate_id):
    try:
        connection = get_connection()
        cursor = connection.cursor()

        cursor.execute(
            """
            SELECT
                rr.certificate_id,
                rr.device_id,
                rr.recycler_id,
                rr.recycling_status,
                rr.completion_date,
                r.name,
                r.organization
            FROM recycling_records rr
            JOIN recyclers r ON rr.recycler_id = r.id
            WHERE rr.certificate_id = ?
            """,
            (certificate_id,)
        )

        record = cursor.fetchone()
        connection.close()

        if not record:
            return {
                "status": "error",
                "code": 404,
                "data": None,
                "error": "Certificate not found"
            }, 404

        return {
            "status": "success",
            "code": 200,
            "data": {
    "certificate_id": record[0],
    "device_id": record[1],
    "recycler_id": record[2],
    "recycling_status": record[3],
    "completion_date": record[4],
    "recycler_name": record[5],
    "recycler_organization": record[6],
    "verification": "VALID"
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