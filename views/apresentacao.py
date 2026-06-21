"""
Apresentação — painel executivo das saídas pra mostrar à direção.

Princípio: separa DESPESA REAL (entra na DRE) de MOVIMENTO INTERNO (transferência
entre contas / aplicação-resgate — entra_dre=0, NÃO é gasto). Sem isso, os grandes
valores de transferência/aplicação distorceriam a leitura. O "a classificar" fica
visível pra direção saber que ainda há refino em andamento.
"""
from __future__ import annotations

from datetime import date

import altair as alt
import pandas as pd
import streamlit as st

from db import query


def brl(v: float) -> str:
    return f"R$ {v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


from tema import POSITIVO, NEUTRO, ATENCAO, CREME, TEXTO, FLORESTA_CLARO

VERDE, CINZA, AMBAR = POSITIVO, NEUTRO, ATENCAO

st.title("📊 Painel de Saídas")

# ── Filtros ──────────────────────────────────────────────────────────────────
empresas = query("SELECT id, apelido FROM empresas WHERE ativa=1 ORDER BY apelido")
ext = query("SELECT MIN(data) lo, MAX(data) hi FROM lancamentos WHERE tipo='saida'")[0]
lo = pd.to_datetime(ext["lo"]).date() if ext["lo"] else date.today()
hi = pd.to_datetime(ext["hi"]).date() if ext["hi"] else date.today()

c1, c2 = st.columns([1, 2])
sel_emp = c1.selectbox("Empresa", ["Todas"] + [e["apelido"] for e in empresas])
periodo = c2.date_input("Período", value=(lo, hi), min_value=lo, max_value=hi,
                        format="DD/MM/YYYY")
if isinstance(periodo, (list, tuple)):
    d_ini, d_fim = (periodo[0], periodo[-1]) if periodo else (lo, hi)
else:
    d_ini = d_fim = periodo

cond, params = ["l.tipo='saida'", "l.data BETWEEN ? AND ?"], [d_ini.isoformat(), d_fim.isoformat()]
if sel_emp != "Todas":
    eid = next(e["id"] for e in empresas if e["apelido"] == sel_emp)
    cond.append("l.empresa_id=?"); params.append(eid)
where = " AND ".join(cond)

rows = query(
    f"""SELECT COALESCE(p.nome,'A classificar') AS cat, p.entra_dre AS dre,
        COUNT(*) AS qtd, SUM(l.valor) AS total
        FROM lancamentos l LEFT JOIN plano_contas p ON p.id=l.plano_conta_id
        WHERE {where} GROUP BY p.id, p.nome, p.entra_dre ORDER BY total DESC""",
    tuple(params),
)
if not rows:
    st.info("Sem saídas no período selecionado.")
    st.stop()

# ── Buckets: despesa real (dre=1) · interno (dre=0) · a classificar (NULL) ────
real = [r for r in rows if r["dre"] == 1]
interno = [r for r in rows if r["dre"] == 0]
pend = [r for r in rows if r["dre"] is None]
tot_saiu = sum(r["total"] for r in rows)
tot_real = sum(r["total"] for r in real)
tot_int = sum(r["total"] for r in interno)
tot_pend = sum(r["total"] for r in pend)
n_pend = sum(r["qtd"] for r in pend)
cmv = sum(r["total"] for r in rows if "Mercadoria" in r["cat"])

titulo = "Grupo Edmundo" if sel_emp == "Todas" else sel_emp
st.caption(f"**{titulo}** · {d_ini.strftime('%d/%m/%Y')} a {d_fim.strftime('%d/%m/%Y')}")

# ── KPIs ─────────────────────────────────────────────────────────────────────
k = st.columns(4)
k[0].metric("🏭 Despesas reais", brl(tot_real),
            help="O que de fato foi gasto (entra no resultado/DRE).")
k[1].metric("🛒 Compra de mercadoria", brl(cmv),
            f"{(cmv / tot_real * 100):.0f}% das despesas" if tot_real else None,
            delta_color="off")
