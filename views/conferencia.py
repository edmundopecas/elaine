"""
Conferência — Contas a Pagar (Argos) × Pagamentos (extrato).

Fluxo:
  1) (opcional) sobe a "A Pagar Geral" pra atualizar a lista de títulos previstos;
  2) escolhe o período (o que você acabou de importar, ex. 01–04/jul);
  3) o sistema casa cada título com um pagamento por valor+nome, já deixando
     vinculado os 🟢 fortes; você confirma/liga na mão os 🟡/🔴;
  4) ao salvar, o vínculo (baixa) e a diferença de valor ficam GRAVADOS.

O "previsto" mora na tabela `titulos`; a baixa é `titulos.lancamento_id` apontando
pra saída em `lancamentos`. Nada é destruído: reimportar a planilha só acrescenta
títulos novos (dedup por linha_hash) e não desfaz vínculos já feitos.
"""
from __future__ import annotations

from datetime import date, timedelta

import io

import pandas as pd
import streamlit as st

from conferencia import _norm, casar, parse_a_pagar
from db import execute, executemany, query, query_one


def brl(v) -> str:
    if v is None:
        return "—"
    return f"R$ {v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


# Categorias que, por natureza, NÃO têm título no Contas a Pagar (folha paga por
# funcionário, taxas, tarifas, aplicação/resgate, transferência, tributo, etc.).
# Uma saída dessas na lista "sem título" é ESPERADA — não precisa vincular.
_NAO_PRECISA_TITULO = (
    "mao de obra", "salario", "13", "ferias", "rescis", "pro-labore", "pro labore",
    "pessoal", "folha", "fgts", "inss", "gps", "pensao", "adiantamento",
    "tarifa", "taxas de cartao", "adquirente", "outras despesas financeiras",
    "juros", "iof", "aplicac", "resgate", "rendiment",
    "transferencia entre empresas", "consorcio", "emprestimo", "financiamento",
    "devolu", "icms", "tributo", "imposto", "fecoep", "plano de saude", "seguro",
)


def _precisa_titulo(categoria) -> bool:
    """True = pagamento que DEVERIA ter um título no CPR (revisar); False = folha/
    taxa/interno (esperado não ter título). Sem categoria também vira 'revisar'."""
    if not categoria or categoria in ("—", "A classificar"):
        return True
    n = _norm(categoria)
    return not any(k in n for k in _NAO_PRECISA_TITULO)


st.title("⚖️ Conferência — Contas a Pagar × Pagamentos")
st.caption("Cruza a **A Pagar Geral** (Argos) com as **saídas do extrato**: o que "
           "estava previsto, o que foi pago, o que ficou de fora e a diferença de valor.")

empresas = query("SELECT id, apelido FROM empresas WHERE ativa=1 ORDER BY apelido")
emp_por_apelido = {e["apelido"]: e["id"] for e in empresas}

# ─── 1) Atualizar a lista de Contas a Pagar (subir a planilha) ────────────────
with st.expander("📤 Atualizar Contas a Pagar (subir a planilha *A Pagar Geral*)"):
    arq = st.file_uploader("Planilha A Pagar Geral (.xlsx)", type=["xlsx", "xls"],
                           key="upl_apagar")
    if arq:
        try:
            titulos = parse_a_pagar(arq)
        except Exception as e:
            st.error(f"Não consegui ler a planilha: {e}")
            st.stop()
        st.success(f"**{len(titulos)}** títulos lidos · "
                   f"total previsto **{brl(sum(t['valor'] for t in titulos))}**.")
        # dedup: só insere os que ainda não existem (por linha_hash).
        hashes = [t["linha_hash"] for t in titulos]
        existentes = set()
        for i in range(0, len(hashes), 500):
            lote = hashes[i:i + 500]
            ph = ",".join("?" * len(lote))
            existentes |= {r["linha_hash"] for r in
                           query(f"SELECT linha_hash FROM titulos WHERE linha_hash IN ({ph})",
                                 tuple(lote))}
        novos = [t for t in titulos if t["linha_hash"] not in existentes]
        st.caption(f"{len(novos)} novo(s) · {len(titulos) - len(novos)} já estavam cadastrados.")
        if novos and st.button(f"✅ Cadastrar {len(novos)} título(s) novos", type="primary"):
            executemany(
                "INSERT INTO titulos (empresa_id, tipo, descricao, contraparte, valor, "
                "vencimento, documento, tipo_docto, loja, origem, status, linha_hash) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                [(emp_por_apelido.get(t["empresa"]), "pagar",
                  (t["historico"] or t["fornecedor"])[:200], t["fornecedor"], t["valor"],
                  t["vencimento"], t["documento"], t["tipo_docto"], t["loja"],
                  "argos", "aberto", t["linha_hash"]) for t in novos])
            st.success(f"Cadastrados {len(novos)} títulos. Confira abaixo.")
            st.rerun()

