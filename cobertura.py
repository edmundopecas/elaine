# -*- coding: utf-8 -*-
"""Até que dia cada conta está CONFERIDA num mês — mesmo sem lançamento no fim.

Por que existe (06/10/2026, pedido do Filipe): a janela automática da Saúde
Financeira cortava no ÚLTIMO LANÇAMENTO de cada conta. Conta que simplesmente não
mexeu nos últimos dias (Asaas parou em 25/09, Santander Filial em 28/09, BB Filial
em 29/09) segurava a tela inteira no dia 25 — "esses bancos não tiveram
movimentação nesses outros dias". Aqui fica registrado "a conta X está completa
até o dia D no mês M"; a tela usa o MAIOR entre isso e o último lançamento.
"""
from __future__ import annotations

from db import IS_PG, execute, query

_DDL = """
CREATE TABLE IF NOT EXISTS conta_cobertura (
    id                {pk},
    conta_bancaria_id INTEGER NOT NULL REFERENCES contas_bancarias(id),
    mes               TEXT NOT NULL,
    conferido_ate     TEXT NOT NULL,
    criado_em         TEXT NOT NULL DEFAULT ({agora}),
    UNIQUE (conta_bancaria_id, mes)
)"""

_TABELA_OK = False


def _garantir_tabela() -> None:
    global _TABELA_OK
    if _TABELA_OK:
        return
    execute(_DDL.format(pk="SERIAL PRIMARY KEY" if IS_PG else "INTEGER PRIMARY KEY AUTOINCREMENT",
                        agora="now()::text" if IS_PG else "datetime('now')"))
    _TABELA_OK = True


def conferido_ate(mes: str) -> dict[int, str]:
    """{conta_bancaria_id: 'AAAA-MM-DD'} das contas marcadas como completas no mês."""
    _garantir_tabela()
    return {r["conta_bancaria_id"]: r["conferido_ate"]
            for r in query("SELECT conta_bancaria_id, conferido_ate FROM conta_cobertura WHERE mes=?", (mes,))}


def marcar(conta_id: int, mes: str, ate: str) -> None:
    """Marca a conta como completa no mês até `ate` (sobrescreve a marca anterior)."""
    _garantir_tabela()
    execute("INSERT INTO conta_cobertura (conta_bancaria_id, mes, conferido_ate) VALUES (?,?,?) "
            "ON CONFLICT (conta_bancaria_id, mes) DO UPDATE SET conferido_ate=excluded.conferido_ate",
            (conta_id, mes, ate))
