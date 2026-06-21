"""
DRE — Demonstração de Resultado por empresa e período.
Agrupa os lançamentos classificados por grupo/categoria. Transferências internas
(entra_dre=0) ficam de fora, pra não inflar o resultado com caixa que só circulou.
"""
from __future__ import annotations

from datetime import date

import streamlit as st

from db import init_db, query

st.set_page_config(page_title="DRE", page_icon="📊", layout="wide")
init_db()
st.title("📊 DRE — Demonstração de Resultado")

empresas = query("SELECT id, apelido FROM empresas WHERE ativa=1 ORDER BY apelido")
c1, c2, c3 = st.columns(3)
with c1:
    op = ["Todas as empresas"] + [e["apelido"] for e in empresas]
    sel = st.selectbox("Empresa", op)
    emp_id = None if sel == "Todas as empresas" else next(e["id"] for e in empresas if e["apelido"] == sel)
with c2:
    ini = st.date_input("De", value=date.today().replace(day=1))
with c3:
    fim = st.date_input("Até", value=date.today())

filtro_emp = "AND l.empresa_id = ?" if emp_id else ""
params = (ini.isoformat(), fim.isoformat()) + ((emp_id,) if emp_id else ())

linhas = query(
    f"""SELECT p.grupo, p.nome, p.tipo, p.entra_dre, p.ordem,
               SUM(CASE WHEN l.tipo='entrada' THEN l.valor ELSE -l.valor END) AS total
        FROM lancamentos l JOIN plano_contas p ON p.id = l.plano_conta_id
        WHERE l.classificado=1 AND l.data BETWEEN ? AND ? {filtro_emp}
        GROUP BY p.id ORDER BY p.ordem""",
    params,
)

# Lançamentos sem categoria (não entram, mas avisamos)
sem_cat = query(
    f"""SELECT COUNT(*) n, COALESCE(SUM(valor),0) v FROM lancamentos l
        WHERE l.classificado=0 AND l.data BETWEEN ? AND ? {filtro_emp}""",
    params,
)[0]

dre = [l for l in linhas if l["entra_dre"] == 1]
if not dre:
    st.info("Sem lançamentos classificados no período. Importe e classifique extratos primeiro.")
    if sem_cat["n"]:
        st.warning(f"{sem_cat['n']} lançamento(s) pendente(s) de classificação no período.")
    st.stop()

receitas = sum(l["total"] for l in dre if l["tipo"] == "receita")
despesas = sum(-l["total"] for l in dre if l["tipo"] == "despesa")  # total já vem negativo
resultado = receitas - despesas

m1, m2, m3 = st.columns(3)
m1.metric("Receitas", f"R$ {receitas:,.2f}")
m2.metric("Despesas", f"R$ {despesas:,.2f}")
m3.metric("Resultado", f"R$ {resultado:,.2f}",
          delta=f"{(resultado/receitas*100):.1f}% margem" if receitas else None)

st.divider()

# Tabela por grupo
grupos: dict[str, list] = {}
for l in dre:
    grupos.setdefault(l["grupo"], []).append(l)

for grupo, itens in grupos.items():
    subtotal = sum(l["total"] for l in itens)
    st.markdown(f"**{grupo}** — R$ {subtotal:,.2f}")
    st.dataframe(
        [{"Categoria": l["nome"], "Valor": f"R$ {l['total']:,.2f}"} for l in itens],
        use_container_width=True, hide_index=True,
    )

if sem_cat["n"]:
    st.warning(f"⚠️ {sem_cat['n']} lançamento(s) (R$ {sem_cat['v']:,.2f}) ainda sem "
               "classificação — não estão na DRE acima. Resolva em **Classificar**.")

with st.expander("ℹ️ Ver detalhes do cálculo"):
    st.markdown(
        "- **Receitas** = soma das entradas em categorias do tipo *receita*.\n"
        "- **Despesas** = soma das saídas em categorias do tipo *despesa* "
        "(deduções, custos e despesas operacionais).\n"
        "- **Resultado** = Receitas − Despesas.\n"
        "- **Excluídas:** transferências entre empresas, aportes de sócio e "
        "aplicações financeiras (não são resultado — só movem caixa).\n"
        "- **Não incluídas:** lançamentos ainda sem classificação."
    )
