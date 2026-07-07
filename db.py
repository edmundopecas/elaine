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
    _SCHEMA_APLICADO = False   # o DDL do schema só roda 1x por processo

    def _pg():
        """Conexão única, reaberta se cair.

        O search_path vai como OPÇÃO DE CONEXÃO (startup param), não como `SET`:
        estamos atrás do pooler de transação do Supabase (porta 6543), que troca o
        servidor a cada transação — um `SET search_path` roda num servidor e a query
        seguinte cai em outro que voltou ao schema 'public', dando UndefinedTable nas
        tabelas do schema 'elaine'. Como opção de startup, o pooler garante o mesmo
        search_path em todo servidor que ele entregar."""
        global _conn
        if _conn is None or _conn.closed:
            _conn = psycopg2.connect(DATABASE_URL,
                                     options="-c search_path=elaine,public")
            _conn.commit()
        return _conn

    def _tr(sql: str) -> str:
        # % é literal no nosso SQL (LIKE '%x%'); escapa antes de trocar ? -> %s
        return sql.replace("%", "%%").replace("?", "%s")

    # A conexão é persistente e o pooler de transação do Supabase (porta 6543)
    # derruba conexões ociosas: a PRÓXIMA query cai numa conexão morta e estoura
    # InterfaceError('connection already closed') / OperationalError. O `_pg()` só
    # reabre quando `.closed` já está setado — mas o cliente muitas vezes só descobre
    # que morreu ao TENTAR usar. Então: em erro de conexão, descarta a conexão e
    # tenta de novo UMA vez com uma conexão nova (auto-cura, sem tela de erro).
    _CONN_MORTA = (psycopg2.InterfaceError, psycopg2.OperationalError)

    def _rollback_seguro(conn) -> None:
        try:
            conn.rollback()
        except Exception:
            pass                         # conexão já morta: nada a desfazer

    def _descartar_conn(conn) -> None:
        global _conn
        try:
            conn.close()
        except Exception:
            pass
        _conn = None                     # força _pg() a abrir uma nova

    def _rodar(fn):
        """Executa fn(conn); em erro de conexão morta, reabre e tenta 1x mais."""
        ultimo = None
        for tentativa in (1, 2):
            conn = _pg()
            try:
                return fn(conn)
            except Exception as e:
                _rollback_seguro(conn)
                if tentativa == 1 and isinstance(e, _CONN_MORTA):
                    _descartar_conn(conn)
                    ultimo = e
                    continue
                raise
        raise ultimo                     # inalcançável, mas explícito

    def query(sql: str, params: tuple = ()) -> list[dict[str, Any]]:
        def _q(conn):
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(_tr(sql), params)
                rows = [dict(r) for r in cur.fetchall()]
            conn.commit()
            return rows
        return _rodar(_q)

    def query_one(sql: str, params: tuple = ()) -> dict[str, Any] | None:
        rows = query(sql, params)
        return rows[0] if rows else None

    def execute(sql: str, params: tuple = ()) -> int:
        s = sql.lstrip()
        is_insert = s[:6].upper() == "INSERT"

        def _e(conn):
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
        return _rodar(_e)

    def executemany(sql: str, seq: list[tuple]) -> int:
        def _em(conn):
            with conn.cursor() as cur:
                cur.executemany(_tr(sql), seq)
                rc = cur.rowcount
            conn.commit()
            return rc
        return _rodar(_em)

    def init_db() -> None:
        """Cria o schema 'elaine' (idempotente) e semeia se vazio.

        Resiliente: a conexão é persistente (reusada entre reruns no Streamlit
        Cloud). Se uma transação anterior quebrou, ela fica em estado 'aborted' e
        TODA query seguinte dá InFailedSqlTransaction. Aqui: (1) rollback limpa
        qualquer transação travada (auto-cura), e (2) o DDL roda em autocommit —
        cada instrução confirma sozinha, então uma falha não prende a conexão."""
        global _SCHEMA_APLICADO
        conn = _pg()
        conn.rollback()                      # limpa transação pendente/quebrada
        if not _SCHEMA_APLICADO:             # DDL só 1x por processo (não a cada rerun)
            schema = SCHEMA_PG_PATH.read_text(encoding="utf-8")
            prev_ac = conn.autocommit
            conn.autocommit = True           # DDL não fica preso numa transação
            try:
                with conn.cursor() as cur:
                    cur.execute("SET search_path TO elaine, public")
                    cur.execute(schema)
            finally:
                conn.autocommit = prev_ac
            _SCHEMA_APLICADO = True
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


# ─────────────────────────────────────────────────────────────────────────────
# Cache de leitura (Streamlit) — performance
# ─────────────────────────────────────────────────────────────────────────────
# Cada interação re-roda o script inteiro e re-consulta o banco remoto
# (Supabase, sa-east-1): são as idas-e-voltas de rede que geram o lag. Aqui
# cacheamos TODA leitura (query) e LIMPAMOS o cache em QUALQUER escrita
# (execute/executemany) — então navegar/filtrar fica instantâneo, mas depois de
# classificar/importar os dados aparecem frescos na hora. Sem Streamlit (CLIs),
# fica tudo igual ao original.
try:
    import streamlit as _st
    _HAS_ST = True
except Exception:
    _HAS_ST = False

if _HAS_ST:
    _raw_query = query
    _raw_execute = execute
    _raw_executemany = executemany

    @_st.cache_data(ttl=600, show_spinner=False)
    def query(sql: str, params: tuple = ()) -> list[dict[str, Any]]:  # type: ignore[no-redef]
        return _raw_query(sql, params)

    def execute(sql: str, params: tuple = ()) -> int:  # type: ignore[no-redef]
        r = _raw_execute(sql, params)
        _st.cache_data.clear()
        return r

    def executemany(sql: str, seq: list[tuple]) -> int:  # type: ignore[no-redef]
        r = _raw_executemany(sql, seq)
        _st.cache_data.clear()
        return r