# ─── 2) Período ───────────────────────────────────────────────────────────────
ult = query_one("SELECT MAX(data) hi FROM lancamentos WHERE tipo='saida'")
hi = pd.to_datetime(ult["hi"]).date() if ult and ult["hi"] else date.today()
lo_default = hi - timedelta(days=4)

c1, c2 = st.columns([2, 1])
periodo = c1.date_input("Período dos pagamentos", value=(lo_default, hi),
                        format="DD/MM/YYYY")
if isinstance(periodo, (list, tuple)):
    d_ini, d_fim = (periodo[0], periodo[-1]) if len(periodo) == 2 else (periodo[0], periodo[0])
else:
    d_ini = d_fim = periodo
sel_emp = c2.selectbox("Empresa", ["Todas"] + [e["apelido"] for e in empresas])

# margem: um título vence perto do pagamento (paga adiantado/atrasado alguns dias)
venc_ini = (d_ini - timedelta(days=7)).isoformat()
venc_fim = (d_fim + timedelta(days=7)).isoformat()

cond_t = ["origem='argos'", "vencimento BETWEEN ? AND ?"]
par_t = [venc_ini, venc_fim]
cond_s = ["l.tipo='saida'", "l.data BETWEEN ? AND ?"]
par_s = [d_ini.isoformat(), d_fim.isoformat()]
if sel_emp != "Todas":
    eid = emp_por_apelido[sel_emp]
    cond_t.append("(empresa_id=? OR empresa_id IS NULL)"); par_t.append(eid)
    cond_s.append("l.empresa_id=?"); par_s.append(eid)

titulos_db = query(f"SELECT id, contraparte, valor, vencimento, documento, tipo_docto, "
                   f"loja, empresa_id, status, lancamento_id FROM titulos "
                   f"WHERE {' AND '.join(cond_t)} ORDER BY vencimento, valor DESC",
                   tuple(par_t))
saidas = query(f"""SELECT l.id, l.data, l.valor, l.contraparte, l.descricao,
                   e.apelido AS empresa_apelido,
                   COALESCE(p.nome,'—') AS plano
                   FROM lancamentos l
                   LEFT JOIN empresas e ON e.id=l.empresa_id
                   LEFT JOIN plano_contas p ON p.id=l.plano_conta_id
                   WHERE {' AND '.join(cond_s)} ORDER BY l.data, l.valor DESC""",
               tuple(par_s))

if not titulos_db:
    st.info("Nenhum título de Contas a Pagar nesse período. Suba a **A Pagar Geral** "
            "no expander acima (os títulos precisam vencer perto das datas dos pagamentos).")
    st.stop()

# ─── 3) Casamento (só dos títulos ainda abertos) ─────────────────────────────
sd_por_id = {s["id"]: s for s in saidas}
abertos = [{"_tid": t["id"], "fornecedor": t["contraparte"] or "", "valor": t["valor"],
            "vencimento": t["vencimento"], "documento": t["documento"],
            "tipo_docto": t["tipo_docto"], "loja": t["loja"],
            "empresa": next((e["apelido"] for e in empresas if e["id"] == t["empresa_id"]), None)}
           for t in titulos_db if not t["lancamento_id"]]
ja_baixados = [t for t in titulos_db if t["lancamento_id"]]

# saídas já usadas por baixas existentes não podem ser sugeridas de novo
usadas_baixa = {t["lancamento_id"] for t in ja_baixados}
saidas_livres = [s for s in saidas if s["id"] not in usadas_baixa]
res = casar(abertos, saidas_livres)

# ─── Monta as opções do seletor de pagamento ─────────────────────────────────
def _rotulo_saida(s: dict) -> str:
    d = pd.to_datetime(s["data"]).strftime("%d/%m")
    nome = (s["contraparte"] or s["descricao"] or "—")[:28]
    return f"{d} · {nome} · {brl(s['valor'])} · #{s['id']}"

OP_NENHUM = "— (não pago)"
rotulo_por_id = {s["id"]: _rotulo_saida(s) for s in saidas}
id_por_rotulo = {v: k for k, v in rotulo_por_id.items()}
opcoes = [OP_NENHUM] + [rotulo_por_id[s["id"]] for s in saidas]

