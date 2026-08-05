"""
Comparativo — Entrou × Saiu × Resultado, por empresa.

Confronta o FATURAMENTO (entradas que entram na DRE) com a DESPESA REAL (saídas
que entram na DRE) de cada empresa, e mostra o resultado (sobra/déficit). Tudo
que é movimento interno (entra_dre=0) fica de fora dos dois lados — senão a
transferência entre as contas do grupo distorceria a leitura.
"""
from __future__ import annotations

import unicodedata
from datetime import date

import altair as alt
import pandas as pd
import streamlit as st

from db import query


def brl(v: float) -> str:
    return f"R$ {v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def brl_md(v: float) -> str:
    """Dinheiro dentro de texto markdown. O Streamlit lê `$…$` como fórmula LaTeX:
    num texto com DOIS valores ele renderiza o miolo em monoespaçado e come o «$»
    (era o caso do resumo «Da operação sobraram R$ … abatendo … R$ …»)."""
    return brl(v).replace("R$", "R\\$")


from tema import POSITIVO as VERDE, NEGATIVO as VERMELHO

st.title("⚖️ Comparativo — Entrou × Saiu")

# ── Filtro de período (sempre o grupo todo; o foco é comparar empresas) ───────
ext = query("SELECT MIN(data) lo, MAX(data) hi FROM lancamentos")[0]
lo = pd.to_datetime(ext["lo"]).date() if ext["lo"] else date.today()
hi = pd.to_datetime(ext["hi"]).date() if ext["hi"] else date.today()
periodo = st.date_input("Período", value=(lo, hi), min_value=lo, max_value=hi,
                        format="DD/MM/YYYY")
if isinstance(periodo, (list, tuple)):
    d_ini, d_fim = (periodo[0], periodo[-1]) if periodo else (lo, hi)
else:
    d_ini = d_fim = periodo
par = (d_ini.isoformat(), d_fim.isoformat())

# ── Entrou (faturamento) e Saiu (despesa real) por empresa ────────────────────
entrou = {r["emp"]: r["v"] for r in query(
    """SELECT e.apelido emp, SUM(l.valor) v FROM lancamentos l
       JOIN plano_contas p ON p.id=l.plano_conta_id JOIN empresas e ON e.id=l.empresa_id
       WHERE l.tipo='entrada' AND p.entra_dre=1 AND l.data BETWEEN ? AND ?
       GROUP BY e.apelido""", par)}
saiu = {r["emp"]: r["v"] for r in query(
    """SELECT e.apelido emp, SUM(l.valor) v FROM lancamentos l
       JOIN plano_contas p ON p.id=l.plano_conta_id JOIN empresas e ON e.id=l.empresa_id
       WHERE l.tipo='saida' AND p.entra_dre=1 AND l.data BETWEEN ? AND ?
       GROUP BY e.apelido""", par)}

empresas = sorted(set(entrou) | set(saiu),
                  key=lambda e: -(entrou.get(e, 0) + saiu.get(e, 0)))
if not empresas:
    st.info("Sem dados no período selecionado.")
    st.stop()

dados = [{"Empresa": e, "Entrou": entrou.get(e, 0.0), "Saiu": saiu.get(e, 0.0),
          "Resultado": entrou.get(e, 0.0) - saiu.get(e, 0.0)} for e in empresas]

tot_e = sum(d["Entrou"] for d in dados)
tot_s = sum(d["Saiu"] for d in dados)
tot_r = tot_e - tot_s

st.caption(f"**Grupo Edmundo** · {d_ini.strftime('%d/%m/%Y')} a {d_fim.strftime('%d/%m/%Y')}")

# ── Tudo que saiu do caixa mas não é despesa da operação (retiradas dos sócios,
#    consórcio, empréstimo, saque, valores a recuperar) — Filipe quer abater
#    porque no fim o dinheiro saiu. Ficam de fora só transferências entre as
#    contas (id 31), aporte de sócio (32) e aplicações (33: dinheiro ainda seu).─
retiradas = query(
    """SELECT COALESCE(SUM(CASE WHEN l.tipo='saida' THEN l.valor ELSE -l.valor END),0) v
       FROM lancamentos l JOIN plano_contas p ON p.id=l.plano_conta_id
       WHERE p.entra_dre=0 AND p.id NOT IN (31, 32, 33)
         AND l.data BETWEEN ? AND ?""", par)[0]["v"]
