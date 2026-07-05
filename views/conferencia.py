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

from conferencia import _norm, bucket_categoria, casar, parse_a_pagar
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

EMOJI = {"casado": "🟢", "categoria": "🟢", "valor": "🟡", "sem_saida": "🔴"}
ROTULO_STATUS = {"casado": "casado (nome+valor)", "categoria": "casado (categoria+valor)",
                 "valor": "valor", "sem_saida": "sem pagamento"}

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
                   "Status": f"{EMOJI[t['_status']]} {ROTULO_STATUS[t['_status']]}",
                   "Fornecedor": t["fornecedor"], "Vencimento": t["vencimento"],
                   "Tipo": t["tipo_docto"], "Previsto": t["valor"],
                   "Pagamento": rotulo_por_id.get(s["id"], OP_NENHUM) if s else OP_NENHUM,
                   "Δ (pago-prev)": t["_diferenca"]})

df = pd.DataFrame(linhas)

# ─── KPIs ────────────────────────────────────────────────────────────────────
# CONFERIDO = baixa salva + casou por NOME (forte) OU por CATEGORIA (valor + mesma
# categoria — é o caso da folha, CPR no nome da empresa × banco no nome do funcionário).
casados = [t for t in res["titulos"] if t["_status"] in ("casado", "categoria")]
sem_pag = [t for t in res["titulos"] if t["_status"] == "sem_saida"]
tot_prev = sum(t["valor"] for t in titulos_db)
tot_conf = sum(t["valor"] for t in ja_baixados) + sum(t["valor"] for t in casados)
tot_naopago = sum(t["valor"] for t in sem_pag)
tot_dif = sum((s_["valor"] - t["valor"]) for t in ja_baixados
              if (s_ := sd_por_id.get(t["lancamento_id"]))) \
    + sum(t["_diferenca"] for t in casados if t["_diferenca"] is not None)
k = st.columns(4)
k[0].metric("📋 Previsto (Contas a Pagar)", brl(tot_prev), f"{len(titulos_db)} títulos")
k[1].metric("✅ Pago / conferido", brl(tot_conf),
            f"{len(ja_baixados) + len(casados)} de {len(titulos_db)} títulos",
            delta_color="off",
            help="Casou por nome (forte) ou por categoria+valor (ex.: folha).")
k[2].metric("🔴 Sem pagamento no CPR", brl(tot_naopago),
            f"{len(sem_pag)} títulos",
            delta_color="off",
            help="Títulos previstos que não achei pagamento de mesmo valor/categoria.")
k[3].metric("↔️ Diferença acumulada", brl(tot_dif),
            help="Soma de (pago − previsto) dos conferidos. Negativo = desconto; "
                 "positivo = juros/multa.")

# ─── CONCILIAÇÃO POR CATEGORIA (o jeito simples: total × total) ───────────────
# Não casa título-a-título (impossível na folha, que o CPR lança em bloco e o banco
# paga espalhado/em dinheiro). Compara, por categoria: o previsto no CPR × o que de
# fato saiu das contas (SÓ despesa real — fora aplicação, resgate, transferência,
# pró-labore). A diferença mostra o que faltou pagar (ou foi pago em dinheiro).
_bucket = bucket_categoria   # mesma categorização do motor (usada no casamento)

prev_bucket: dict = {}
for t in titulos_db:
    b = _bucket(t["tipo_docto"] or "")
    prev_bucket[b] = prev_bucket.get(b, 0) + t["valor"]

cond_pg = ["l.tipo='saida'", "l.data BETWEEN ? AND ?", "p.entra_dre=1"]
par_pg = [d_ini.isoformat(), d_fim.isoformat()]
if sel_emp != "Todas":
    cond_pg.append("l.empresa_id=?"); par_pg.append(emp_por_apelido[sel_emp])
pago_rows = query(f"SELECT p.nome, SUM(l.valor) tot FROM lancamentos l "
                  f"JOIN plano_contas p ON p.id=l.plano_conta_id "
                  f"WHERE {' AND '.join(cond_pg)} GROUP BY p.nome", tuple(par_pg))
