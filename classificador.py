"""
Motor de classificação que aprende.

A ideia central do projeto: ao importar um extrato, cada movimento é comparado
com as regras já cadastradas (de-para por histórico). Se uma regra casa, o
movimento já entra classificado (empresa/categoria/centro). O que não casar fica
pendente pra você classificar — e ao classificar, você pode salvar uma nova
regra, que passa a valer pras próximas importações.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Any

from db import execute, query, query_one


def _norm(s: str | None) -> str:
    if s is None:
        return ""
    s = unicodedata.normalize("NFKD", s).encode("ASCII", "ignore").decode()
    return re.sub(r"\s+", " ", s).strip().lower()


def empresa_do_grupo_por_cnpj(cnpj: str | None) -> dict[str, Any] | None:
    """
    Dado o CNPJ da contraparte (só dígitos), retorna a empresa do grupo se a
    RAIZ (8 primeiros dígitos) bater — assim matriz e filial são reconhecidas.
    Usado pra marcar transferências internas automaticamente.
    """
    if not cnpj:
        return None
    dig = re.sub(r"\D", "", cnpj)
    if len(dig) < 8:
        return None
    grupo = query("SELECT id, apelido, cnpj FROM empresas WHERE cnpj IS NOT NULL")
    # 1) match exato do CNPJ completo (distingue matriz de filial)
    for e in grupo:
        if re.sub(r"\D", "", e["cnpj"]) == dig:
            return e
    # 2) fallback: mesma raiz (8 primeiros dígitos) — ainda é o mesmo grupo
    raiz = dig[:8]
    for e in grupo:
        if re.sub(r"\D", "", e["cnpj"])[:8] == raiz:
            return e
    return None


def id_categoria_transferencia_interna() -> int | None:
    """ID da categoria 'Transferência entre Empresas' (entra_dre=0)."""
    r = (query_one("SELECT id FROM plano_contas WHERE tipo='transferencia' "
                   "AND nome LIKE '%entre Empresas%' AND ativo=1 LIMIT 1")
         or query_one("SELECT id FROM plano_contas WHERE tipo='transferencia' "
                      "AND entra_dre=0 AND ativo=1 ORDER BY ordem LIMIT 1"))
    return r["id"] if r else None


def regras_ativas() -> list[dict[str, Any]]:
    """Regras ativas, da mais específica (maior prioridade) pra mais genérica."""
    return query(
        "SELECT * FROM regras_classificacao WHERE ativa = 1 "
        "ORDER BY prioridade DESC, length(padrao) DESC, id ASC"
    )


def classificar_movimento(
    historico: str | None, tipo: str | None, empresa_id: int | None,
    regras: list[dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    """
    Devolve a 1ª regra que casa com o movimento, ou None.
    Regra casa quando: padrão está contido no histórico (normalizado),
    o tipo bate (ou a regra serve pra ambos) e a empresa bate (ou é genérica).
    """
    if regras is None:
        regras = regras_ativas()
    h = _norm(historico)
    if not h:
        return None
    for r in regras:
        if r["aplica_tipo"] and tipo and r["aplica_tipo"] != tipo:
            continue
        if r["empresa_id"] and empresa_id and r["empresa_id"] != empresa_id:
            continue
        padrao = _norm(r["padrao"])
        if not padrao:
            continue
        casou = (h == padrao) if r["tipo_match"] == "igual" else (padrao in h)
        if casou:
            return r
    return None


def aplicar_regra_no_lancamento(lanc_id: int, regra: dict[str, Any]) -> None:
    execute(
        "UPDATE lancamentos SET plano_conta_id = ?, centro_custo_id = ?, "
        "regra_id = ?, classificado = 1 WHERE id = ?",
        (regra["plano_conta_id"], regra["centro_custo_id"], regra["id"], lanc_id),
    )
    execute(
        "UPDATE regras_classificacao SET vezes_aplicada = vezes_aplicada + 1 "
        "WHERE id = ?",
        (regra["id"],),
    )


def criar_regra(
    padrao: str, plano_conta_id: int | None, centro_custo_id: int | None,
    empresa_id: int | None = None, aplica_tipo: str | None = None,
    tipo_match: str = "contem", prioridade: int = 0,
) -> int:
    return execute(
        "INSERT INTO regras_classificacao "
        "(padrao, tipo_match, empresa_id, aplica_tipo, plano_conta_id, "
        " centro_custo_id, prioridade) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (padrao.strip(), tipo_match, empresa_id, aplica_tipo,
         plano_conta_id, centro_custo_id, prioridade),
    )


def aprender_regra(
    padrao: str | None, plano_conta_id: int | None,
    aplica_tipo: str | None = None, empresa_id: int | None = None,
    prioridade: int = 3,
) -> int | None:
    """
    Ensina a base: 'esse padrão (fornecedor/histórico) é dessa categoria'.
    Se já existir uma regra com o mesmo padrão e tipo, atualiza a categoria
    (assim remarcar corrige a base); senão cria uma nova. É o que faz o sistema
    'saber do que se trata cada coisa' sem depender de classificação manual toda
    vez — vale pra qualquer banco, pois casa pelo texto do histórico.
    """
    padrao = (padrao or "").strip()
    if not padrao or plano_conta_id is None:
        return None
    existente = query_one(
        "SELECT id FROM regras_classificacao "
        "WHERE lower(padrao)=lower(?) AND COALESCE(aplica_tipo,'')=COALESCE(?,'')",
        (padrao, aplica_tipo),
    )
    if existente:
        execute("UPDATE regras_classificacao SET plano_conta_id=?, ativa=1 WHERE id=?",
                (plano_conta_id, existente["id"]))
        return existente["id"]
    return criar_regra(padrao, plano_conta_id, None, empresa_id, aplica_tipo,
                       "contem", prioridade)


def reclassificar_pendentes() -> int:
    """
    Roda as regras sobre todos os lançamentos ainda não classificados.
    Útil depois de criar regras novas. Retorna quantos foram classificados.
    """
    regras = regras_ativas()
    if not regras:
        return 0
    pendentes = query(
        "SELECT id, descricao, tipo, empresa_id FROM lancamentos "
        "WHERE classificado = 0"
    )
    n = 0
    for lanc in pendentes:
        regra = classificar_movimento(
            lanc["descricao"], lanc["tipo"], lanc["empresa_id"], regras
        )
        if regra:
            aplicar_regra_no_lancamento(lanc["id"], regra)
            n += 1
    return n


def parear_transferencias_proprias() -> int:
    """Acha entrada que é a EMPRESA mandando dinheiro de uma conta dela pra outra
    e tira da DRE (vira 'Transferência entre Empresas').

    Por que existe (17/07/2026): o `empresa_do_grupo_por_cnpj` só marca interna
    quando o CNPJ é de OUTRA empresa do grupo (`interna["id"] != emp_id`). Quando a
    empresa manda pra ELA MESMA (ex.: Supernova tirando R$30k do gateway Asaas e
    jogando na BB dela), o CNPJ é o dela própria, escapa da detecção e cai nas
    regras de-para — a regra 180 ('RECEBI' → Receita de Aluguel) pegou 2 desses e
    inflou a receita em R$60k. O cliente já virou receita quando pagou no Asaas;
    contar de novo na BB é receita dobrada.

    POR QUE NÃO BASTA "CNPJ igual ao da empresa" (medido nos dados, não chutado):
    317 lançamentos têm cnpj_contraparte == CNPJ da própria empresa, mas **304 são
    VENDA DE VERDADE** — PIX de cliente no QR code, onde o banco carimba o CNPJ de
    QUEM RECEBE (a própria loja), não o do pagador (c10: 166 lanç. R$52,7k · c9: 123
    lanç. R$26,1k, média R$213, mínimo R$2,54 · c13 Rosilene: 15). Marcar todo mundo
    como transferência jogaria ~R$82k de receita real pra fora da DRE.

    Por isso exige PROVA DE PAR: entrada com CNPJ da própria empresa **E** uma saída
    de mesmo valor, mesmo dia, na MESMA empresa, em conta DIFERENTE. Testado sobre a
    base inteira: de 315 candidatos pega 8 — 6 já eram transferência (classificadas
    à mão pelo Filipe) e 2 eram os R$30k errados. Zero falso positivo nas 304 vendas.

    NÃO mexe no que o Filipe classificou à mão (regra_id IS NULL e classificado=1) —
    só corrige o que veio de regra ou está pendente. Idempotente: rodar de novo não
    muda nada (o que já é 'transferencia' é ignorado).

    Returns:
        Quantos lançamentos foram reclassificados.
    """
    cat = id_categoria_transferencia_interna()
    if not cat:
        return 0
    candidatos = query(
        "SELECT l.id, l.data, l.valor, l.conta_bancaria_id, l.empresa_id, l.regra_id, "
        "       l.classificado, p.tipo AS plano_tipo "
        "FROM lancamentos l "
        "JOIN contas_bancarias c ON c.id = l.conta_bancaria_id "
        "JOIN empresas e ON e.id = c.empresa_id "
        "LEFT JOIN plano_contas p ON p.id = l.plano_conta_id "
        "WHERE l.tipo = 'entrada' AND l.cnpj_contraparte = e.cnpj"
    )
    n = 0
    for l in candidatos:
        if (l["plano_tipo"] or "") == "transferencia":
            continue                                    # já está fora da DRE
        if l["regra_id"] is None and l["classificado"] == 1:
            continue                                    # classificação manual do Filipe: não tocar
        par = query_one(
            "SELECT id FROM lancamentos WHERE tipo='saida' AND data=? AND valor=? "
            "AND empresa_id=? AND conta_bancaria_id<>? LIMIT 1",
            (l["data"], l["valor"], l["empresa_id"], l["conta_bancaria_id"]),
        )
        if not par:
            continue                                    # sem par = é venda mesmo, não mexe
        execute("UPDATE lancamentos SET plano_conta_id=?, regra_id=NULL, classificado=1 "
                "WHERE id=?", (cat, l["id"]))
        n += 1
    return n
