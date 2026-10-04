import sqlite3

DATABASE = "social_media.db"


def get_connection():
    return sqlite3.connect(DATABASE)


def create_tables():
    conn = get_connection()

    conn.execute("""
        CREATE TABLE IF NOT EXISTS historico (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            data TEXT,
            plataforma TEXT,
            seguidores INTEGER,
            engajamento REAL,
            alcance INTEGER
        )
    """)

    conn.commit()
    conn.close()
