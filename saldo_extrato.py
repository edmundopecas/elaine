"""Checagem de EMENDA DE SALDO do extrato — pega movimento que o banco lançou
DEPOIS que o arquivo foi gerado.

Motivo (23/07/2026): faltavam R$ 287.709,00 de saídas de julho na Matriz do Safra.
Não era parser nem dedup — os `.xls` tinham sido gerados no fim da noite do próprio
dia (19h-23h) e o banco processa depois (liquidação de empréstimo, IOF, seguro,
transferência interna). Cada arquivo fechava certinho em si mesmo e batia com o app,
então nada acusava. O que denuncia é a EMENDA: o saldo com que o dia seguinte ABRE
não é o saldo com que o anterior FECHOU.

O extrato do Safra traz duas linhas especiais na tabela de movimentos:

    SALDO INICIAL   ⚠️ NÃO é a abertura do período — é o saldo NA DATA EM QUE O
                    ARQUIVO FOI GERADO (o "saldo de hoje")
    SALDO TOTAL     saldo depois de TODOS os movimentos do arquivo, inclusive os
                    datados no futuro

⚠️ CORREÇÃO DE 26/07/2026 — a leitura original ("SALDO INICIAL = abertura") estava
ERRADA e por isso a trava deixou passar os furos dos dias 21 e 22 da Matriz, além de
dar alerta falso todo fim de semana. Prova nas 2 contas, ao centavo:

    saldo_total − (movimentos datados no próximo dia útil) == saldo_inicial

O banco não data movimento em sábado/domingo: joga tudo pra segunda. Então um extrato
tirado no domingo traz linhas datadas na segunda, o SALDO INICIAL é o saldo de hoje
(sem elas) e o SALDO TOTAL é o saldo projetado (com elas) — por isso
`saldo_inicial + créditos − débitos == saldo_total` NÃO fecha por construção.

Daí as conferências certas:

    INTERNA   existe um dia D no arquivo tal que
              saldo_total − (líquido dos movimentos posteriores a D) == saldo_inicial
              (com D = último dia, vira a fórmula antiga; se nenhum D explica, aí sim
              tem linha não lida)

    EMENDA    (saldo_total DESTE − líquido de TODOS os movimentos deste)
              == saldo_total do extrato anterior
              O lado esquerdo é a abertura REAL do período, derivada — não depende do
              rótulo "SALDO INICIAL". A diferença é o que o banco lançou depois que o
              arquivo anterior foi gerado.

Pra emenda funcionar é preciso lembrar o saldo de cada extrato já visto — é o que a
tabela `saldos_extrato` guarda. Gravar é idempotente: reimportar o mesmo arquivo não
cria linha nova.
"""
from __future__ import annotations

import io
import re
from typing import Any

from db import IS_PG, execute, query_one

_RE_PERIODO = re.compile(r"Per[^<]{0,4}odo de (\d{2}/\d{2}/\d{4}) a (\d{2}/\d{2}/\d{4})")
_TOL = 0.005          # meio centavo

_DDL_PG = """
CREATE TABLE IF NOT EXISTS saldos_extrato (
    id                SERIAL PRIMARY KEY,
    conta_bancaria_id INTEGER REFERENCES contas_bancarias(id),
    periodo_ini       TEXT NOT NULL,
    periodo_fim       TEXT NOT NULL,
    saldo_inicial     DOUBLE PRECISION,
    saldo_total       DOUBLE PRECISION,
    arquivo           TEXT,
    criado_em         TEXT NOT NULL DEFAULT (now()::text)
)"""

_DDL_SQLITE = """
CREATE TABLE IF NOT EXISTS saldos_extrato (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    conta_bancaria_id INTEGER REFERENCES contas_bancarias(id),
    periodo_ini       TEXT NOT NULL,
    periodo_fim       TEXT NOT NULL,
    saldo_inicial     REAL,
    saldo_total       REAL,
    arquivo           TEXT,
    criado_em         TEXT NOT NULL DEFAULT (datetime('now'))
)"""

_TABELA_OK = False


def _garantir_tabela() -> None:
    """Cria a tabela se ainda não existe (1x por processo).

    O schema.sql/schema_pg.sql também a declara — isto aqui é pros CLIs, que não
    passam pelo init_db() do app.
    """
    global _TABELA_OK
    if _TABELA_OK:
        return
    execute(_DDL_PG if IS_PG else _DDL_SQLITE)
    _TABELA_OK = True


