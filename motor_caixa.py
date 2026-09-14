"""
Motor único da ponte "entrou → saiu → o que ficou no caixa".

Nasceu em 14/09/2026 porque o ⚖️ Comparativo e a 💊 Saúde Financeira davam
números diferentes para a MESMA janela (145.056,79 x 142.300,19): cada tela somava
um recorte próprio (uma contava rendimento de aplicação e receita eventual, a outra
não; uma juntava consórcio na linha de dívida, a outra só a parcela de banco).
Filipe: "alinha as duas telas, e eu quero ver tudo, o entrou e o saiu, para tomar
a decisão correta". Agora as duas telas chamam `ponte()` e não podem divergir.

Regras (as mesmas que valiam no Comparativo, que era a tela mais completa):
  * ENTROU = toda entrada cuja categoria entra na DRE (entra_dre=1).
    SAIU    = toda saída cuja categoria entra na DRE.
    RESULTADO = entrou − saiu.  Os baldes abaixo (vendas, aluguel, outras receitas,
    compras, pessoal, ...) são só a abertura desse mesmo número: uma entrada lançada
    numa categoria de despesa (estorno) abate o balde da despesa — nada some.
  * Fora do resultado, mas SAIU do caixa: sócios (pró-labore + Pessoal-*),
    parcelas de empréstimo (#39), consórcio/saque/valores a recuperar (#34,#37,#47 e
    o que mais for entra_dre=0 sem ser 31/32/33).
  * NÃO aparecem como entrou/saiu (só dinheiro trocando de bolso):
      - aplicação/resgate (#33): o banco varre a conta todo dia. Só o LÍQUIDO da
        janela entra, e só na conciliação com o caixa observado;
      - transferência entre contas do grupo (#31): sai de uma, entra na outra.
        Só o que NÃO ACHOU O OUTRO LADO (mesma quantia, mesmo dia ou dia seguinte)
        aparece — é sinal de conta não importada, e o Filipe quer ver isso;
      - aporte de sócio (#32).
  * Pendentes (sem categoria) aparecem numa linha própria: número que esconde
    pendente é número mentiroso.
"""
from __future__ import annotations

import unicodedata

import pandas as pd

from db import query

# categorias por bloco (ids de plano_contas)
RECEITA_VENDAS = (1, 2)
RECEITA_ALUGUEL = (67,)
FINANCEIRAS = (26, 27, 28, 35)
EMPRESTIMOS = 39
APLICACAO = 33
TRANSF_GRUPO = 31
APORTE_SOCIO = 32
GRUPOS_DESPESA = {
    "custos": ("Custos",),
    "pessoal": ("Despesas com Pessoal",),
    "estrutura": ("Despesas Administrativas", "Ocupação", "Despesas Comerciais", "Construção"),
    "tributos": ("Tributos", "Deduções"),
}
GRUPOS_SOCIOS = ("Sócios", "Gastos Pessoais (Sócios)")

ORDEM = [
    ("Receita de vendas e serviços", "receita_vendas"),
    ("Receita de aluguel", "aluguel"),
    ("Outras receitas (rendimentos, eventuais)", "outras_rec"),
    ("(−) Compras", "custos"),
    ("(−) Pessoal", "pessoal"),
    ("(−) Estrutura (admin, ocupação, comercial)", "estrutura"),
    ("(−) Tributos e deduções", "tributos"),
    ("(−) Tarifas, juros e IOF", "financeiras"),
    ("(−) Outras despesas", "outras_desp"),
    ("= RESULTADO DA OPERAÇÃO", "resultado"),
    ("(−) Sócios (pró-labore + pessoais)", "socios"),
    ("= DEPOIS DOS SÓCIOS", "apos_socios"),
    ("(−) Parcelas de empréstimo", "emprestimos"),
    ("(−) Consórcio / saque / valores a recuperar", "consorcio_outros"),
    ("(±) Lançamentos ainda sem categoria (pendentes)", "pend"),
    ("(±) Transferências sem o outro lado (conta não importada)", "transf"),
    ("= SOBRA / FALTA (caixa da operação)", "sobra"),
]
# linhas que são subtotal (pra tela destacar)
SUBTOTAIS = {"resultado", "apos_socios", "sobra"}


