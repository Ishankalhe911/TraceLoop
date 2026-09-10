from flask import Blueprint
from database import get_connection


manufacturer_bp = Blueprint("manufacturer", __name__)


# Mock manufacturer database
MOCK_MANUFACTURER_DATA = {
    "DLTEST001": {
        "brand": "Dell",
        "model": "Latitude 5420",
        "serial_id": "DLTEST001",
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
        "serial_id": "HPTEST001",
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
        "serial_id": "LNTEST001",
        "original_config": {
            "cpu": "AMD Ryzen 5 PRO",
            "ram": "16GB",
            "storage": "512GB SSD",
            "display": "14 inch"
        }
    }
}


@manufacturer_bp.route("/manufacturer/lookup/<serial_id>", methods=["GET"])
def manufacturer_lookup(serial_id):

    # Search mock manufacturer database
    manufacturer_data = MOCK_MANUFACTURER_DATA.get(serial_id)

    if manufacturer_data is None:
        return {
            "code": 404,
            "data": None,
            "error": "Serial number not found in manufacturer database",
            "status": "error"
        }, 404

    return {
        "code": 200,
        "data": manufacturer_data,
        "error": None,
        "status": "success"
    }, 200