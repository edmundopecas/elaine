"""Dedup robusto de movimentos na importação.

PROBLEMA que isto resolve: o índice UNIQUE em `lancamentos.linha_hash` (e o dedup
antigo que checava só "existe esse hash?") COLAPSAVA linhas legitimamente idênticas
no mesmo dia — ex.: 3 parcelas de consórcio de R$1.445,40 viravam 1 só. Toda parcela
repetida real era perdida na importação.

SOLUÇÃO: deduplicar por MULTISET de conteúdo (data, valor, tipo, documento,
histórico[:100]) — a Nª ocorrência de uma linha idêntica no extrato só é considerada
duplicada se o banco já tem N ocorrências daquele mesmo conteúdo. As ocorrências extras
são inseridas com um `linha_hash` sufixado (`hash#2`, `hash#3`, ...) só pra respeitar o
índice UNIQUE — o dedup em si não depende mais do hash ser único.

Robusto a re-importação: como a comparação é por contagem de conteúdo (e não pela
string do hash), os sufixos `#2/#3` não atrapalham — uma reimportação do mesmo extrato
volta a casar as 3 parcelas pela contagem e não duplica nada.
"""
from __future__ import annotations

from collections import Counter
from typing import Any


def _content_key(data_iso: str, valor: Any, tipo: str | None,
                 documento: str | None = None, descricao: str | None = None) -> tuple:
    """Chave de dedup de um movimento: (data, valor, tipo) — só isso.

    Deduplica por MULTIPLICIDADE desta chave: a Nª ocorrência de (data, valor, tipo) no
    extrato só é duplicada se o banco já tem N daquela mesma trinca. Assim 3 cotas de
    consórcio iguais (3.4.01, R$1.445,40, saída no mesmo dia) = contagem 3, todas mantidas
    — conserta o colapso causado pelo índice UNIQUE em linha_hash.

    NÃO inclui documento nem histórico DE PROPÓSITO: ambos variam entre exports do mesmo
    extrato (o Safra exporta o mesmo PIX ora com doc real ora com doc '0'; o texto do
    histórico mudou quando o parser evoluiu). Se a chave dependesse deles, reimportar o
    mesmo período por outro export/depois de mexer no parser geraria duplicado falso.
    `documento`/`descricao` são aceitos por compatibilidade de chamada e ignorados.
    """
    return (
        data_iso or "",
        round(float(valor or 0), 2),
        tipo or "",
    )


def planejar_insercao(movimentos: list[dict], existentes: list[dict]) -> tuple[list[tuple[dict, str]], int]:
    """Decide o que inserir, deduplicando por multiset de conteúdo.

    Args:
        movimentos: parseados do extrato (cada um com data [date], valor, tipo,
            documento, historico, linha_hash).
        existentes: linhas já no banco daquela conta — dicts com data, valor, tipo,
            documento, descricao, linha_hash.

    Returns:
        (a_inserir, duplicados) onde a_inserir é uma lista de (movimento, linha_hash_único)
        pronta pra INSERT, e duplicados é a contagem de ocorrências já presentes no banco.
    """
    db_counts = Counter(
        _content_key(r["data"], r["valor"], r["tipo"], r.get("documento"), r.get("descricao"))
        for r in existentes
    )
    usados = {r["linha_hash"] for r in existentes if r.get("linha_hash")}

    vistos: Counter = Counter()
    a_inserir: list[tuple[dict, str]] = []
    duplicados = 0

    for m in sorted(movimentos, key=lambda x: x["data"]):
        ck = _content_key(m["data"].isoformat(), m["valor"], m["tipo"],
                          m.get("documento"), m.get("historico"))
        vistos[ck] += 1
        if vistos[ck] <= db_counts.get(ck, 0):
            duplicados += 1            # essa ocorrência já está no banco
            continue
        # ocorrência nova → garante linha_hash único (sufixo só p/ o índice UNIQUE)
        base = m["linha_hash"]
        h = base
        k = 1
        while h in usados:
            k += 1
            h = f"{base}#{k}"
        usados.add(h)
        a_inserir.append((m, h))

    return a_inserir, duplicados
