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


def inserir_metrica(
    data,
    plataforma,
    seguidores,
    engajamento,
    alcance,
    likes=0,
    comentarios=0,
    partilhas=0,
    visualizacoes=0
):

    with closing(get_connection()) as conn:

        conn.execute(
            """
            INSERT INTO historico (

                data,
                plataforma,
                seguidores,
                engajamento,
                alcance,
                likes,
                comentarios,
                partilhas,
                visualizacoes

            )

            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data,
                plataforma,
                seguidores,
                engajamento,
                alcance,
                likes,
                comentarios,
                partilhas,
                visualizacoes
            )
        )

        conn.commit()


def carregar_historico():

    with closing(get_connection()) as conn:

        cursor = conn.cursor()

        cursor.execute("""
            SELECT *
            FROM historico
            ORDER BY data ASC
        """)

        return cursor.fetchall()


def apagar_historico():

    with closing(get_connection()) as conn:

        conn.execute(
            "DELETE FROM historico"
        )

        conn.commit()


def obter_kpis():

    with closing(get_connection()) as conn:

        cursor = conn.cursor()

        cursor.execute("""
            SELECT

                MAX(seguidores),

                MAX(alcance),

                AVG(engajamento),

                SUM(likes),

                SUM(comentarios),

                SUM(partilhas),

                SUM(visualizacoes)

            FROM historico
        """)

        return cursor.fetchone()
