from flask import Flask, request
from database import create_tables, get_connection

app = Flask(__name__)

# Create database tables
create_tables()


@app.route("/")
def home():
    return {
        "status": "success",
        "code": 200,
        "data": {
            "message": "Trace-Loop Pillar 2 is running"
        },
        "error": None,
        "timestamp": "ISO8601-UTC",
        "traceloop_version": "v1"
    }


@app.route("/database-test")
def database_test():
    try:
        connection = get_connection()
        cursor = connection.cursor()

        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )

        tables = cursor.fetchall()

        connection.close()

        return {
            "status": "success",
            "code": 200,
            "data": {
                "database": "connected",
                "tables": [table[0] for table in tables]
            },
            "error": None
        }

    except Exception as e:
        return {
            "status": "error",
            "code": 500,
            "data": None,
            "error": str(e)
        }


# Register a new device
@app.route("/devices", methods=["POST"])
def register_device():

    data = request.get_json()

    if not data:
        return {
            "status": "error",
            "code": 400,
            "data": None,
            "error": "Request body is required"
        }, 400

    required_fields = [
        "device_type",
        "brand",
        "model",
        "serial_number"
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

        # Check whether serial number already exists
        cursor.execute(
            "SELECT device_id FROM devices WHERE serial_number = ?",
            (data["serial_number"],)
        )

        existing_device = cursor.fetchone()

        if existing_device:
            connection.close()

            return {
                "status": "error",
                "code": 409,
                "data": None,
                "error": "Device with this serial number already exists"
            }, 409

        # Generate next TRACE-LOOP device ID
        cursor.execute(
            "SELECT COUNT(*) FROM devices"
        )

        count = cursor.fetchone()[0]
        device_id = f"TL{count + 1:06d}"

        # Insert device
        cursor.execute(
            """
            INSERT INTO devices
            (
                device_id,
                device_type,
                brand,
                model,
                serial_number,
                status
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                device_id,
                data["device_type"],
                data["brand"],
                data["model"],
                data["serial_number"],
                "REGISTERED"
            )
        )

        connection.commit()
        connection.close()

        return {
            "status": "success",
            "code": 201,
            "data": {
                "device_id": device_id,
                "device_type": data["device_type"],
                "brand": data["brand"],
                "model": data["model"],
                "serial_number": data["serial_number"],
                "status": "REGISTERED"
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
@app.route("/devices/<device_id>", methods=["GET"])
def get_device(device_id):
    try:
        connection = get_connection()
        cursor = connection.cursor()

        cursor.execute(
            """
            SELECT
                device_id,
                device_type,
                brand,
                model,
                serial_number,
                current_owner_id,
                status,
                created_at
            FROM devices
            WHERE device_id = ?
            """,
            (device_id,)
        )

        device = cursor.fetchone()
        connection.close()

        if not device:
            return {
                "status": "error",
                "code": 404,
                "data": None,
                "error": "Device not found"
            }, 404

        return {
            "status": "success",
            "code": 200,
            "data": {
                "device_id": device[0],
                "device_type": device[1],
                "brand": device[2],
                "model": device[3],
                "serial_number": device[4],
                "current_owner_id": device[5],
                "status": device[6],
                "created_at": device[7]
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
@app.route("/users", methods=["POST"])
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
@app.route("/devices/<device_id>/transfer", methods=["POST"])
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
@app.route("/devices/<device_id>/assign-owner", methods=["POST"])
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
@app.route("/devices/<device_id>/history", methods=["GET"])
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
@app.route("/recyclers", methods=["POST"])
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
@app.route("/recyclers/<int:recycler_id>/verify", methods=["POST"])
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
@app.route("/devices/<device_id>/recycle", methods=["POST"])
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

        # Check device
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

        # Device must have an owner
        if current_owner_id is None:
            connection.close()

            return {
                "status": "error",
                "code": 400,
                "data": None,
                "error": "Device does not have an owner"
            }, 400

        # Device should not already be recycled
        if current_status in ["SENT_TO_RECYCLER", "RECEIVED_BY_RECYCLER", "PROCESSING", "RECYCLED"]:
            connection.close()

            return {
                "status": "error",
                "code": 409,
                "data": None,
                "error": "Device is already in the recycling process"
            }, 409

        # Check recycler
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

        # Recycler must be verified
        if recycler[1] != "VERIFIED":
            connection.close()

            return {
                "status": "error",
                "code": 403,
                "data": None,
                "error": "Recycler is not verified"
            }, 403

        # Update device status
        cursor.execute(
            """
            UPDATE devices
            SET status = 'SENT_TO_RECYCLER'
            WHERE device_id = ?
            """,
            (device_id,)
        )

        # Create recycling record
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
@app.route("/devices/<device_id>/receive", methods=["POST"])
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

        # Check device
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

        # Device must be sent to recycler first
        if device[0] != "SENT_TO_RECYCLER":
            connection.close()

            return {
                "status": "error",
                "code": 400,
                "data": None,
                "error": "Device is not waiting for recycler receipt"
            }, 400

        # Check recycler
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

        # Update device status
        cursor.execute(
            """
            UPDATE devices
            SET status = 'RECEIVED_BY_RECYCLER'
            WHERE device_id = ?
            """,
            (device_id,)
        )

        # Update recycling record
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
@app.route("/devices/<device_id>/complete-recycling", methods=["POST"])
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

        # Check device
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

        # Device must be received by recycler
        if device[0] != "RECEIVED_BY_RECYCLER":
            connection.close()

            return {
                "status": "error",
                "code": 400,
                "data": None,
                "error": "Device has not been received by recycler"
            }, 400

        # Check recycler
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

        # Generate certificate ID
        cursor.execute(
            """
            SELECT COUNT(*) FROM recycling_records
            """
        )

        count = cursor.fetchone()[0] + 1

        certificate_id = f"TRC-CERT-{count:05d}"

        # Update device
        cursor.execute(
            """
            UPDATE devices
            SET status = 'RECYCLED'
            WHERE device_id = ?
            """,
            (device_id,)
        )

        # Update recycling record
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
        connection.close()

        return {
            "status": "success",
            "code": 200,
            "data": {
                "device_id": device_id,
                "recycler_id": data["recycler_id"],
                "status": "RECYCLED",
                "certificate_id": certificate_id
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
@app.route("/certificates/<certificate_id>", methods=["GET"])
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
                "organization": record[6],
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
@app.route("/certificates/<certificate_id>/qr", methods=["GET"])
def certificate_qr(certificate_id):
    try:
        import qrcode
        from io import BytesIO
        from flask import send_file

        verification_url = f"http://127.0.0.1:5000/certificates/{certificate_id}"

        qr = qrcode.make(verification_url)

        buffer = BytesIO()
        qr.save(buffer, format="PNG")
        buffer.seek(0)

        return send_file(
            buffer,
            mimetype="image/png",
            download_name=f"{certificate_id}.png"
        )

    except Exception as e:
        return {
            "status": "error",
            "code": 500,
            "data": None,
            "error": str(e)
        }, 500
@app.route("/rewards", methods=["POST"])
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
@app.route("/rewards/<int:user_id>", methods=["GET"])
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
if __name__ == "__main__":
    app.run(debug=True)