EMOJI = {"casado": "🟢", "valor": "🟡", "sem_saida": "🔴"}

linhas = []
# títulos já baixados (conferidos) aparecem no topo, travados como 🟢
for t in ja_baixados:
    s = sd_por_id.get(t["lancamento_id"])
    dif = round((s["valor"] - t["valor"]), 2) if s else None
    linhas.append({"_tid": t["id"], "Status": "🟢 conferido",
                   "Fornecedor": t["contraparte"], "Vencimento": t["vencimento"],
                   "Tipo": t["tipo_docto"], "Previsto": t["valor"],
                   "Pagamento": rotulo_por_id.get(t["lancamento_id"], OP_NENHUM),
                   "Δ (pago-prev)": dif})
for t in res["titulos"]:
    s = t["_saida"]
    linhas.append({"_tid": t["_tid"],
                   "Status": f"{EMOJI[t['_status']]} {t['_status']}"
                             + (f" · {int(t['_sim']*100)}%" if s else ""),
                   "Fornecedor": t["fornecedor"], "Vencimento": t["vencimento"],
                   "Tipo": t["tipo_docto"], "Previsto": t["valor"],
                   "Pagamento": rotulo_por_id.get(s["id"], OP_NENHUM) if s else OP_NENHUM,
                   "Δ (pago-prev)": t["_diferenca"]})

df = pd.DataFrame(linhas)

# ─── KPIs ────────────────────────────────────────────────────────────────────
tot_prev = sum(t["valor"] for t in titulos_db)
tot_conf = sum(t["valor"] for t in ja_baixados) + \
    sum(t["valor"] for t in res["titulos"] if t["_saida"])
tot_naopago = sum(t["valor"] for t in res["titulos"] if not t["_saida"])
tot_dif = sum((r["Δ (pago-prev)"] or 0) for r in linhas)
k = st.columns(4)
k[0].metric("📋 Previsto (Contas a Pagar)", brl(tot_prev), f"{len(titulos_db)} títulos")
k[1].metric("✅ Pago / conferido", brl(tot_conf))
k[2].metric("🔴 Previsto e não pago", brl(tot_naopago))
k[3].metric("↔️ Diferença acumulada", brl(tot_dif),
            help="Soma de (pago − previsto). Negativo = pagou menos (desconto); "
                 "positivo = pagou mais (juros/multa).")

# ─── Exportar Excel pra diretoria (.xlsx de verdade — abre certo no Excel) ────
def _montar_excel_diretoria() -> bytes:
    sem = res["saidas_sem_titulo"]
    resumo = pd.DataFrame({
        "Indicador": ["Período", "Empresa", "Previsto (Contas a Pagar)",
                      "Pago / conferido", "Previsto e não pago",
                      "Diferença acumulada (pago-prev)", "Saídas sem título (total)"],
        "Valor": [f"{d_ini.strftime('%d/%m/%Y')} a {d_fim.strftime('%d/%m/%Y')}",
                  sel_emp, tot_prev, tot_conf, tot_naopago, tot_dif,
                  sum(s["valor"] for s in sem)],
    })
    conf = pd.DataFrame([{c: v for c, v in ln.items() if c != "_tid"} for ln in linhas]) \
        .rename(columns={"Δ (pago-prev)": "Diferença (pago-prev)"})
    semdf = pd.DataFrame([{"Data": pd.to_datetime(s["data"]).strftime("%d/%m/%Y"),
                           "Empresa": s["empresa_apelido"], "Valor": s["valor"],
                           "Fornecedor/Contraparte": s["contraparte"] or s["descricao"],
                           "Categoria": s["plano"]} for s in sem])
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as xl:
        resumo.to_excel(xl, sheet_name="Resumo", index=False)
        if sem:
            (semdf.groupby("Categoria", as_index=False)["Valor"].sum()
             .sort_values("Valor", ascending=False)
             .to_excel(xl, sheet_name="Sem título por categoria", index=False))
        conf.to_excel(xl, sheet_name="Conferência", index=False)
        if sem:
            semdf.to_excel(xl, sheet_name="Saídas sem título", index=False)
    return buf.getvalue()

st.download_button(
    "📊 Baixar Excel (para a diretoria)", data=_montar_excel_diretoria(),
    file_name=f"Conferencia_ContasPagar_{d_ini.strftime('%Y%m%d')}_"
              f"{d_fim.strftime('%Y%m%d')}.xlsx",
    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    help="Abre certinho no Excel (abas: Resumo, Conferência, Saídas sem título).")