def _signed(v: float, tipo: str) -> float:
    """Efeito no caixa: entrada soma, saída subtrai."""
    return float(v) if tipo == "entrada" else -float(v)


def ponte(d_ini: str, d_fim: str, emp_id: int | None = None) -> dict:
    """Todos os números da ponte para a janela [d_ini, d_fim] (ISO), grupo ou empresa.

    Retorna um dict com uma chave por bloco (ver ORDEM) — todos ASSINADOS pelo efeito
    no caixa (receita positiva, despesa negativa) — mais `entrou`, `saiu`,
    `aplic_liq`, `aporte`, `caixa_calc` e `nao_fecha` (de quem é a transferência sem
    o outro lado).
    """
    emp_sql = " AND l.empresa_id=?" if emp_id else ""
    par = [d_ini, d_fim] + ([emp_id] if emp_id else [])

    rows = query(
        """SELECT l.plano_conta_id pid, p.grupo, p.entra_dre, p.nome, l.tipo,
                  COALESCE(SUM(l.valor),0) v
           FROM lancamentos l LEFT JOIN plano_contas p ON p.id=l.plano_conta_id
           WHERE l.data BETWEEN ? AND ?""" + emp_sql + """
           GROUP BY l.plano_conta_id, p.grupo, p.entra_dre, p.nome, l.tipo""", tuple(par))

    r = {k: 0.0 for _, k in ORDEM}
    r.update(entrou=0.0, saiu=0.0, aplic_liq=0.0, aporte=0.0)

    for x in rows:
        pid, grupo, dre, tipo, v = x["pid"], x["grupo"], x["entra_dre"], x["tipo"], float(x["v"])
        s = _signed(v, tipo)
        if pid is None:
            r["pend"] += s
            continue
        if dre == 1:
            r["entrou" if tipo == "entrada" else "saiu"] += v
            if pid in RECEITA_VENDAS:
                r["receita_vendas"] += s
            elif pid in RECEITA_ALUGUEL:
                r["aluguel"] += s
            elif pid in FINANCEIRAS:
                r["financeiras"] += s
            else:
                for chave, grupos in GRUPOS_DESPESA.items():
                    if grupo in grupos:
                        r[chave] += s
                        break
                else:
                    # receita que não é venda/aluguel (Outras Receitas, Rendimentos)
                    # ou despesa de grupo novo que ninguém mapeou — nada se perde
                    r["outras_rec" if (grupo or "").startswith("Receita") else "outras_desp"] += s
            continue
        # fora da DRE
        if pid == APLICACAO:
            r["aplic_liq"] += s
        elif pid == APORTE_SOCIO:
            r["aporte"] += s
        elif pid == TRANSF_GRUPO:
            pass                      # tratado abaixo, só o que não fecha
        elif pid == EMPRESTIMOS:
            r["emprestimos"] += s
        elif grupo in GRUPOS_SOCIOS:
            r["socios"] += s
        else:
            r["consorcio_outros"] += s

    r["resultado"] = r["entrou"] - r["saiu"]
    r["apos_socios"] = r["resultado"] + r["socios"]
    r["transf"], r["nao_fecha"] = transferencias_sem_outro_lado(d_ini, d_fim, emp_id)
    r["sobra"] = (r["apos_socios"] + r["emprestimos"] + r["consorcio_outros"]
                  + r["pend"] + r["transf"])
    # o que de fato ficou nas contas = sobra + aplicação líquida + aporte de sócio
    r["caixa_calc"] = r["sobra"] + r["aplic_liq"] + r["aporte"]
    return r


