"""
Baixas do Contas a Pagar — **um título pode ser quitado por VÁRIOS pagamentos**
(e um pagamento pode quitar vários títulos).

Por que existe (04/08/2026, pedido do Filipe): "paga ICMS e FECOEP às vezes em
guias juntas e às vezes separadas; queria pegar os dois pagamentos e ligar com 1
do que está lançado no financeiro". Medido na base de julho, é exatamente isso:

    título #1049 ENERGEX «8100 ICMS»   R$ 21.319,92 (venc 27/07)
        = 24/07  R$ 20.154,32  SISPAG TRIBUTOS GNRE-AL   (o ICMS)
        + 24/07  R$  1.165,60  SISPAG TRIBUTOS GNRE-AL   (o FECOEP, ~5,8%)

    título #1169 W1 «15928 ICMS» R$ 1.261,58 = 1.192,89 + 68,69 (27/07)
    título  #964 CAMBUCI R$ 50,32 = 47,97 + 2,35 · #959 PADRE CICERO R$ 28,56 = 27,23 + 1,33

O vínculo mora na tabela `titulo_baixas` (N↔N). **`titulos.lancamento_id` continua
preenchido** com o pagamento PRINCIPAL (o de maior valor) e `status`/`data_baixa`
seguem sendo mantidos — assim toda tela/relatório que já lia a coluna antiga
continua certo, sem saber que o título foi pago em duas guias.

Regra de ouro da conferência (vale desde 07/07): **nada é ligado sozinho**. Este
módulo só grava o que o Filipe mandou gravar na tela.
"""
from __future__ import annotations

from db import execute, executemany, query


# ─── Leitura ─────────────────────────────────────────────────────────────────
def por_titulo(titulo_ids: list[int]) -> dict[int, list[int]]:
    """{titulo_id: [lancamento_id, ...]} — inclui a baixa antiga (`lancamento_id`)
    mesmo que o backfill ainda não tenha rodado, pra tela nunca "perder" uma baixa."""
    if not titulo_ids:
        return {}
    ph = ",".join(["?"] * len(titulo_ids))
    mapa: dict[int, list[int]] = {}
    for r in query(f"SELECT titulo_id, lancamento_id FROM titulo_baixas "
                   f"WHERE titulo_id IN ({ph}) ORDER BY id", tuple(titulo_ids)):
        mapa.setdefault(r["titulo_id"], []).append(r["lancamento_id"])
    for r in query(f"SELECT id, lancamento_id FROM titulos "
                   f"WHERE id IN ({ph}) AND lancamento_id IS NOT NULL", tuple(titulo_ids)):
        atual = mapa.setdefault(r["id"], [])
        if r["lancamento_id"] not in atual:
            atual.append(r["lancamento_id"])
    return mapa


def titulos_por_lancamento() -> dict[int, list[int]]:
    """{lancamento_id: [titulo_id, ...]} de TUDO que já tem baixa — é o que diz se um
    pagamento já foi usado (e por quem), inclusive quando quita mais de um título."""
    mapa: dict[int, list[int]] = {}
    for r in query("SELECT titulo_id, lancamento_id FROM titulo_baixas"):
        mapa.setdefault(r["lancamento_id"], []).append(r["titulo_id"])
    for r in query("SELECT id, lancamento_id FROM titulos WHERE lancamento_id IS NOT NULL"):
        atual = mapa.setdefault(r["lancamento_id"], [])
        if r["id"] not in atual:
            atual.append(r["id"])
    return mapa


# ─── Escrita ─────────────────────────────────────────────────────────────────
def ligar(titulo_id: int, lancamentos: list[dict]) -> None:
    """Faz do conjunto `lancamentos` a baixa DO título (substitui o que havia).

    `lancamentos` são dicts com pelo menos id, valor e data. O de MAIOR valor vira o
    `titulos.lancamento_id` (o principal, o que as telas antigas mostram) e a
    `data_baixa` é a data do ÚLTIMO pagamento — a data em que o título ficou quitado.
    """
    if not lancamentos:
        desligar(titulo_id)
        return
    execute("DELETE FROM titulo_baixas WHERE titulo_id=?", (titulo_id,))
    executemany("INSERT INTO titulo_baixas (titulo_id, lancamento_id) VALUES (?,?)",
                [(titulo_id, int(l["id"])) for l in lancamentos])
    principal = max(lancamentos, key=lambda l: float(l["valor"]))
    quitado_em = max(str(l["data"])[:10] for l in lancamentos)
    execute("UPDATE titulos SET lancamento_id=?, status='pago', data_baixa=? WHERE id=?",
            (int(principal["id"]), quitado_em, titulo_id))


def desligar(titulo_id: int) -> None:
    """Desfaz a baixa inteira do título (todos os pagamentos)."""
    execute("DELETE FROM titulo_baixas WHERE titulo_id=?", (titulo_id,))
    execute("UPDATE titulos SET lancamento_id=NULL, status='aberto', data_baixa=NULL "
            "WHERE id=?", (titulo_id,))


def backfill() -> int:
    """Copia pra `titulo_baixas` as baixas antigas que só existem em
    `titulos.lancamento_id`. Idempotente (roda quantas vezes quiser)."""
    faltando = query("""SELECT t.id titulo_id, t.lancamento_id
                        FROM titulos t
                        WHERE t.lancamento_id IS NOT NULL
                          AND NOT EXISTS (SELECT 1 FROM titulo_baixas b
                                          WHERE b.titulo_id=t.id
                                            AND b.lancamento_id=t.lancamento_id)""")
    if faltando:
        executemany("INSERT INTO titulo_baixas (titulo_id, lancamento_id) VALUES (?,?)",
                    [(r["titulo_id"], r["lancamento_id"]) for r in faltando])
    return len(faltando)
