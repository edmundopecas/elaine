"""
Transferências entre Contas — visão das MOVIMENTAÇÕES INTERNAS do grupo
(grupo 'Movimentações Internas': transferência entre empresas, aplicação/resgate,
saque, consórcio, empréstimos…). Esse dinheiro NÃO entra na DRE — só circula
entre as contas. A tela mostra de qual empresa saiu, para quem foi (incluindo
destinos externos como o ferro velho/Braga) e a lista detalhada.
"""
from __future__ import annotations

import io
from datetime import date

import altair as alt
import pandas as pd
import streamlit as st

from db import query


def brl(v: float) -> str:
    return f"R$ {v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


from tema import FLORESTA as AZUL, TERRACOTA as ROXO, TEXTO

# Prefixos de tipo que o extrato cola na frente do nome do destino — tirar pra
# consolidar (ex.: "Pix Enviado BRAGA" e "BRAGA" viram o mesmo destino).
_PREFIXOS = ("PIX ENVIADO TRANSF", "PIX ENVIADO", "PIX ENVIADO TRANSF -",
             "TED ENVIADO", "TED", "TRANSFERENCIA ENVIADA", "TRANSFERÊNCIA ENVIADA",
             "PIX", "DOC")


def destino_norm(quem: str) -> str:
    """Normaliza o nome do destino pra agrupar (tira prefixo de tipo do banco)."""
    s = (quem or "").strip().upper()
    for p in _PREFIXOS:
        if s.startswith(p):
            s = s[len(p):].strip(" -–").strip()
    return s or "(transferência sem nome)"

st.title("🔄 Transferências entre Contas")
st.caption("Movimentações internas do grupo — transferência entre empresas, "
           "aplicação/resgate, saque etc. **Não entram na DRE**: é dinheiro que "
           "só circulou entre as contas, não é receita nem despesa.")

# ── Filtros ──────────────────────────────────────────────────────────────────
empresas = query("SELECT id, apelido FROM empresas WHERE ativa=1 ORDER BY apelido")
cats = query("SELECT id, nome FROM plano_contas WHERE grupo='Movimentações Internas' "
             "AND ativo=1 ORDER BY ordem")
ext = query("SELECT MIN(data) lo, MAX(data) hi FROM lancamentos")[0]
lo = pd.to_datetime(ext["lo"]).date() if ext["lo"] else date.today()
hi = pd.to_datetime(ext["hi"]).date() if ext["hi"] else date.today()

c1, c2, c3 = st.columns([1, 1, 2])
sel_emp = c1.selectbox("Empresa", ["Todas"] + [e["apelido"] for e in empresas])
sel_cat = c2.selectbox("Tipo", ["Todas (internas)"] + [c["nome"] for c in cats])
periodo = c3.date_input("Período", value=(lo, hi), min_value=lo, max_value=hi,
                        format="DD/MM/YYYY")
if isinstance(periodo, (list, tuple)):
    d_ini, d_fim = (periodo[0], periodo[-1]) if periodo else (lo, hi)
else:
    d_ini = d_fim = periodo

cond = ["p.grupo='Movimentações Internas'", "l.data BETWEEN ? AND ?"]
params = [d_ini.isoformat(), d_fim.isoformat()]
if sel_emp != "Todas":
    eid = next(e["id"] for e in empresas if e["apelido"] == sel_emp)
    cond.append("l.empresa_id=?"); params.append(eid)
if sel_cat != "Todas (internas)":
    cond.append("p.nome=?"); params.append(sel_cat)
where = " AND ".join(cond)

linhas = query(
    f"""SELECT l.data, e.apelido AS emp, COALESCE(cb.descricao, cb.banco, '—') AS conta,
        l.tipo, l.valor, p.nome AS cat,
        COALESCE(NULLIF(TRIM(l.contraparte),''), l.descricao, '(sem nome)') AS quem
        FROM lancamentos l JOIN plano_contas p ON p.id=l.plano_conta_id
        JOIN empresas e ON e.id=l.empresa_id
        LEFT JOIN contas_bancarias cb ON cb.id=l.conta_bancaria_id
        WHERE {where} ORDER BY l.valor DESC""",
    tuple(params),
)
if not linhas:
    st.info("Sem movimentações internas no período/filtros selecionados.")
    st.stop()

saidas = [r for r in linhas if r["tipo"] == "saida"]
entradas = [r for r in linhas if r["tipo"] == "entrada"]
tot_saiu = sum(r["valor"] for r in saidas)
tot_entrou = sum(r["valor"] for r in entradas)
# Transferência entre empresas (o "coração" das internas)
transf_saiu = sum(r["valor"] for r in saidas if "entre Empresas" in r["cat"])

titulo = "Grupo Edmundo" if sel_emp == "Todas" else sel_emp
st.caption(f"**{titulo}** · {d_ini.strftime('%d/%m/%Y')} a {d_fim.strftime('%d/%m/%Y')}")