sobra_final = tot_r - retiradas

# ── KPIs do grupo ─────────────────────────────────────────────────────────────
k = st.columns(3)
k[0].metric("💰 Entrou (recebimento)", brl(tot_e))
k[1].metric("💸 Saiu (despesa real)", brl(tot_s))
k[2].metric("📈 Resultado da operação", brl(tot_r),
            "sobrou" if tot_r >= 0 else "faltou",
            delta_color="normal" if tot_r >= 0 else "inverse")

st.caption("Resultado da operação = recebimento − despesa real. Mostra se a operação "
           "em si se paga. Ainda **não** desconta o que os sócios tiraram, consórcio, "
           "empréstimo nem saque.")

# Destaque: o que sobrou de fato, já abatendo o que saiu de caixa fora da operação
s = st.columns(2)
s[0].metric("💵 Sobrou de fato (caixa da operação)", brl(sobra_final),
            help="Resultado da operação menos tudo que saiu de caixa e não é despesa: "
                 "gastos pessoais dos sócios, consórcio, empréstimo, saque e valores "
                 "a recuperar.")
s[1].metric("➖ Saídas que não são despesa (sócios, consórcio, empréstimo, saque)",
            brl(retiradas), delta_color="off",
            help="Saiu do caixa, mas não é despesa da operação (dono tirando dinheiro, "
                 "virando patrimônio, quitando dívida, saque). Aplicações e "
                 "transferências entre contas ficam de fora.")
st.caption(f"Da operação sobraram **{brl_md(tot_r)}**; abatendo tudo que saiu de caixa e "
           f"não é despesa — sócios, consórcio, empréstimo, saque e valores a recuperar "
           f"(**{brl_md(retiradas)}**) — o que de fato sobrou em caixa pela operação foi "
           f"**{brl_md(sobra_final)}**. (Aplicações não entram: o dinheiro continua seu, "
           f"só rendendo.)")

# ── 🔎 Ponte: do resultado da operação ao caixa que de fato ficou ─────────────
_nd = query(
    """SELECT p.nome, l.tipo, SUM(l.valor) v FROM lancamentos l
       JOIN plano_contas p ON p.id=l.plano_conta_id
       WHERE p.entra_dre=0 AND l.data BETWEEN ? AND ?
       GROUP BY p.nome, l.tipo""", par)
_pend = query(
    """SELECT l.tipo, SUM(l.valor) v FROM lancamentos l
       WHERE l.plano_conta_id IS NULL AND l.data BETWEEN ? AND ?
       GROUP BY l.tipo""", par)


def _bucket(nome: str) -> str:
    if nome == "Aplicação/Resgate Financeiro":
        return "Aplicações (dinheiro parado, ainda seu)"
    if nome == "Transferência entre Empresas":
        return "Transferências entre contas do grupo"
    if nome.startswith("Pessoal - ") or nome == "Pró-labore":
        return "Gastos particulares dos sócios"
    return "Consórcio / empréstimos / saque / outros"


bridge: dict[str, float] = {}
for r in _nd:
    bridge[_bucket(r["nome"])] = bridge.get(_bucket(r["nome"]), 0.0) + (
        r["v"] if r["tipo"] == "entrada" else -r["v"])
pend_net = sum((r["v"] if r["tipo"] == "entrada" else -r["v"]) for r in _pend)

# ── DE QUEM É O SALDO QUE NÃO FECHA (04/08/2026) ─────────────────────────────
# Pergunta do Filipe olhando a ponte: "essas transferências todas elas são da Robson?
# porque não importamos nada da Robson, é por isso que estão em aberto". Estava certo
# no principal — mas só olhando o número não dava pra saber. Agora a tela mostra de
# quem é, com a conta aberta. MEDIDO em julho: Robson +150.000,00 · Macdiesel
# −16.289,00 = os R$ 133.711,00 exatos que apareciam sem explicação.
_tr_lanc = query(
    """SELECT l.data, l.tipo, l.valor, l.contraparte, l.descricao, l.cnpj_contraparte
       FROM lancamentos l JOIN plano_contas p ON p.id=l.plano_conta_id
       WHERE p.nome='Transferência entre Empresas' AND l.data BETWEEN ? AND ?
       ORDER BY l.data, l.valor DESC""", par)

