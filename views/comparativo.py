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
from motor_caixa import ORDEM, SUBTOTAIS, ponte


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

# ── Ponte do motor único (motor_caixa.ponte) — o MESMO da 💊 Saúde Financeira ──
PT = ponte(*par)
# o que saiu do caixa e não é despesa: sócios, empréstimo, consórcio/saque/a recuperar
retiradas = -(PT["socios"] + PT["emprestimos"] + PT["consorcio_outros"])
sobra_final = PT["sobra"]     # mesma linha "= SOBRA / FALTA" da tabela abaixo
assert abs(PT["resultado"] - tot_r) < 0.01, "motor e tela divergiram — investigar"

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
                 "a recuperar — mais pendentes e transferências sem o outro lado. "
                 "É a linha '= SOBRA / FALTA' da tabela abaixo.")
s[1].metric("➖ Saídas que não são despesa (sócios, consórcio, empréstimo, saque)",
            brl(retiradas), delta_color="off",
            help="Saiu do caixa, mas não é despesa da operação (dono tirando dinheiro, "
                 "virando patrimônio, quitando dívida, saque). Aplicações e "
                 "transferências entre contas ficam de fora.")
st.caption(f"Da operação sobraram **{brl_md(tot_r)}**; abatendo tudo que saiu de caixa e "
           f"não é despesa — sócios, consórcio, empréstimo, saque e valores a recuperar "
           f"(**{brl_md(retiradas)}**), mais pendentes e transferências sem o outro lado "
           f"(**{brl_md(PT['pend'] + PT['transf'])}**) — o que de fato sobrou em caixa pela "
           f"operação foi **{brl_md(sobra_final)}**. (Aplicações não entram: o dinheiro "
           f"continua seu, só rendendo.)")

# ── 🔎 Ponte completa: entrou → saiu → o que ficou no caixa ─────────────────
transf, _nao_fecha = PT["transf"], PT["nao_fecha"]
with st.expander("🔎 Ver tudo: o que entrou, o que saiu e por que o caixa não é o resultado",
                 expanded=True):
    st.caption("Tudo aqui vem de extrato — cada lançamento já é dinheiro movimentado. "
               "Fica de fora só o que é dinheiro trocando de bolso: aplicação/resgate que "
               "o próprio banco faz e transferência entre contas do grupo **que achou o "
               "outro lado**. Mesma tabela da 💊 Saúde Financeira.")
    linhas = [(nome, PT[k]) for nome, k in ORDEM
              if k in SUBTOTAIS or abs(PT[k]) >= 0.005]
    linhas.append(("(+) Aplicação líquida no período (dinheiro parado, ainda seu)",
                   PT["aplic_liq"]))
    if abs(PT["aporte"]) >= 0.005:
        linhas.append(("(+) Aporte / empréstimo de sócio", PT["aporte"]))
    linhas.append(("= Caixa que de fato ficou nas contas", PT["caixa_calc"]))
    df_p = pd.DataFrame([{" ": n, "R$": brl(v)} for n, v in linhas])
    negrito = df_p[" "].str.startswith("=")
    st.dataframe(
        df_p.style.apply(lambda col: ["font-weight: bold" if b else "" for b in negrito]),
        hide_index=True, use_container_width=True,
        column_config={"R$": st.column_config.TextColumn(width="small",
                                                         alignment="right")})
    if abs(transf) > 0.01 and _nao_fecha:
        st.markdown("**Transferências sem o outro lado — de quem é:**")
        st.dataframe(
            pd.DataFrame([{"Quem": n, "Entrou": brl(e), "Saiu": brl(s),
                           "Falta o outro lado": brl(liq)}
                          for n, liq, e, s in _nao_fecha]),
            hide_index=True, use_container_width=True,
            column_config={"Quem": st.column_config.TextColumn(width="medium"),
                           **{c: st.column_config.TextColumn(width="small",
                                                             alignment="right")
                              for c in ("Entrou", "Saiu", "Falta o outro lado")}})
        st.caption("**Positivo** = o dinheiro chegou e não vimos sair (a conta de quem "
                   "mandou não é importada). **Negativo** = saiu daqui e não vimos chegar. "
                   "Conta de **fora do grupo** (Robson, por exemplo) nunca fecha sozinha. "
                   "Só aparece o lançamento que **não achou o outro lado** — mesma quantia "
                   "saindo de uma conta e entrando noutra no mesmo dia (ou no seguinte) "
                   "fecha e some, mesmo sem nome no extrato.")

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