k = st.columns(3)
k[0].metric("➡️ Saiu (enviado)", brl(tot_saiu), f"{len(saidas)} movimentações",
            delta_color="off")
k[1].metric("⬅️ Entrou (recebido)", brl(tot_entrou), f"{len(entradas)} movimentações",
            delta_color="off")
k[2].metric("🏢 Transferência entre empresas", brl(transf_saiu),
            help="Parte das saídas que é transferência entre contas/empresas "
                 "(inclui destinos externos como o ferro velho).")

st.divider()

# ── Para quem saiu (destino) + de qual empresa saiu (origem) ──────────────────
col_a, col_b = st.columns(2)

with col_a:
    st.subheader("Para quem saiu o dinheiro")
    dest = {}
    for r in saidas:
        chave = destino_norm(r["quem"])
        dest[chave] = dest.get(chave, 0) + r["valor"]
    if dest:
        top = sorted(dest.items(), key=lambda x: -x[1])[:15]
        ddf = pd.DataFrame([{"Destino": q[:40].title(), "Valor": v, "Label": brl(v)}
                            for q, v in top])
        bd = alt.Chart(ddf).encode(
            x=alt.X("Valor:Q", title=None, axis=alt.Axis(format="~s"),
                    scale=alt.Scale(domain=[0, top[0][1] * 1.25])),
            y=alt.Y("Destino:N", sort="-x", title=None))
        st.altair_chart(
            (bd.mark_bar(color=AZUL, cornerRadiusEnd=4).encode(
                tooltip=["Destino:N", alt.Tooltip("Valor:Q", format=",.2f", title="R$")])
             + bd.mark_text(align="left", dx=4, color=TEXTO, fontSize=11).encode(text="Label:N")
             ).properties(height=40 + 28 * len(ddf)),
            use_container_width=True)
        if len(dest) > 15:
            st.caption(f"Mostrando os 15 maiores de {len(dest)} destinos.")
    else:
        st.info("Nenhuma saída no período.")

with col_b:
    st.subheader("De qual empresa saiu")
    orig = {}
    for r in saidas:
        orig[r["emp"]] = orig.get(r["emp"], 0) + r["valor"]
    if orig:
        odf = pd.DataFrame([{"Empresa": e, "Valor": v, "Label": brl(v)}
                            for e, v in sorted(orig.items(), key=lambda x: -x[1])])
        bo = alt.Chart(odf).encode(
            x=alt.X("Valor:Q", title=None, axis=alt.Axis(format="~s"),
                    scale=alt.Scale(domain=[0, max(orig.values()) * 1.25])),
            y=alt.Y("Empresa:N", sort="-x", title=None))
        st.altair_chart(
            (bo.mark_bar(color=ROXO, cornerRadiusEnd=4).encode(
                tooltip=["Empresa:N", alt.Tooltip("Valor:Q", format=",.2f", title="R$")])
             + bo.mark_text(align="left", dx=4, color=TEXTO, fontSize=11).encode(text="Label:N")
             ).properties(height=60 + 32 * len(odf)),
            use_container_width=True)

# ── Por tipo de movimentação ──────────────────────────────────────────────────
st.divider()
st.subheader("📊 Por tipo de movimentação")
portipo = {}
for r in linhas:
    d = portipo.setdefault(r["cat"], [0, 0.0, 0.0])
    d[0] += 1
    if r["tipo"] == "saida":
        d[1] += r["valor"]
    else:
        d[2] += r["valor"]
tdf = pd.DataFrame([{"Tipo": k_, "Qtd": v[0], "Saiu": v[1], "Entrou": v[2]}
                    for k_, v in sorted(portipo.items(), key=lambda x: -(x[1][1] + x[1][2]))])
st.dataframe(tdf, hide_index=True, use_container_width=True,
             column_config={"Saiu": st.column_config.NumberColumn(format="R$ %.2f"),
                            "Entrou": st.column_config.NumberColumn(format="R$ %.2f")})

# ── Lista detalhada + export ──────────────────────────────────────────────────
st.divider()
cab, exp = st.columns([3, 1])
cab.subheader("📋 Lista detalhada")
det = pd.DataFrame([{
    "Data": r["data"], "Empresa": r["emp"], "Conta": r["conta"],
    "Sentido": "➡️ Enviado" if r["tipo"] == "saida" else "⬅️ Recebido",
    "Para/De quem": r["quem"], "Tipo": r["cat"], "Valor": r["valor"]} for r in linhas])

buf = io.BytesIO()
with pd.ExcelWriter(buf, engine="openpyxl") as w:
    det.to_excel(w, index=False, sheet_name="Transferências")
exp.download_button("⬇️ Exportar Excel", buf.getvalue(),
                    file_name=f"transferencias_{d_ini.isoformat()}_a_{d_fim.isoformat()}.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    use_container_width=True)
st.dataframe(det, hide_index=True, use_container_width=True, height=420,
             column_config={"Valor": st.column_config.NumberColumn(format="R$ %.2f")})