# Quem é a contraparte, em três tentativas (da mais confiável pra menos):
#  1) CNPJ (o OFX do Safra traz no 2º MEMO) — pela RAIZ, então matriz e filial, que
#     têm a mesma raiz 06012511, caem juntas sozinhas;
#  2) apelido no texto: o extrato escreve o mesmo grupo de N jeitos ("ROBSON PIME",
#     "Robson Pimentel Da Silva Ltda"). **Braga entra junto do Edmundo** por medição,
#     não por palpite: em julho as 17 saídas «Pix Enviado BRAGA AUTO PECAS»
#     (R$ 767.821,16) têm TODAS a entrada gêmea — mesmo valor, mesmo dia — numa conta
#     do Edmundo; sobra R$ 0,00. Filipe: "esse valor que você considerou com o nome
#     Braga é a Edmundo Matriz". Sem unir, a tela mostrava duas linhas gigantes que se
#     anulam e escondia o que de fato não fecha;
#  3) texto genérico do Safra ("PIX ENVIADO/RECEBIDO TRANSF", sem nome e sem CNPJ):
#     vira UM balde só. São os dois lados da mesma transferência — em junho, 10 saídas
#     e 10 entradas de R$ 411.770,56 cada — e no balde único eles se anulam, em vez de
#     aparecerem como duas linhas gigantes "sem explicação".
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

# raiz do CNPJ -> apelido da empresa cadastrada (raiz repetida = matriz+filial)
_por_raiz: dict[str, set] = {}
for _e in query("SELECT apelido, cnpj FROM empresas WHERE cnpj IS NOT NULL AND cnpj<>''"):
    _por_raiz.setdefault("".join(c for c in _e["cnpj"] if c.isdigit())[:8],
                         set()).add(_e["apelido"])
_RAIZ_NOME = {r: ("Edmundo (matriz/filial)" if len(n) > 1 else next(iter(n)))
              for r, n in _por_raiz.items()}


def _sem_acento(txt: str) -> str:
    return unicodedata.normalize("NFKD", txt or "").encode("ASCII", "ignore").decode().lower()


def _apelido(txt: str, cnpj: str | None) -> str:
    raiz = "".join(c for c in (cnpj or "") if c.isdigit())[:8]
    if raiz in _RAIZ_NOME:
        return _RAIZ_NOME[raiz]
    t = _sem_acento(txt).strip()
    for chave, nome in _APELIDOS:
        if chave in t:
            return nome
    limpo = " ".join(t.replace("-", " ").split())
    if any(limpo.startswith(g) and len(limpo) <= len(g) + 4 for g in _GENERICOS):
        return SEM_NOME
    return (txt or "?").strip()[:34] or "?"


# PAREAR POR VALOR+DATA, NÃO POR NOME. Em junho o OFX do Safra manda metade dos PIX
# só como "PIX ENVIADO TRANSF" (sem nome, sem CNPJ), então agrupar por nome mostrava
# "Edmundo −674.721,10" contra um balde "sem nome +471.359,00" — dois números que se
# anulam e não explicam nada. Pareado é o que TEM o outro lado: mesma quantia saindo
# de uma conta e entrando noutra no mesmo dia (ou no dia seguinte, quando o crédito
# cai depois). O que sobra é o que de fato falta importar.
_ent = [dict(r) for r in _tr_lanc if r["tipo"] == "entrada"]
_sai = [dict(r) for r in _tr_lanc if r["tipo"] == "saida"]
_por_valor: dict[int, list[dict]] = {}
for _e in _ent:
    _por_valor.setdefault(int(round(_e["valor"] * 100)), []).append(_e)
for _s in _sai:
    for _cand in _por_valor.get(int(round(_s["valor"] * 100)), []):
        if _cand.get("_usada"):
            continue
        _dias = (pd.Timestamp(str(_cand["data"])[:10]) - pd.Timestamp(str(_s["data"])[:10])).days
        if 0 <= _dias <= 1:
            _cand["_usada"] = _s["_usada"] = True
            break

