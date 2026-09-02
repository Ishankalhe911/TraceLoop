import sqlite3

DATABASE = "traceloop.db"


def get_connection():
    return sqlite3.connect(DATABASE)


def create_tables():
    connection = get_connection()
    cursor = connection.cursor()

    # Users table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            email TEXT UNIQUE NOT NULL,
            phone TEXT,
            role TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Devices table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS devices (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            device_id TEXT UNIQUE NOT NULL,
            device_type TEXT NOT NULL,
            brand TEXT NOT NULL,
            model TEXT,
            serial_number TEXT UNIQUE NOT NULL,
            current_owner_id INTEGER,
            status TEXT NOT NULL DEFAULT 'REGISTERED',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (current_owner_id) REFERENCES users(id)
        )
    """)

    # Ownership history
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS ownership_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            device_id TEXT NOT NULL,
            from_user INTEGER,
            to_user INTEGER NOT NULL,
            transfer_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            transaction_type TEXT NOT NULL,
            FOREIGN KEY (from_user) REFERENCES users(id),
            FOREIGN KEY (to_user) REFERENCES users(id)
        )
    """)

    # Recyclers table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS recyclers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            organization TEXT,
            contact TEXT,
            license_number TEXT UNIQUE NOT NULL,
            verification_status TEXT NOT NULL DEFAULT 'PENDING',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Recycling records
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS recycling_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            device_id TEXT NOT NULL,
            recycler_id INTEGER NOT NULL,
            received_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            condition TEXT,
            recycling_status TEXT NOT NULL DEFAULT 'RECEIVED',
            certificate_id TEXT UNIQUE,
            completed_date TIMESTAMP,
            FOREIGN KEY (recycler_id) REFERENCES recyclers(id)
        )
    """)

    connection.commit()
    connection.close()

    print("TRACE-LOOP database created successfully!")


if __name__ == "__main__":
    create_tables()