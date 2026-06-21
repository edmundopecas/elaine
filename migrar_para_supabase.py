"""
Migra os dados do SQLite local (elaine.db) pro Postgres do Supabase, no schema
`elaine`, PRESERVANDO os ids (pras chaves estrangeiras continuarem válidas).

Uso:
    # com a connection string na env:
    ELAINE_DATABASE_URL="postgresql://...:6543/postgres" python migrar_para_supabase.py
    # ou passando direto:
    python migrar_para_supabase.py "postgresql://...:6543/postgres"

Por padrão é PREVIEW (só conta). Pra gravar de verdade: adicione --commit.
Idempotente com --reset: zera as tabelas do schema elaine antes de copiar.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import psycopg2
import psycopg2.extras

BASE_DIR = Path(__file__).parent
SQLITE_PATH = BASE_DIR / "elaine.db"
SCHEMA_PG = BASE_DIR / "schema_pg.sql"

# Ordem que respeita as FKs (pais antes dos filhos)
ORDEM = [
    "empresas", "centros_custo", "plano_contas", "contas_bancarias",
    "regras_classificacao", "importacoes", "lancamentos", "titulos",
]


def _cols(scur, tabela: str) -> list[str]:
    scur.execute(f"PRAGMA table_info({tabela})")
    return [r[1] for r in scur.fetchall()]


def main(url: str, commit: bool, reset: bool) -> None:
    sconn = sqlite3.connect(SQLITE_PATH)
    sconn.row_factory = sqlite3.Row
    scur = sconn.cursor()

    pconn = psycopg2.connect(url)
    pcur = pconn.cursor()
    pcur.execute("SET search_path TO elaine, public")

    # 1) cria schema/tabelas
    print("Criando schema 'elaine' (idempotente)…")
    pcur.execute(SCHEMA_PG.read_text(encoding="utf-8"))
    pconn.commit()

    if reset:
        print("Limpando tabelas do schema elaine (--reset)…")
        pcur.execute("TRUNCATE %s RESTART IDENTITY CASCADE"
                     % ", ".join(f"elaine.{t}" for t in ORDEM))
        pconn.commit()

    # 2) copia tabela a tabela, preservando id
    total = 0
    for tabela in ORDEM:
        cols = _cols(scur, tabela)
        rows = scur.execute(f"SELECT {', '.join(cols)} FROM {tabela}").fetchall()
        n_pg = pcur.execute(f"SELECT COUNT(*) FROM elaine.{tabela}") or 0
        ja = pcur.fetchone()[0]
        print(f"  {tabela:22} SQLite={len(rows):>5}  Postgres(antes)={ja:>5}", end="")
        if not commit:
            print("   [preview]")
            continue
        if rows:
            collist = ", ".join(cols)
            sql = f"INSERT INTO elaine.{tabela} ({collist}) VALUES %s"
            valores = [tuple(r[c] for c in cols) for r in rows]
            psycopg2.extras.execute_values(pcur, sql, valores, page_size=500)
        # acerta a sequence do id pro MAX(id) atual
        pcur.execute(
            "SELECT setval(pg_get_serial_sequence(%s, 'id'), "
            "GREATEST((SELECT COALESCE(MAX(id),1) FROM elaine.%s), 1), true)"
            % ("%s", tabela), (f"elaine.{tabela}",))
        pconn.commit()
        total += len(rows)
        print(f"   -> inseridos {len(rows)}")

    # 3) confere contagens
    if commit:
        print("\nConferência (Postgres):")
        for tabela in ORDEM:
            pcur.execute(f"SELECT COUNT(*) FROM elaine.{tabela}")
            print(f"  {tabela:22} {pcur.fetchone()[0]:>6}")
        print(f"\nTotal de linhas migradas: {total}")
    else:
        print("\n[PREVIEW] Nada gravado. Rode com --commit (e --reset se quiser zerar antes).")

    sconn.close()
    pconn.close()


def _resolver_url(args: list[str]) -> str | None:
    """URL do Postgres: arg > env > .streamlit/secrets.toml (database_url)."""
    import os
    if args:
        return args[0]
    if os.environ.get("ELAINE_DATABASE_URL"):
        return os.environ["ELAINE_DATABASE_URL"].strip()
    sec = BASE_DIR / ".streamlit" / "secrets.toml"
    if sec.exists():
        try:
            import tomllib
            with open(sec, "rb") as f:
                data = tomllib.load(f)
            if data.get("database_url"):
                return str(data["database_url"]).strip()
        except Exception:
            pass
    return None


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    url = _resolver_url(args)
    if not url:
        sys.exit("Faltou a connection string (arg, ELAINE_DATABASE_URL ou "
                 ".streamlit/secrets.toml).")
    main(url, commit="--commit" in sys.argv, reset="--reset" in sys.argv)