_por_apelido: dict[str, list[float]] = {}
for r in _ent + _sai:
    if r.get("_usada"):
        continue                       # achou o outro lado: não é problema nenhum
    a = _por_apelido.setdefault(
        _apelido(f"{r['contraparte'] or ''} {r['descricao'] or ''}",
                 r["cnpj_contraparte"]), [0.0, 0.0])
    a[0 if r["tipo"] == "entrada" else 1] += r["valor"]
_nao_fecha = sorted(((n, e - s, e, s) for n, (e, s) in _por_apelido.items()
                     if abs(e - s) > 0.01), key=lambda x: -abs(x[1]))
# A transferência entre as PRÓPRIAS contas do grupo deveria zerar (sai de uma,
# entra na outra). O saldo que sobra é só distorção de imports incompletos —
# tiramos do corpo da ponte e mostramos como ajuste à parte, pra não enganar.
transf = bridge.pop("Transferências entre contas do grupo", 0.0)
caixa_operacao = tot_r + sum(bridge.values()) + pend_net   # caixa que deveria ter ficado
caixa_calc = caixa_operacao + transf                       # caixa observado hoje

with st.expander("🔎 Ver detalhes: por que o resultado não é o que ficou na conta"):
    st.caption("Tudo aqui vem de extrato — cada lançamento já é dinheiro movimentado "
               "(não existe 'a receber' nem 'a pagar'). O resultado da operação gera "
               "caixa, mas parte dele sai para coisas que **não são despesa**:")
    linhas = [("Resultado da operação (receita − despesa)", tot_r)]
    for nome in ["Aplicações (dinheiro parado, ainda seu)",
                 "Gastos particulares dos sócios",
                 "Consórcio / empréstimos / saque / outros"]:
        if nome in bridge:
            linhas.append((nome, bridge[nome]))
    if pend_net:
        linhas.append(("Saídas ainda sem categoria (pendentes)", pend_net))
    linhas.append(("= Caixa que deveria ter ficado (pela operação)", caixa_operacao))
    if transf:
        linhas.append(("(+) Transferências sem o outro lado (conta não importada)",
                       transf))
    linhas.append(("= Caixa observado hoje nas contas", caixa_calc))
    # dinheiro como TEXTO alinhado à direita: o NumberColumn(format="R$ %.2f") escreve
    # "R$ 134220.90" (ponto decimal, sem milhar) e o "localized" come os centavos.
    st.dataframe(
        pd.DataFrame([{" ": n, "R$": brl(v)} for n, v in linhas]),
        hide_index=True, use_container_width=True,
        column_config={"R$": st.column_config.TextColumn(width="small",
                                                         alignment="right")})
    st.caption("⚠️ Transferência entre as suas próprias contas **não é dinheiro novo** "
               "— deveria zerar (sai de uma conta, entra na outra). Esse saldo de "
               f"**{brl(transf)}** é o que **falta o outro lado**: a conta que mandou "
               "(ou recebeu) o dinheiro não está importada. Veja de quem é logo abaixo.")

    if transf and _nao_fecha:
        st.markdown("**De quem é esse saldo** — só quem não tem o outro lado no período:")
        st.dataframe(
            pd.DataFrame([{"Quem": n, "Entrou": brl(e), "Saiu": brl(s),
                           "Falta o outro lado": brl(liq)}
                          for n, liq, e, s in _nao_fecha]),
            hide_index=True, use_container_width=True,
            column_config={"Quem": st.column_config.TextColumn(width="medium"),
                           **{c: st.column_config.TextColumn(width="small",
                                                             alignment="right")
                              for c in ("Entrou", "Saiu", "Falta o outro lado")}})
        st.caption("As linhas acima somam exatamente o saldo. **Positivo** = o dinheiro "
                   "chegou e não vimos sair (a conta de quem mandou não é importada). "
                   "**Negativo** = saiu daqui e não vimos chegar. Quem é conta de "
                   "**fora do grupo** (Robson, por exemplo) nunca vai zerar sozinho: "
                   "o extrato deles não é importado. · **Como é contado:** só aparece "
                   "aqui o lançamento que **não achou o outro lado** — mesma quantia "
                   "saindo de uma conta e entrando noutra no mesmo dia (ou no seguinte) "
                   "sai da conta, mesmo quando o extrato não escreve o nome. Matriz, "
                   "Filial e Braga contam como a mesma casa: em julho as 17 saídas "
                   "«BRAGA AUTO PECAS» têm todas a entrada gêmea numa conta do Edmundo.")

