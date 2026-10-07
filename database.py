import sqlite3
from contextlib import closing

DATABASE = "social_media.db"


def get_connection():
    return sqlite3.connect(DATABASE)


def create_tables():

    with closing(get_connection()) as conn:

        conn.execute("""
        CREATE TABLE IF NOT EXISTS historico (

            id INTEGER PRIMARY KEY AUTOINCREMENT,

            data TEXT NOT NULL,

            plataforma TEXT NOT NULL,

            seguidores INTEGER DEFAULT 0,

            engajamento REAL DEFAULT 0,

            alcance INTEGER DEFAULT 0,

            likes INTEGER DEFAULT 0,

            comentarios INTEGER DEFAULT 0,

            partilhas INTEGER DEFAULT 0,

            visualizacoes INTEGER DEFAULT 0,

            criado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """)

        conn.commit()