def _iso(d: str) -> str:
    """'22/07/2026' -> '2026-07-22'."""
    return f"{d[6:]}-{d[3:5]}-{d[0:2]}"


def ler_saldos(file_bytes: bytes, nome_arquivo: str = "") -> dict[str, Any] | None:
    """Lê período + SALDO INICIAL + SALDO TOTAL do .xls do Safra.

    Devolve None quando o arquivo não é do Safra ou não traz as linhas de saldo —
    o export do mês inteiro, por exemplo, vem sem elas (aí só dá pra conferir o
    movimento, não o saldo).
    """
    from parsers import _eh_html_disfarcado, _norm_xls, _num_br

    if not _eh_html_disfarcado(file_bytes):
        return None
    texto = file_bytes.decode("latin-1", "ignore")
    m = _RE_PERIODO.search(texto)
    if not m:
        return None

    import pandas as pd
    try:
        tabs = pd.read_html(io.BytesIO(file_bytes))
    except ValueError:
        return None

    s_ini = s_tot = None
    for t in tabs:
        header = None
        for i in range(min(6, len(t))):
            cels = [_norm_xls(x) for x in t.iloc[i].tolist()]
            if any(c == "data" for c in cels) and any("lancamento" in c for c in cels):
                header = (i, cels)
                break
        if not header:
            continue
        hi, cels = header
        j_lanc = next((j for j, h in enumerate(cels)
                       if "lancamento" in h and "tipo" not in h), None)
        j_saldo = next((j for j, h in enumerate(cels) if h.startswith("saldo")), None)
        if j_lanc is None or j_saldo is None:
            continue
        for r in range(hi + 1, len(t)):
            row = t.iloc[r].tolist()
            lanc = _norm_xls(row[j_lanc])
            if not lanc.startswith("saldo"):
                continue
            v = _num_br(row[j_saldo])       # o texto já traz o sinal ('-R$ 1.234,56')
            if v is None:
                continue
            if "inicial" in lanc:
                s_ini = v
            elif "total" in lanc:
                s_tot = v
    if s_ini is None and s_tot is None:
        return None
    return {"periodo_ini": _iso(m.group(1)), "periodo_fim": _iso(m.group(2)),
            "saldo_inicial": s_ini, "saldo_total": s_tot, "arquivo": nome_arquivo}


def _liquido(movimentos: list[dict], depois_de: str | None = None) -> float:
    """Créditos - débitos. Com `depois_de`, só os movimentos de dia POSTERIOR a ele."""
    tot = 0.0
    for m in movimentos:
        d = m["data"]
        d = d.isoformat() if hasattr(d, "isoformat") else str(d)
        if depois_de is not None and d <= depois_de:
            continue
        tot += m["valor"] if m["tipo"] == "entrada" else -m["valor"]
    return tot


def abertura_derivada(saldos: dict[str, Any], movimentos: list[dict]) -> float | None:
    """Saldo com que o período REALMENTE abriu = saldo_total - líquido do arquivo.

    Derivado do SALDO TOTAL de propósito: o rótulo "SALDO INICIAL" do Safra é o saldo
    da data de geração, não a abertura (ver docstring do módulo).
    """
    if saldos.get("saldo_total") is None:
        return None
    return round(saldos["saldo_total"] - _liquido(movimentos), 2)