st.divider()

# ── Barras agrupadas Entrou × Saiu por empresa ────────────────────────────────
st.subheader("Entrou × Saiu por empresa")
long = pd.DataFrame(
    [{"Empresa": d["Empresa"], "Tipo": "Entrou", "Valor": d["Entrou"]} for d in dados] +
    [{"Empresa": d["Empresa"], "Tipo": "Saiu", "Valor": d["Saiu"]} for d in dados])
ch = (
    alt.Chart(long)
    .mark_bar(cornerRadiusEnd=3)
    .encode(
        x=alt.X("Valor:Q", title=None, axis=alt.Axis(format="~s")),
        y=alt.Y("Empresa:N", sort=empresas, title=None),
        yOffset="Tipo:N",
        color=alt.Color("Tipo:N", scale=alt.Scale(domain=["Entrou", "Saiu"],
                                                   range=[VERDE, VERMELHO]),
                        legend=alt.Legend(orient="top", title=None)),
        tooltip=["Empresa:N", "Tipo:N", alt.Tooltip("Valor:Q", format=",.2f", title="R$")],
    )
    .properties(height=80 + 55 * len(empresas))
)
st.altair_chart(ch, use_container_width=True)

# ── Resultado por empresa (verde sobra / vermelho déficit) ────────────────────
st.subheader("Resultado por empresa (sobrou ou faltou)")
rdf = pd.DataFrame([{"Empresa": d["Empresa"], "Resultado": d["Resultado"],
                     "Label": brl(d["Resultado"])} for d in dados])
base = alt.Chart(rdf).encode(
    x=alt.X("Resultado:Q", title=None, axis=alt.Axis(format="~s")),
    y=alt.Y("Empresa:N", sort=empresas, title=None))
barras = base.mark_bar(cornerRadiusEnd=3).encode(
    color=alt.condition(alt.datum.Resultado >= 0, alt.value(VERDE), alt.value(VERMELHO)),
    tooltip=["Empresa:N", alt.Tooltip("Resultado:Q", format=",.2f", title="R$")])
st.altair_chart(barras.properties(height=60 + 32 * len(empresas)), use_container_width=True)

# ── Tabela detalhada ──────────────────────────────────────────────────────────
st.divider()
st.subheader("📋 Tabela")
tdf = pd.DataFrame(dados)
tdf["Margem"] = tdf.apply(
    lambda r: (r["Resultado"] / r["Entrou"] * 100) if r["Entrou"] else 0, axis=1)
st.dataframe(
    tdf, hide_index=True, use_container_width=True,
    column_config={
        "Entrou": st.column_config.NumberColumn(format="R$ %.2f"),
        "Saiu": st.column_config.NumberColumn(format="R$ %.2f"),
        "Resultado": st.column_config.NumberColumn(format="R$ %.2f"),
        "Margem": st.column_config.NumberColumn("Margem", format="%.1f%%",
                                                help="Resultado ÷ Faturamento"),
    })
st.caption("Resultado = Faturamento − Despesa real. Não é o lucro contábil "
           "(falta depreciação, impostos sobre lucro etc.), mas mostra se a "
           "operação de cada empresa sobrou ou faltou caixa no período.")

# ── 🏦 Por conta — onde está o caixa ──────────────────────────────────────────
# Aqui entra TUDO que movimentou a conta (inclusive transferências entre as
# contas do grupo e aplicações), porque o objetivo é mostrar para onde o
# dinheiro foi fisicamente — não o resultado de competência.
st.divider()
st.subheader("🏦 Por conta — onde está o caixa")

