import bcrypt

from database import get_connection


def criar_utilizador(nome, email, password):
    nome = (nome or "").strip()
    email = (email or "").strip().lower()

    if not nome or not email or not password:
        return False, "Preencha todos os campos."
    if len(password) < 8:
        return False, "A palavra-passe deve ter pelo menos 8 caracteres."

    password_hash = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM users WHERE LOWER(email) = LOWER(%s)", (email,))
            if cur.fetchone():
                return False, "Este email já está registado."
            cur.execute(
                "INSERT INTO users (name, email, password_hash) VALUES (%s, %s, %s) RETURNING id",
                (nome, email, password_hash),
            )
            user_id = str(cur.fetchone()[0])
        conn.commit()
        return True, user_id
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def autenticar_utilizador(email, password):
    email = (email or "").strip().lower()
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, name, email, password_hash, plan_type FROM users WHERE LOWER(email) = LOWER(%s)",
                (email,),
            )
            row = cur.fetchone()
        if not row or not row[3]:
            return None
        if not bcrypt.checkpw(password.encode("utf-8"), row[3].encode("utf-8")):
            return None
        return {"id": str(row[0]), "name": row[1], "email": row[2], "plan_type": row[4] or "free"}
    finally:
        conn.close()