pago_bucket: dict = {}
for r in pago_rows:
    b = _bucket(r["nome"])
    pago_bucket[b] = pago_bucket.get(b, 0) + r["tot"]

st.divider()
st.subheader("📊 Conciliação por categoria")
st.caption("O jeito simples: **quanto o CPR previa × quanto saiu das contas** (só "
           "despesa real — **fora aplicação, transferência e pró-labore**). A folha "
           "não casa 1-a-1 (o CPR lança em bloco, o banco paga espalhado e parte em "
           "dinheiro) — aqui você compara o **total** e a **diferença** mostra o que "
           "faltou (ou foi pago em dinheiro).")
buckets = sorted(set(prev_bucket) | set(pago_bucket),
                 key=lambda b: -(prev_bucket.get(b, 0) + pago_bucket.get(b, 0)))
conc = pd.DataFrame([{
    "Categoria": b, "Previsto (CPR)": round(prev_bucket.get(b, 0), 2),
    "Pago pelas contas": round(pago_bucket.get(b, 0), 2),
    "Diferença (pago − previsto)": round(pago_bucket.get(b, 0) - prev_bucket.get(b, 0), 2),
} for b in buckets])
st.dataframe(conc, hide_index=True, use_container_width=True, column_config={
    "Previsto (CPR)": st.column_config.NumberColumn(format="R$ %.2f"),
    "Pago pelas contas": st.column_config.NumberColumn(format="R$ %.2f"),
    "Diferença (pago − previsto)": st.column_config.NumberColumn(format="R$ %.2f")})
t_prev, t_pago = sum(prev_bucket.values()), sum(pago_bucket.values())
st.caption(f"**Total** — Previsto no CPR: **{brl(t_prev)}** · Pago pelas contas (despesa "
           f"real): **{brl(t_pago)}** · Diferença: **{brl(t_pago - t_prev)}**. "
           "Diferença negativa = faltou pagar ou foi em dinheiro; positiva = pagou algo "
           "fora do CPR (ex.: tributo, aluguel).")

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
        conc.to_excel(xl, sheet_name="Conciliação por categoria", index=False)
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
# ─── 4) Detalhe título-a-título (OPCIONAL) — bom pra bater a mercadoria ───────
with st.expander("🔎 Detalhe: conferir título por título (opcional — útil pra bater "
                 "a mercadoria; a folha não casa 1-a-1)"):
    st.caption("🟢 casou (por nome+valor, ou por categoria+valor — ex.: folha) · "
               "🔴 sem pagamento no CPR. Ajuste a coluna **Pagamento** e salve.")
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
                execute("UPDATE titulos SET lancamento_id=?, status='pago', data_baixa=? "
                        "WHERE id=?", (sid, s["data"], tid))
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
# Busca PURA por valor: esse pagamento tem algum título de MESMO VALOR no CPR?
# (mesmo que não tenha casado por nome/categoria — dá pra ver que o valor existe lá.)
_valores_cpr = {round(t["valor"], 2) for t in titulos_db}


def _tab_sem(lst):
    return pd.DataFrame([{"Data": pd.to_datetime(s["data"]).strftime("%d/%m/%Y"),
                          "Empresa": s["empresa_apelido"], "Valor": s["valor"],
                          "Fornecedor/Contraparte": s["contraparte"] or s["descricao"],
                          "Categoria": s["plano"],
                          "Valor no CPR?": ("✅ sim" if round(s["valor"], 2) in _valores_cpr
                                            else "—")} for s in lst])


if not sem:
    st.success("Todo pagamento do período casou com um título. 🎉")
else:
    revisar = [s for s in sem if _precisa_titulo(s["plano"])]
    esperado = [s for s in sem if not _precisa_titulo(s["plano"])]

    st.markdown("**⚠️ Precisa de atenção — pagou e talvez devesse ter título no CPR**")
    st.caption("Pagamentos de fornecedor/despesa que **não casaram** com um título. A coluna "
               "**Valor no CPR?** mostra se existe um título de mesmo valor lá (aí é só "
               "categorizar o pagamento ou vincular no detalhe). Folha, taxa e movimento "
               "interno **não** entram aqui.")
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