# ── Transferências entre contas do grupo: só o que não achou o outro lado ─────
# Pareia por VALOR + DATA (mesmo dia ou dia seguinte), não por nome: o OFX do
# Safra manda metade dos PIX só como "PIX ENVIADO TRANSF" (sem nome, sem CNPJ).
# Matriz, Filial e Braga contam como a mesma casa (mesma raiz de CNPJ).
_APELIDOS = (("robson", "Robson"), ("macdiesel", "Macdiesel"), ("turali", "Turali"),
             ("supernova", "Supernova"), ("rosilene", "Rosilene"),
             ("eb particip", "EB Participações"), ("ferro velho", "Ferro Velho"),
             ("braga", "Edmundo (matriz/filial)"),
             ("edmundo", "Edmundo (matriz/filial)"), ("e pecas", "Edmundo (matriz/filial)"),
             ("e p serv", "Edmundo (matriz/filial)"), ("e p ser", "Edmundo (matriz/filial)"),
             ("06012511", "Edmundo (matriz/filial)"))
_GENERICOS = ("pix enviado transf", "pix recebido transf", "pix enviado", "pix recebido",
              "transferencia enviada", "transferencia recebida")
SEM_NOME = "Sem nome no extrato (os dois lados)"


def _sem_acento(txt: str) -> str:
    return unicodedata.normalize("NFKD", txt or "").encode("ASCII", "ignore").decode().lower()


def _raiz_nome() -> dict[str, str]:
    por_raiz: dict[str, set] = {}
    for e in query("SELECT apelido, cnpj FROM empresas WHERE cnpj IS NOT NULL AND cnpj<>''"):
        por_raiz.setdefault("".join(c for c in e["cnpj"] if c.isdigit())[:8],
                            set()).add(e["apelido"])
    return {r: ("Edmundo (matriz/filial)" if len(n) > 1 else next(iter(n)))
            for r, n in por_raiz.items()}


def _apelido(txt: str, cnpj: str | None, raiz_nome: dict[str, str]) -> str:
    raiz = "".join(c for c in (cnpj or "") if c.isdigit())[:8]
    if raiz in raiz_nome:
        return raiz_nome[raiz]
    t = _sem_acento(txt).strip()
    for chave, nome in _APELIDOS:
        if chave in t:
            return nome
    limpo = " ".join(t.replace("-", " ").split())
    if any(limpo.startswith(g) and len(limpo) <= len(g) + 4 for g in _GENERICOS):
        return SEM_NOME
    return (txt or "?").strip()[:34] or "?"


def transferencias_sem_outro_lado(d_ini: str, d_fim: str, emp_id: int | None = None):
    """(saldo líquido que não fechou, [(quem, líquido, entrou, saiu), ...])."""
    emp_sql = " AND l.empresa_id=?" if emp_id else ""
    par = [d_ini, d_fim] + ([emp_id] if emp_id else [])
    lanc = [dict(x) for x in query(
        """SELECT l.data, l.tipo, l.valor, l.contraparte, l.descricao, l.cnpj_contraparte
           FROM lancamentos l WHERE l.plano_conta_id=? AND l.data BETWEEN ? AND ?"""
        + emp_sql + " ORDER BY l.data, l.valor DESC", tuple([TRANSF_GRUPO] + par))]
    ent = [x for x in lanc if x["tipo"] == "entrada"]
    sai = [x for x in lanc if x["tipo"] == "saida"]
    por_valor: dict[int, list[dict]] = {}
    for e in ent:
        por_valor.setdefault(int(round(e["valor"] * 100)), []).append(e)
    for s in sai:
        for cand in por_valor.get(int(round(s["valor"] * 100)), []):
            if cand.get("_usada"):
                continue
            dias = (pd.Timestamp(str(cand["data"])[:10]) - pd.Timestamp(str(s["data"])[:10])).days
            if 0 <= dias <= 1:
                cand["_usada"] = s["_usada"] = True
                break
    raiz_nome = _raiz_nome()
    por_apelido: dict[str, list[float]] = {}
    for x in ent + sai:
        if x.get("_usada"):
            continue
        a = por_apelido.setdefault(
            _apelido(f"{x['contraparte'] or ''} {x['descricao'] or ''}",
                     x["cnpj_contraparte"], raiz_nome), [0.0, 0.0])
        a[0 if x["tipo"] == "entrada" else 1] += x["valor"]
    nao_fecha = sorted(((n, e - s, e, s) for n, (e, s) in por_apelido.items()
                        if abs(e - s) > 0.01), key=lambda x: -abs(x[1]))
    return sum(liq for _, liq, _, _ in nao_fecha), nao_fecha
