from flask import Flask, request, jsonify
from flask_cors import CORS
from database import create_tables, get_connection
from routes.device_routes import device_bp
from routes.user_routes import user_bp
from routes.ownership_routes import ownership_bp
from routes.recycler_routes import recycler_bp
from routes.recycling_routes import recycling_bp
from routes.certificate_routes import certificate_bp
from routes.reward_routes import reward_bp

# Pillar 3 routes
from routes.device_profile_routes import device_profile_bp
from routes.manufacturer_routes import manufacturer_bp
from routes.verification_routes import verification_bp

app = Flask(__name__)
CORS(app)
app.register_blueprint(device_bp)
app.register_blueprint(user_bp)
app.register_blueprint(ownership_bp)
app.register_blueprint(recycler_bp)
app.register_blueprint(recycling_bp)
app.register_blueprint(certificate_bp)
app.register_blueprint(reward_bp)
# Register Pillar 3 blueprints
app.register_blueprint(device_profile_bp)
app.register_blueprint(manufacturer_bp)
app.register_blueprint(verification_bp)


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
            
        }, 500
if __name__ == "__main__":
    app.run(debug=True)