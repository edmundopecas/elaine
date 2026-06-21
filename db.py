"""
Camada de banco — funciona em DOIS modos, com a MESMA interface
(query / query_one / execute / executemany), pras 18 telas não mudarem:

  • SQLite (padrão, dev local): quando NÃO há credencial de Postgres.
  • Postgres/Supabase (produção): quando existe a connection string, lida de
    `ELAINE_DATABASE_URL` (env, usada pelos CLIs) ou de `st.secrets["database_url"]`
    (Streamlit Cloud). As tabelas ficam no schema `elaine`.

Tradução automática do SQL (que é escrito no dialeto SQLite com `?`):
  • `?`  -> `%s`   (placeholder do psycopg2)
  • `%`  -> `%%`   (todo % no texto do SQL é literal — ex. LIKE '%BOLETO%' —, e o
                    psycopg2 trata % como especial quando há parâmetros)
  • execute() de INSERT ganha `RETURNING id` pra devolver o id novo (= lastrowid).
"""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).parent
DB_PATH = BASE_DIR / "elaine.db"
SCHEMA_PATH = BASE_DIR / "schema.sql"
SCHEMA_PG_PATH = BASE_DIR / "schema_pg.sql"


def _database_url() -> str | None:
    """Connection string do Postgres, se configurada (env tem prioridade)."""
    url = os.environ.get("ELAINE_DATABASE_URL")
    if url:
        return url.strip()
    try:
        import streamlit as st
        if "database_url" in st.secrets:
            return str(st.secrets["database_url"]).strip()
    except Exception:
        pass
    return None


DATABASE_URL = _database_url()
IS_PG = bool(DATABASE_URL)


# ─────────────────────────────────────────────────────────────────────────────
# Modo Postgres (Supabase)
# ─────────────────────────────────────────────────────────────────────────────
if IS_PG:
    import psycopg2
    import psycopg2.extras

    _conn = None

    def _pg():
        """Conexão única, reaberta se cair. search_path no schema 'elaine'."""
        global _conn
        if _conn is None or _conn.closed:
            _conn = psycopg2.connect(DATABASE_URL)
            with _conn.cursor() as cur:
                cur.execute("SET search_path TO elaine, public")
            _conn.commit()
        return _conn

    def _tr(sql: str) -> str:
        # % é literal no nosso SQL (LIKE '%x%'); escapa antes de trocar ? -> %s
        return sql.replace("%", "%%").replace("?", "%s")

    def query(sql: str, params: tuple = ()) -> list[dict[str, Any]]:
        conn = _pg()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(_tr(sql), params)
                rows = [dict(r) for r in cur.fetchall()]
            conn.commit()
            return rows
        except Exception:
            conn.rollback()
            raise

    def query_one(sql: str, params: tuple = ()) -> dict[str, Any] | None:
        rows = query(sql, params)
        return rows[0] if rows else None

    def execute(sql: str, params: tuple = ()) -> int:
        conn = _pg()
        s = sql.lstrip()
        is_insert = s[:6].upper() == "INSERT"
        try:
            with conn.cursor() as cur:
                if is_insert and "returning" not in s.lower():
                    cur.execute(_tr(sql) + " RETURNING id", params)
                    new_id = cur.fetchone()[0]
                    conn.commit()
                    return int(new_id)
                cur.execute(_tr(sql), params)
                rc = cur.rowcount
                conn.commit()
                return rc
        except Exception:
            conn.rollback()
            raise

    def executemany(sql: str, seq: list[tuple]) -> int:
        conn = _pg()
        try:
            with conn.cursor() as cur:
                cur.executemany(_tr(sql), seq)
                rc = cur.rowcount
            conn.commit()
            return rc
        except Exception:
            conn.rollback()
            raise

    def init_db() -> None:
        """Cria o schema 'elaine' (idempotente) e semeia se vazio."""
        schema = SCHEMA_PG_PATH.read_text(encoding="utf-8")
        conn = _pg()
        with conn.cursor() as cur:
            cur.execute(schema)
        conn.commit()
        if not query("SELECT 1 FROM plano_contas LIMIT 1"):
            from seed import rodar_seed
            rodar_seed()


# ─────────────────────────────────────────────────────────────────────────────
# Modo SQLite (dev local) — comportamento original
# ─────────────────────────────────────────────────────────────────────────────
else:
    def get_conn() -> sqlite3.Connection:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def query(sql: str, params: tuple = ()) -> list[dict[str, Any]]:
        with get_conn() as conn:
            cur = conn.execute(sql, params)
            return [dict(r) for r in cur.fetchall()]

    def query_one(sql: str, params: tuple = ()) -> dict[str, Any] | None:
        rows = query(sql, params)
        return rows[0] if rows else None

    def execute(sql: str, params: tuple = ()) -> int:
        with get_conn() as conn:
            cur = conn.execute(sql, params)
            conn.commit()
            return cur.lastrowid if cur.lastrowid else cur.rowcount

    def executemany(sql: str, seq: list[tuple]) -> int:
        with get_conn() as conn:
            cur = conn.executemany(sql, seq)
            conn.commit()
            return cur.rowcount

    def init_db() -> None:
        """Cria as tabelas (idempotente) e roda o seed se o banco estiver vazio."""
        schema = SCHEMA_PATH.read_text(encoding="utf-8")
        with get_conn() as conn:
            conn.executescript(schema)
        if not query("SELECT 1 FROM plano_contas LIMIT 1"):
            from seed import rodar_seed
            rodar_seed()