def conferir(conta_id: int, saldos: dict[str, Any] | None,
             movimentos: list[dict]) -> list[str]:
    """Devolve a lista de alertas (vazia = tudo certo).

    `movimentos` tem que ser o parse CRU do arquivo — antes de consolidar DDA ou
    qualquer outro tratamento, senão o total não corresponde ao saldo.
    """
    if not saldos:
        return []
    _garantir_tabela()
    alertas: list[str] = []
    s_ini, s_tot = saldos["saldo_inicial"], saldos["saldo_total"]
    ini = saldos["periodo_ini"]

    # ---------------------------------------------------------------- INTERNA
    # O arquivo é consistente se existe um dia D tal que o SALDO INICIAL é o saldo
    # até D (D = data de geração; o que vem depois são os lançamentos que o banco
    # datou no próximo dia útil). Com D = último dia do arquivo isso vira a
    # conferência clássica `inicial + créditos - débitos == total`.
    if s_ini is not None and s_tot is not None:
        dias = sorted({(m["data"].isoformat() if hasattr(m["data"], "isoformat")
                        else str(m["data"])) for m in movimentos})
        explicado = any(
            abs((s_tot - _liquido(movimentos, depois_de=d)) - s_ini) <= _TOL
            for d in ([None] + dias))
        if not explicado:
            alertas.append(
                f"O arquivo não fecha consigo mesmo: com saldo inicial {s_ini:,.2f} e "
                f"saldo total {s_tot:,.2f}, nenhum corte de data explica a diferença "
                f"(líquido lido: {_liquido(movimentos):,.2f}). Possível linha não lida "
                f"— não importe sem conferir.")

    # ----------------------------------------------------------------- EMENDA
    abertura = abertura_derivada(saldos, movimentos)
    if abertura is None:
        return alertas

    ant = query_one(
        "SELECT periodo_ini, periodo_fim, saldo_total, arquivo FROM saldos_extrato "
        "WHERE conta_bancaria_id=? AND periodo_fim <= ? AND saldo_total IS NOT NULL "
        "ORDER BY periodo_fim DESC, id DESC LIMIT 1", (conta_id, ini))
    if not ant:
        return alertas

    # Períodos que se tocam (o anterior termina no dia em que este começa) têm o
    # mesmo dia dos dois lados — o saldo do anterior já inclui movimento deste dia,
    # então a emenda não é comparável. NUNCA dar ✅ nesse caso: dizer que não deu.
    if str(ant["periodo_fim"]) >= ini:
        alertas.append(
            f"NÃO DEU PRA CONFERIR A EMENDA: o extrato anterior vai até "
            f"{ant['periodo_fim']} e este começa em {ini} — os períodos se sobrepõem, "
            f"e o saldo do anterior já inclui movimento deste dia. Isso não é um erro, "
            f"mas também não é garantia de que não falta nada.")
        return alertas

    dif = round(abertura - float(ant["saldo_total"]), 2)
    if abs(dif) > _TOL:
        alertas.append(
            f"BURACO DE EXTRATO: o extrato anterior (até {ant['periodo_fim']}) fechou "
            f"em R$ {float(ant['saldo_total']):,.2f}, mas este período abre em "
            f"R$ {abertura:,.2f} — faltam R$ {dif:,.2f} de movimento entre "
            f"{ant['periodo_fim']} e {ini}. Quase sempre é o banco tendo lançado DEPOIS "
            f"que aquele arquivo foi gerado. Baixe de novo o extrato cobrindo "
            f"{ant['periodo_fim']} a {ini} e importe por cima (o dedup não deixa "
            f"duplicar).")
    return alertas


def tem_ancora(conta_id: int, periodo_ini: str) -> bool:
    """True se existe extrato anterior COMPARÁVEL (fecha antes deste começar).

    A tela usa isto pra não dizer "✅ emenda com o anterior" quando não houve
    anterior nenhum — ✅ que não foi verificado quebra a confiança (mesma lição
    do "está no CPR"). Sem âncora, o certo é dizer que não deu pra conferir.
    """
    _garantir_tabela()
    return query_one(
        "SELECT 1 AS ok FROM saldos_extrato WHERE conta_bancaria_id=? AND periodo_fim < ? "
        "AND saldo_total IS NOT NULL LIMIT 1", (conta_id, periodo_ini)) is not None


def registrar(conta_id: int, saldos: dict[str, Any] | None) -> bool:
    """Guarda o saldo deste extrato pra emenda da próxima vez. Idempotente."""
    if not saldos:
        return False
    _garantir_tabela()
    ja = query_one(
        "SELECT id FROM saldos_extrato WHERE conta_bancaria_id=? AND periodo_ini=? "
        "AND periodo_fim=? AND COALESCE(saldo_inicial,-1e18)=COALESCE(?,-1e18) "
        "AND COALESCE(saldo_total,-1e18)=COALESCE(?,-1e18)",
        (conta_id, saldos["periodo_ini"], saldos["periodo_fim"],
         saldos["saldo_inicial"], saldos["saldo_total"]))
    if ja:
        return False
    execute(
        "INSERT INTO saldos_extrato (conta_bancaria_id, periodo_ini, periodo_fim, "
        "saldo_inicial, saldo_total, arquivo) VALUES (?,?,?,?,?,?)",
        (conta_id, saldos["periodo_ini"], saldos["periodo_fim"],
         saldos["saldo_inicial"], saldos["saldo_total"], saldos.get("arquivo")))
    return True