cmov = query(
    """SELECT c.id cid, e.apelido emp, c.banco, c.descricao,
              COALESCE(SUM(CASE WHEN l.tipo='entrada' THEN l.valor ELSE 0 END),0) entrou,
              COALESCE(SUM(CASE WHEN l.tipo='saida'   THEN l.valor ELSE 0 END),0) saiu
       FROM lancamentos l
       JOIN contas_bancarias c ON c.id=l.conta_bancaria_id
       JOIN empresas e ON e.id=c.empresa_id
       WHERE l.data BETWEEN ? AND ?
       GROUP BY c.id, e.apelido, c.banco, c.descricao""", par)

if not cmov:
    st.info("Sem movimento por conta no período.")
else:
    contas = [{
        "Empresa": r["emp"],
        "Conta": f'{r["banco"]} · {r["descricao"]}',
        "Entrou": r["entrou"],
        "Saiu": r["saiu"],
        "Caixa do período": r["entrou"] - r["saiu"],
    } for r in cmov]
    contas.sort(key=lambda c: -c["Caixa do período"])
    ordem_contas = [c["Conta"] for c in contas]
    tot_caixa = sum(c["Caixa do período"] for c in contas)

    st.caption(
        "Movimento **real** de cada conta no período: entra tudo (vendas, "
        "transferências de outras contas do grupo, aplicações). "
        "🔴 **negativo = a conta gastou/transferiu mais do que recebeu** — foi "
        "bancada por outras contas (efeito do caixa centralizado). "
        "🟢 positivo = a conta segurou o dinheiro. "
        "É a *variação* do período, não o saldo absoluto (os extratos não trazem "
        "saldo inicial de todas as contas)."
    )

    cdf = pd.DataFrame([{"Conta": c["Conta"], "Caixa do período": c["Caixa do período"],
                         "Empresa": c["Empresa"]} for c in contas])
    barras_c = (
        alt.Chart(cdf)
        .mark_bar(cornerRadiusEnd=3)
        .encode(
            x=alt.X("Caixa do período:Q", title=None, axis=alt.Axis(format="~s")),
            y=alt.Y("Conta:N", sort=ordem_contas, title=None),
            color=alt.condition(alt.datum["Caixa do período"] >= 0,
                                 alt.value(VERDE), alt.value(VERMELHO)),
            tooltip=["Empresa:N", "Conta:N",
                     alt.Tooltip("Caixa do período:Q", format=",.2f", title="R$")],
        )
        .properties(height=60 + 32 * len(contas))
    )
    st.altair_chart(barras_c, use_container_width=True)

    st.dataframe(
        pd.DataFrame(contas), hide_index=True, use_container_width=True,
        column_config={
            "Entrou": st.column_config.NumberColumn(format="R$ %.2f"),
            "Saiu": st.column_config.NumberColumn(format="R$ %.2f"),
            "Caixa do período": st.column_config.NumberColumn(
                format="R$ %.2f", help="Entrou − Saiu (tudo, inclusive transferências internas)"),
        })
    st.caption(
        f"Soma do caixa de todas as contas no período: **{brl(tot_caixa)}** — o "
        f"dinheiro que de fato entrou e ficou. É menor que o resultado da operação "
        f"({brl(tot_r)}) porque parte do que a operação gerou não está na conta "
        f"corrente: foi para **aplicação** (parado, mas ainda seu), para **gastos "
        f"particulares dos sócios** e para **consórcio/empréstimos**. Aqui é tudo "
        f"extrato bancário — não existe valor 'a receber' ou 'a pagar'. "
        f"⚠️ Parte das contas ainda não está importada até o fim do período, então "
        f"este caixa está provisoriamente superestimado."
    )

# ── 💰 Onde está aplicado ─────────────────────────────────────────────────────
st.divider()
st.subheader("💰 Onde está aplicado")

aprows = query(
    """SELECT e.apelido emp, c.banco, c.descricao,
              COALESCE(SUM(CASE WHEN l.tipo='saida'   THEN l.valor ELSE 0 END),0) aplicado,
              COALESCE(SUM(CASE WHEN l.tipo='entrada' THEN l.valor ELSE 0 END),0) resgatado,
              COUNT(*) n
       FROM lancamentos l
       JOIN contas_bancarias c ON c.id=l.conta_bancaria_id
       JOIN empresas e ON e.id=c.empresa_id
       WHERE l.plano_conta_id=33 AND l.data BETWEEN ? AND ?
       GROUP BY e.apelido, c.banco, c.descricao""", par)