k[2].metric("🔄 Movimento interno", brl(tot_int),
            help="Transferências e aplicações entre contas — NÃO é gasto, só circulou.")
k[3].metric("❓ A classificar", brl(tot_pend), f"{n_pend} lançamentos",
            delta_color="off")

st.divider()

# ── Composição (donut) + Para onde foi (barras) ──────────────────────────────
col_a, col_b = st.columns([1, 1.4])

with col_a:
    st.subheader("Composição das saídas")
    comp = pd.DataFrame([
        {"Grupo": "Despesas reais", "Valor": tot_real},
        {"Grupo": "Movimento interno", "Valor": tot_int},
        {"Grupo": "A classificar", "Valor": tot_pend},
    ])
    comp = comp[comp["Valor"] > 0]
    donut = (
        alt.Chart(comp)
        .mark_arc(innerRadius=70, stroke="#F6F6EF", strokeWidth=2)
        .encode(
            theta=alt.Theta("Valor:Q", stack=True),
            color=alt.Color("Grupo:N", legend=alt.Legend(orient="bottom", title=None),
                            scale=alt.Scale(domain=["Despesas reais", "Movimento interno",
                                                    "A classificar"],
                                            range=[VERDE, CINZA, AMBAR])),
            tooltip=["Grupo:N", alt.Tooltip("Valor:Q", format=",.2f", title="R$")],
        )
        .properties(height=300)
    )
    st.altair_chart(donut, use_container_width=True)
    st.caption(f"De cada R$ 1,00 que saiu, **R$ {tot_real / tot_saiu:.2f}** foi despesa real.")

with col_b:
    st.subheader("Para onde foi o dinheiro (despesas reais)")
    if real:
        # Todas as categorias (sem agrupar em "Outros"); altura dinâmica pra
        # cada barra ter seu espaço e os valores não se sobreporem.
        rdf = pd.DataFrame([{"Categoria": r["cat"], "Valor": r["total"],
                             "Label": brl(r["total"])} for r in real])
        maxv = max(r["total"] for r in real) or 1
        base = alt.Chart(rdf).encode(
            # folga de 22% à direita pra caber o rótulo da maior barra
            x=alt.X("Valor:Q", title=None, axis=alt.Axis(format="~s"),
                    scale=alt.Scale(domain=[0, maxv * 1.22])),
            y=alt.Y("Categoria:N", sort="-x", title=None),
        )
        barras = base.mark_bar(color=VERDE, cornerRadiusEnd=4).encode(
            tooltip=["Categoria:N", alt.Tooltip("Valor:Q", format=",.2f", title="R$")])
        rotulo = base.mark_text(align="left", dx=4, color=TEXTO,
                                fontSize=12).encode(text="Label:N")
        # ~30px por barra: lista um pouco longa, mas tudo legível (sem "Outros")
        altura = 30 + 30 * len(rdf)
        st.altair_chart((barras + rotulo).properties(height=altura),
                        use_container_width=True)
    else:
        st.info("Nenhuma despesa real classificada ainda no período.")

# ── Despesas reais por empresa (só quando vê o grupo todo) ───────────────────
if sel_emp == "Todas":
    st.divider()
    st.subheader("Despesas reais por empresa")
    erows = query(
        f"""SELECT e.apelido AS emp, SUM(l.valor) AS total
            FROM lancamentos l JOIN plano_contas p ON p.id=l.plano_conta_id
            JOIN empresas e ON e.id=l.empresa_id
            WHERE {where} AND p.entra_dre=1
            GROUP BY e.apelido ORDER BY total DESC""",
        tuple(params),
    )
    if erows:
        edf = pd.DataFrame([{"Empresa": r["emp"], "Valor": r["total"],
                             "Label": brl(r["total"])} for r in erows])
        be = alt.Chart(edf).encode(
            x=alt.X("Valor:Q", title=None, axis=alt.Axis(format="~s")),
            y=alt.Y("Empresa:N", sort="-x", title=None))
        st.altair_chart(
            (be.mark_bar(color=FLORESTA_CLARO, cornerRadiusEnd=4).encode(
                tooltip=["Empresa:N", alt.Tooltip("Valor:Q", format=",.2f", title="R$")])
             + be.mark_text(align="left", dx=4, color=TEXTO).encode(text="Label:N")
             ).properties(height=min(60 + 30 * len(edf), 320)),
            use_container_width=True)