st.divider()
st.caption("🟢 forte (valor+nome) · 🟡 valor bate, nome fraco (confira) · "
           "🔴 sem pagamento. Ajuste a coluna **Pagamento** e salve.")

# ─── 4) Editor: confirmar / vincular ─────────────────────────────────────────
editado = st.data_editor(
    df, hide_index=True, use_container_width=True, key="conf_editor",
    column_config={
        "_tid": None,
        "Status": st.column_config.TextColumn(disabled=True, width="small"),
        "Fornecedor": st.column_config.TextColumn(disabled=True),
        "Vencimento": st.column_config.TextColumn(disabled=True, width="small"),
        "Tipo": st.column_config.TextColumn(disabled=True, width="small"),
        "Previsto": st.column_config.NumberColumn(format="R$ %.2f", disabled=True),
        "Pagamento": st.column_config.SelectboxColumn(
            options=opcoes, width="large",
            help="Escolha o pagamento do extrato que quitou este título."),
        "Δ (pago-prev)": st.column_config.NumberColumn(format="R$ %.2f", disabled=True),
    },
)

if st.button("💾 Salvar vínculos", type="primary"):
    novos_vinc = {}   # tid -> saida_id (ou None)
    for _, r in editado.iterrows():
        novos_vinc[r["_tid"]] = id_por_rotulo.get(r["Pagamento"])
    # detecta a mesma saída em dois títulos
    usados = [sid for sid in novos_vinc.values() if sid]
    dups = {x for x in usados if usados.count(x) > 1}
    if dups:
        st.error("O mesmo pagamento está ligado a mais de um título "
                 f"({len(dups)} caso(s)). Cada pagamento só pode quitar um título.")
        st.stop()
    n_lig = n_desl = 0
    atual = {t["id"]: t["lancamento_id"] for t in titulos_db}
    for tid, sid in novos_vinc.items():
        if sid == atual.get(tid):
            continue
        if sid:
            s = sd_por_id[sid]
            execute("UPDATE titulos SET lancamento_id=?, status='pago', data_baixa=? WHERE id=?",
                    (sid, s["data"], tid))
            n_lig += 1
        else:
            execute("UPDATE titulos SET lancamento_id=NULL, status='aberto', "
                    "data_baixa=NULL WHERE id=?", (tid,))
            n_desl += 1
    st.success(f"Salvo! {n_lig} vínculo(s) novo(s)"
               + (f" · {n_desl} desfeito(s)" if n_desl else "") + ".")
    st.rerun()

# ─── Saídas sem título (pagou e não estava previsto) ─────────────────────────
st.divider()
st.subheader("⚪ Saídas sem título no período")
sem = res["saidas_sem_titulo"]


def _tab_sem(lst):
    return pd.DataFrame([{"Data": pd.to_datetime(s["data"]).strftime("%d/%m/%Y"),
                          "Empresa": s["empresa_apelido"], "Valor": s["valor"],
                          "Fornecedor/Contraparte": s["contraparte"] or s["descricao"],
                          "Categoria": s["plano"]} for s in lst])


if not sem:
    st.success("Todo pagamento do período casou com um título. 🎉")
else:
    revisar = [s for s in sem if _precisa_titulo(s["plano"])]
    esperado = [s for s in sem if not _precisa_titulo(s["plano"])]

    st.markdown("**⚠️ Precisa de atenção — pagou e talvez devesse ter título no CPR**")
    st.caption("São pagamentos de fornecedor/despesa sem título correspondente. Se algum "
               "DEVIA ter título, ache-o na tabela de cima (fica 🔴 *não pago*) e vincule "
               "este pagamento lá. Folha, taxa e movimento interno **não** entram aqui.")
    if revisar:
        st.dataframe(_tab_sem(revisar), hide_index=True, use_container_width=True,
                     column_config={"Valor": st.column_config.NumberColumn(format="R$ %.2f")})
        st.caption(f"{len(revisar)} pagamento(s) · **{brl(sum(s['valor'] for s in revisar))}** a revisar.")
    else:
        st.success("Nenhum pagamento suspeito — todo o resto é folha/taxa/interno (ok). 🎉")

    if esperado:
        with st.expander(f"Ver os {len(esperado)} que naturalmente NÃO têm título — "
                         f"folha, taxa, aplicação, pró-labore, tributo "
                         f"({brl(sum(s['valor'] for s in esperado))}) · nada a fazer"):
            st.dataframe(_tab_sem(esperado), hide_index=True, use_container_width=True,
                         column_config={"Valor": st.column_config.NumberColumn(format="R$ %.2f")})