if not aprows:
    st.info("Nenhuma aplicação ou resgate no período.")
else:
    def _situacao(aplicado: float, liq: float, n: int) -> str:
        if aplicado and abs(liq) < max(100.0, 0.02 * aplicado) and n > 1:
            return "🔁 vai-e-vem diário (não acumula)"
        if liq > 0:
            return "📥 acumulou na aplicação"
        if liq < 0:
            return "📤 resgatou (tirou da aplicação)"
        return "—"

    aplic = []
    for r in aprows:
        liq = r["aplicado"] - r["resgatado"]
        aplic.append({
            "Empresa": r["emp"],
            "Conta": f'{r["banco"]} · {r["descricao"]}',
            "Aplicado": r["aplicado"],
            "Resgatado": r["resgatado"],
            "Líquido aplicado": liq,
            "Situação": _situacao(r["aplicado"], liq, r["n"]),
        })
    aplic.sort(key=lambda a: -a["Líquido aplicado"])
    tot_liq = sum(a["Líquido aplicado"] for a in aplic)
    acumulado = sum(a["Líquido aplicado"] for a in aplic if a["Líquido aplicado"] > 0)

    m = st.columns(2)
    m[0].metric("📥 Ficou aplicado no período (líquido)", brl(tot_liq))
    m[1].metric("💼 Total enviado para aplicação (bruto)",
                brl(sum(a["Aplicado"] for a in aplic)), delta_color="off")

    st.caption(
        "Quanto dinheiro foi **parar em aplicação** em cada banco no período "
        "(aplicado − resgatado). 🔁 contas marcadas como *vai-e-vem* aplicam e "
        "resgatam o mesmo valor todo dia — não acumulam nada, é só o banco rendendo "
        "o saldo do dia. ⚠️ É o **movimento do período**, não o saldo total da "
        "aplicação (o extrato não traz o saldo da conta-aplicação)."
    )

    # Gráfico só das contas que de fato acumulam/resgatam (ignora vai-e-vem ~0)
    reais = [a for a in aplic if not a["Situação"].startswith("🔁")]
    if reais:
        adf = pd.DataFrame([{"Conta": a["Conta"], "Líquido aplicado": a["Líquido aplicado"],
                             "Empresa": a["Empresa"]} for a in reais])
        ch_ap = (
            alt.Chart(adf)
            .mark_bar(cornerRadiusEnd=3)
            .encode(
                x=alt.X("Líquido aplicado:Q", title=None, axis=alt.Axis(format="~s")),
                y=alt.Y("Conta:N", sort=[a["Conta"] for a in reais], title=None),
                color=alt.condition(alt.datum["Líquido aplicado"] >= 0,
                                     alt.value(VERDE), alt.value(VERMELHO)),
                tooltip=["Empresa:N", "Conta:N",
                         alt.Tooltip("Líquido aplicado:Q", format=",.2f", title="R$")],
            )
            .properties(height=60 + 32 * len(reais))
        )
        st.altair_chart(ch_ap, use_container_width=True)

    st.dataframe(
        pd.DataFrame(aplic), hide_index=True, use_container_width=True,
        column_config={
            "Aplicado": st.column_config.NumberColumn(format="R$ %.2f"),
            "Resgatado": st.column_config.NumberColumn(format="R$ %.2f"),
            "Líquido aplicado": st.column_config.NumberColumn(
                format="R$ %.2f", help="Aplicado − Resgatado no período"),
        })
    maior = aplic[0]
    st.caption(
        f"No período ficaram **{brl(acumulado)}** parados em aplicação"
        + (f" — o maior acúmulo foi em **{maior['Conta']}** ({brl(maior['Líquido aplicado'])})."
           if maior["Líquido aplicado"] > 0 else ".")
        + " Pra saber o **montante total** acumulado em cada aplicação (não só o de "
          "junho), eu preciso do saldo da conta-aplicação ou do saldo inicial — me "
          "passa que eu somo."
    )