# ── Drill-down: para quem foi pago dentro de uma categoria ───────────────────
st.divider()
st.subheader("🔎 Para quem você pagou")
st.caption("Escolha uma categoria e veja o ranking de fornecedores/destinos.")

cats_presentes = [r["cat"] for r in rows]  # já vem ordenado por total (maior 1º)
cat_id_map = {c["nome"]: c["id"] for c in query("SELECT id, nome FROM plano_contas")}
escolha = st.selectbox("Ver fornecedores de:", cats_presentes)

cond_f, params_f = list(cond), list(params)
if escolha == "A classificar":
    cond_f.append("l.plano_conta_id IS NULL")
else:
    cond_f.append("l.plano_conta_id=?"); params_f.append(cat_id_map.get(escolha))
where_f = " AND ".join(cond_f)

forn = query(
    f"""SELECT COALESCE(NULLIF(TRIM(l.contraparte),''), l.descricao, '(sem nome)') AS quem,
        COUNT(*) AS qtd, SUM(l.valor) AS total
        FROM lancamentos l WHERE {where_f}
        GROUP BY quem ORDER BY total DESC""",
    tuple(params_f),
)
if forn:
    total_cat = sum(r["total"] for r in forn)
    cc = st.columns(3)
    cc[0].metric("Total da categoria", brl(total_cat))
    cc[1].metric("Fornecedores/destinos", f"{len(forn)}")
    cc[2].metric("Maior pagamento", brl(forn[0]["total"]),
                 forn[0]["quem"][:22], delta_color="off")

    top = forn[:15]
    fdf = pd.DataFrame([{"Fornecedor": (r["quem"] or "")[:45], "Valor": r["total"],
                         "Label": brl(r["total"])} for r in top])
    bf = alt.Chart(fdf).encode(
        x=alt.X("Valor:Q", title=None, axis=alt.Axis(format="~s")),
        y=alt.Y("Fornecedor:N", sort="-x", title=None))
    st.altair_chart(
        (bf.mark_bar(color=VERDE, cornerRadiusEnd=4).encode(
            tooltip=["Fornecedor:N", alt.Tooltip("Valor:Q", format=",.2f", title="R$")])
         + bf.mark_text(align="left", dx=4, color=TEXTO).encode(text="Label:N")
         ).properties(height=min(60 + 28 * len(fdf), 460)),
        use_container_width=True)
    if len(forn) > 15:
        st.caption(f"Mostrando os 15 maiores de {len(forn)}. Tabela completa abaixo.")

    tdf = pd.DataFrame([{"Fornecedor / destino": r["quem"], "Qtd": r["qtd"],
                         "Total (R$)": r["total"]} for r in forn])
    st.dataframe(tdf, hide_index=True, use_container_width=True,
                 column_config={"Total (R$)": st.column_config.NumberColumn(format="R$ %.2f")})
else:
    st.info("Sem lançamentos nessa categoria no período.")

# ── Rodapé honesto ───────────────────────────────────────────────────────────
st.divider()
if tot_pend > 0:
    st.info(f"ℹ️ Ainda há **{brl(tot_pend)}** em {n_pend} lançamentos a classificar — "
            "os números de despesa devem subir um pouco conforme o refino termina.")
st.caption("Movimento interno (transferências entre contas e aplicações/resgates) é "
           "excluído do resultado por não representar gasto — apenas circulação de caixa.")
