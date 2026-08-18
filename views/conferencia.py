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

from collections import Counter
from datetime import date, timedelta

import io

import pandas as pd
import streamlit as st

import importlib

import conferencia as _motor

# AUTO-CURA DE DEPLOY (02/08/2026) — por que este reload existe:
# no Streamlit Cloud o processo é REAPROVEITADO depois do deploy. A PÁGINA
# (views/conferencia.py) é re-executada a cada rerun, mas `conferencia.py` é um MÓDULO:
# fica em sys.modules com a versão ANTIGA. Quando o deploy traz função nova no motor
# (foi o caso do `sugerir`), o import de baixo estoura `ImportError: cannot import name
# 'sugerir'` e a tela fica VERMELHA até alguém clicar em Reboot no painel do Streamlit.
# Recarregando o módulo quando falta o símbolo novo, a tela se conserta sozinha no
# primeiro F5. Ao acrescentar função nova ao motor, troque o nome na checagem abaixo.
if not hasattr(_motor, "sugerir"):
    importlib.reload(_motor)          # atualiza sys.modules['conferencia'] no lugar

import baixas
from conferencia import (_norm, bucket_categoria, casar, parse_a_pagar, similaridade,
                         sugerir, sugerir_combos)
from db import executemany, query, query_one


def brl(v) -> str:
    if v is None:
        return "—"
    return f"R$ {v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def brl_md(v) -> str:
    """Dinheiro pra ser escrito em **markdown** (st.info/warning/markdown/caption).

    Por que existe (04/08/2026, visto na tela): o Streamlit trata `$…$` como fórmula
    LaTeX. Num texto com DOIS valores — "R$ 20.154,32 + R$ 1.165,60" — ele lê tudo
    entre os dois cifrões como matemática e escreve o miolo em monoespaçado, comendo
    o «$» e a formatação. Escapando o cifrão o texto sai como dinheiro mesmo.
    Só pra markdown: em tabela/Excel o `\\$` apareceria literal, lá continua o `brl`.
    """
    return brl(v).replace("R$", "R\\$")


def brl_ord(valores) -> list[str]:
    """Dinheiro em BR (R$ 1.234,56) **que ordena pelo valor** quando o Filipe clica
    no cabeçalho da coluna.

    Por que existe (03/08/2026): coluna de NÚMERO ordena certo mas o Streamlit não
    escreve dinheiro em português — `format="R$ %.2f"` sai "R$ 3930.38" e
    `format="localized"` come os centavos ("R$ 2.500"); e célula vazia vira a palavra
    **None** (testado: até nulo de verdade em Float64 escreve "None"). Coluna de TEXTO
    escreve bonito, mas ordena como PALAVRA — foi o que ele viu: 336 · 300 · 3.930 ·
    290. A saída é texto **preenchido com espaço à esquerda até todos terem o mesmo
    tamanho**: aí a ordem alfabética passa a ser a ordem do valor (espaço vem antes de
    dígito), e como a coluna é alinhada à direita o espaço não aparece.
    SÓ vale pra coluna de valores POSITIVOS — com negativo o comprimento deixa de
    acompanhar a grandeza e a ordem sai errada.
    """
    txt = ["" if v is None or pd.isna(v) else brl(v) for v in valores]
    larg = max((len(t) for t in txt), default=0)
    return [t.rjust(larg) for t in txt]


# REGRA (pedido do Filipe, 07/07/2026): "✅ está no CPR" SÓ quando a saída casa de
# verdade com um título (nome+valor) OU já tem baixa. TUDO que não casou = "❌ não
# está no CPR" — sem lista de exceção por categoria. A tela é uma conferência crua do
# que está e do que não está; nada é marcado "✅" por dedução de categoria (isso
# mentia "sim" pra tarifa/devolução/folha que não passam no CPR e quebrava a confiança).

# Dinheiro que anda entre as contas do grupo não é pagamento de título.
_EXCLUIR_PAGO = ("aplicac", "resgate", "transferencia entre empresas")

# ─── POR QUE ESTÁ FORA? — duas filas dentro do ❌ (02/08/2026) ────────────────
# Reclamação do Filipe: "toda vez que baixo a planilha vem coisa nova" — o ❌ nunca
# esvazia. MEDIDO no período 27–31/07: das 191 saídas fora do CPR, 151 eram de
# categorias que praticamente NUNCA têm título no Argos (pagas direto pelo banco:
# tarifa, folha via SISPAG, débito automático). Elas se repetem todo dia e afogam o
# que de fato falta conferir.
# A lista abaixo NÃO é palpite — é a taxa de casamento medida em JULHO INTEIRO
# (saída de julho × título já conciliado):
#     Outras Despesas Financeiras  470 saídas →   0 com título ( 0%)
#     Salários                     132        →   5            ( 4%)
#     Devoluções e Descontos       111        →   0            ( 0%)
#     Taxas de Cartão/Adquirente    25        →   0            ( 0%)
#     Empréstimos/Financiamentos    16        →   0            ( 0%)
#     Consórcio                      7        →   0            ( 0%)
# Só entra categoria com ~0% de casamento. IPTU (52%), MEI/Prestadores (44%),
# ICMS sobre Compras (12%) e Pró-labore (12%) ficam DE FORA de propósito: aparecem
# no CPR com frequência, então continuam na fila de conferir.
# IMPORTANTE: isto NÃO marca ninguém como ✅ — a regra de 07/07 continua valendo
# (nada vira "está no CPR" por dedução de categoria). Só separa o ❌ em duas filas,
# e os três números do topo não mudam. Uma saída dessas categorias que TENHA título
# casa normalmente e nem chega aqui (por isso a separação é de baixo risco: o pior
# caso é uma cobrança dessas realmente faltar no CPR e cair na 2ª lista, não sumir).
# Comparação por nome EXATO da categoria (normalizado) — de propósito: por pedaço,
# "Salários" pegaria "13º Salário", que casa 100% no CPR. Editável sem medo.
# (04/08/2026: também é a lista que o `sugerir_combos` ignora ao procurar título
# pago em DUAS guias — sem ela, tarifa de centavos soma qualquer valor.)
NATUREZA_FORA_CPR = frozenset(_norm(x) for x in (
    "Outras Despesas Financeiras",   # tarifa, juros, IOF — o banco debita direto
    "Salários",                      # folha via SISPAG/PAGSAL, não passa no CPR
    "Devoluções e Descontos",        # estorno/devolução, não é pagamento de título
    "Taxas de Cartão/Adquirente",
    "Empréstimos/Financiamentos",    # amortização em débito automático
    "Consórcio",
))


def _natureza(plano) -> bool:
    """A saída é de uma categoria que não passa pelo Contas a Pagar do Argos?"""
    return _norm(plano or "") in NATUREZA_FORA_CPR


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
        # dedup por MULTISET de linha_hash (robusto a títulos legítimos idênticos:
        # dois boletos do mesmo fornecedor, mesmo valor, mesmo vencimento e sem
        # documento geram o MESMO hash — igual às parcelas de consórcio em lancamentos).
        # Conta quantas ocorrências de cada hash-base já existem no banco e só insere a
        # SOBRA, com sufixo (#2, #3) pra respeitar o índice UNIQUE. Sem isso, o
        # executemany estourava UniqueViolation quando o arquivo trazia hashes iguais.
        def _base(h: str) -> str:
            return h.split("#", 1)[0]

        existentes_raw = {r["linha_hash"] for r in query(
            "SELECT linha_hash FROM titulos WHERE linha_hash IS NOT NULL")}
        db_counts = Counter(_base(h) for h in existentes_raw)
        usados = set(existentes_raw)

        vistos: Counter = Counter()
        novos: list[tuple[dict, str]] = []  # (título, linha_hash único)
        for t in titulos:
            b = t["linha_hash"]
            vistos[b] += 1
            if vistos[b] <= db_counts.get(b, 0):
                continue  # essa ocorrência já está no banco
            h = b
            k = 1
            while h in usados:
                k += 1
                h = f"{b}#{k}"
            usados.add(h)
            novos.append((t, h))
        st.caption(f"{len(novos)} novo(s) · {len(titulos) - len(novos)} já estavam cadastrados.")
        if novos and st.button(f"✅ Cadastrar {len(novos)} título(s) novos", type="primary"):
            executemany(
                "INSERT INTO titulos (empresa_id, tipo, descricao, contraparte, valor, "
                "vencimento, documento, tipo_docto, loja, origem, status, linha_hash) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                [(emp_por_apelido.get(t["empresa"]), "pagar",
                  (t["historico"] or t["fornecedor"])[:200], t["fornecedor"], t["valor"],
                  t["vencimento"], t["documento"], t["tipo_docto"], t["loja"],
                  "argos", "aberto", h) for t, h in novos])
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
# BAIXA COM VÁRIOS PAGAMENTOS (04/08/2026): quem manda agora é `titulo_baixas`
# (N↔N, ver baixas.py) — um título de ICMS pode ter sido pago em DUAS guias GNRE no
# mesmo dia. `titulos.lancamento_id` continua preenchido com o principal, então
# `baixas.por_titulo` devolve a união dos dois e nada se perde.
sd_por_id = {s["id"]: s for s in saidas}
baixas_por_tid = baixas.por_titulo([t["id"] for t in titulos_db])
abertos = [{"_tid": t["id"], "fornecedor": t["contraparte"] or "", "valor": t["valor"],
            "vencimento": t["vencimento"], "documento": t["documento"],
            "tipo_docto": t["tipo_docto"], "loja": t["loja"],
            "empresa": next((e["apelido"] for e in empresas if e["id"] == t["empresa_id"]), None)}
           for t in titulos_db if not baixas_por_tid.get(t["id"])]
ja_baixados = [t for t in titulos_db if baixas_por_tid.get(t["id"])]

# ─── PAGAMENTO FORA DO PERÍODO (03/08/2026) — BUG GRAVE, achado no teste ──────
# Um título já conciliado com uma saída de FORA do período aberto (baixa feita em
# 20/07 com a tela em 27–31/07) não encontrava o rótulo: a célula Pagamento mostrava
# "— (não pago)" mesmo estando 🟢 conferido. E o pior: ao clicar em **Salvar
# conciliação** o motor lia aquilo como "sem pagamento" e **DESFAZIA a baixa** —
# 50 títulos por página de uma vez, sem avisar. Buscando esses lançamentos à parte,
# o rótulo existe, a célula mostra o pagamento certo e o salvar não mexe em nada.
# (O `if` do salvar também ganhou uma trava, ver lá embaixo.)
_ids_fora = [lid for t in ja_baixados for lid in baixas_por_tid.get(t["id"], [])
             if lid not in sd_por_id]
if _ids_fora:
    _ph = ",".join(["?"] * len(_ids_fora))
    for _s in query(f"""SELECT l.id, l.data, l.valor, l.contraparte, l.descricao,
                        e.apelido AS empresa_apelido, COALESCE(p.nome,'—') AS plano
                        FROM lancamentos l
                        LEFT JOIN empresas e ON e.id=l.empresa_id
                        LEFT JOIN plano_contas p ON p.id=l.plano_conta_id
                        WHERE l.id IN ({_ph})""", tuple(_ids_fora)):
        sd_por_id[_s["id"]] = _s

# saídas já usadas por baixas existentes não podem ser sugeridas de novo
# (mapa global: um pagamento pode ter quitado título de OUTRO período — é o que
# permite o caso "guia junta" e o que alimenta a coluna Situação do 🎯)
titulos_da_saida = baixas.titulos_por_lancamento()
usadas_baixa = {lid for t in ja_baixados for lid in baixas_por_tid.get(t["id"], [])}
saidas_livres = [s for s in saidas if s["id"] not in titulos_da_saida]
res = casar(abertos, saidas_livres)

# ─── PALPITE pros 🔴 (02/08/2026) ────────────────────────────────────────────
# Filipe: "isso aqui tá uma bagunça, não to conseguindo nem achar nada". O dropdown
# de Pagamento é UM só pra coluna inteira (limitação do SelectboxColumn), ordenado por
# valor — pra um título de R$1.495 ele oferecia R$249 mil primeiro. Em julho são 231
# títulos 🔴 contra 973 saídas livres: achar na mão é agulha no palheiro.
# Agora o sistema procura por linha: `sugerir` acha o melhor pagamento DAQUELE título
# (valor ±5% + nome), o texto explica o porquê e um checkbox liga em lote.
usadas_casar = {t["_saida"]["id"] for t in res["titulos"] if t["_saida"]}
disponiveis = [s for s in saidas_livres if s["id"] not in usadas_casar]
sem_pgto = [t for t in res["titulos"] if t["_status"] == "sem_saida"]
sug_por_tid = {sem_pgto[i]["_tid"]: inf
               for i, inf in sugerir(sem_pgto, disponiveis).items()}

# ─── PAGO EM DUAS GUIAS (04/08/2026) ─────────────────────────────────────────
# Pedido do Filipe: "paga ICMS e FECOEP às vezes em guias juntas e às vezes
# separadas; queria pegar os dois pagamentos e ligar com 1 do que está lançado no
# financeiro". O `sugerir` é 1-p/-1, então esses títulos ficavam eternamente 🔴.
# O `sugerir_combos` procura DOIS pagamentos do mesmo dia, mesma categoria, que
# somem o valor exato do título. MEDIDO em julho: achou 4 e acertou os 4 (ENERGEX
# 21.319,92 = 20.154,32 + 1.165,60; W1 1.261,58; CAMBUCI 50,32; PADRE CICERO 28,56),
# sem nenhum falso positivo. Continua valendo a regra: nada é ligado sozinho.
combo_por_tid = {sem_pgto[i]["_tid"]: lista[0]
                 for i, lista in sugerir_combos(
                     sem_pgto, disponiveis,
                     ignorar_categorias=NATUREZA_FORA_CPR).items()}

# ─── Monta as opções do seletor de pagamento ─────────────────────────────────
# ORDEM DO RÓTULO (03/08/2026): o VALOR vem primeiro. Antes era "data · nome · valor"
# e o Filipe — que procura pelo VALOR do título — tinha que varrer o meio de cada
# linha pra achar o número. Com o valor na frente e a lista ordenada por valor, achar
# R$ 2.926,10 é descer a coluna até chegar na faixa.
# NÃO alinhar o valor com espaço (tentei U+2007): o navegador APARA espaço no começo
# do texto, aí o valor da célula deixa de bater com a opção da lista e o Streamlit
# desenha a célula VAZIA — títulos conferidos apareciam sem pagamento nenhum.
def _rotulo_saida(s: dict) -> str:
    d = pd.to_datetime(s["data"]).strftime("%d/%m")
    nome = (s["contraparte"] or s["descricao"] or "—")[:28]
    return f"{brl(s['valor'])} · {d} · {nome} · #{s['id']}"

OP_NENHUM = "— (não pago)"
rotulo_por_id = {s["id"]: _rotulo_saida(s) for s in sd_por_id.values()}
id_por_rotulo = {v: k for k, v in rotulo_por_id.items()}


# TÍTULO PAGO EM 2+ GUIAS (04/08/2026): a coluna Pagamento é um selectbox com UMA
# lista pra tabela inteira — não tem como mostrar dois pagamentos numa célula. Então
# o conjunto ganha um rótulo próprio, que entra na lista de opções (obrigatório: se o
# valor da célula não estiver nas opções, o Streamlit desenha a célula VAZIA e o
# Salvar leria isso como "sem pagamento"). O nº do título no fim garante que dois
# títulos com a mesma soma não compartilhem rótulo.
def _rotulo_multi(tid: int, ss: list[dict]) -> str:
    return (f"🔗 {len(ss)} pagamentos · {brl(sum(s['valor'] for s in ss))} · nº {tid}")

# TETO DO DROPDOWN (02/08/2026) — por que existe:
# o SelectboxColumn manda TODAS as opções pro navegador e as materializa ao abrir a
# célula. Num período de mês inteiro são ~1.900 saídas: MEDIDO — com 1.880 opções a aba
# CONGELA (>40s sem responder) e, ao voltar, a escolha do Filipe SOME (o front travou
# antes de mandar a edição pro servidor, então o widget é redesenhado do estado antigo);
# com ~50 opções o dropdown abre na hora e a escolha persiste. Daí o filtro + o teto.
MAX_OPCOES = 150

EMOJI = {"casado": "🟢", "categoria": "🟢", "valor": "🟡", "sem_saida": "🔴"}
# rótulos CURTOS: os antigos ("casado (categoria+valor)") não cabiam na coluna e
# apareciam cortados na tela do Filipe ("sem pagamen").
ROTULO_STATUS = {"casado": "casado", "categoria": "casado (cat.)",
                 "valor": "só valor", "sem_saida": "sem pgto"}
SEM_SUG = "—"


def _venc_curto(v) -> str:
    """Vencimento como 31/07 — o "2026-07-31" comia a largura da coluna de sugestão."""
    return pd.to_datetime(v).strftime("%d/%m") if v else "—"


def _venc_tipo(v, tipo) -> str:
    """"31/07 · MERCADORIA" numa coluna só."""
    t = (tipo or "").strip()
    return f"{_venc_curto(v)} · {t}" if t else _venc_curto(v)


def _texto_sug(inf: dict) -> str:
    """O palpite escrito por extenso, COM O MOTIVO — ele confere sem abrir nada."""
    s = inf["saida"]
    d = pd.to_datetime(s["data"]).strftime("%d/%m")
    nome = (s["contraparte"] or s["descricao"] or "—")[:30]
    # quando o valor é igual não repete o valor (já está na coluna Previsto) — é o que
    # cabe na largura da coluna sem cortar o "nome X%", que é metade da explicação.
    motivo = ("mesmo valor" if abs(inf["dif"]) <= 0.01
              else f"{brl(s['valor'])} (dif. {brl(inf['dif'])})")
    return f"{d} · {nome} · {motivo} · nome {inf['sim']:.0%}"


def _texto_combo(c: dict) -> str:
    """O par de guias escrito por extenso: «24/07 · 2 pagamentos somam exato ·
    20.154,32 + 1.165,60»."""
    d = pd.to_datetime(c["dia"]).strftime("%d/%m")
    return (f"{d} · 2 pagamentos somam exato · "
            + " + ".join(brl(s["valor"]).replace("R$ ", "") for s in c["saidas"]))


linhas = []
tid_do_rotulo_multi: dict[str, int] = {}   # rótulo "🔗 2 pagamentos…" -> título
# títulos já baixados (conferidos) aparecem no topo, travados como 🟢
for t in ja_baixados:
    lids = baixas_por_tid.get(t["id"], [])
    ss = [sd_por_id[l] for l in lids if l in sd_por_id]
    dif = round(sum(s["valor"] for s in ss) - t["valor"], 2) if ss else None
    if len(ss) > 1:
        rot = _rotulo_multi(t["id"], ss)
        tid_do_rotulo_multi[rot] = t["id"]
    else:
        rot = rotulo_por_id.get(lids[0], OP_NENHUM) if lids else OP_NENHUM
    linhas.append({"_tid": t["id"], "_score": -1.0,
                   "Status": "🟢 conferido" + (" (2 guias)" if len(ss) > 1 else ""),
                   "Fornecedor": t["contraparte"],
                   "Venc. · Tipo": _venc_tipo(t["vencimento"], t["tipo_docto"]),
                   "Previsto": t["valor"],
                   "💡 Sugestão do sistema": SEM_SUG, "Ligar": False,
                   "Pagamento": rot, "Δ (pago-prev)": dif})
for t in res["titulos"]:
    s = t["_saida"]
    inf = sug_por_tid.get(t["_tid"])
    combo = combo_por_tid.get(t["_tid"])
    # o par de guias tem prioridade na coluna 💡: soma EXATA vale mais que o palpite
    # 1-p/-1 (que aceita ±5% de diferença).
    texto = (_texto_combo(combo) if combo else
             (_texto_sug(inf) if inf else SEM_SUG))
    linhas.append({"_tid": t["_tid"],
                   "_score": 1.0 if combo else (inf["score"] if inf else -1.0),
                   "Status": f"{EMOJI[t['_status']]} {ROTULO_STATUS[t['_status']]}",
                   "Fornecedor": t["fornecedor"],
                   "Venc. · Tipo": _venc_tipo(t["vencimento"], t["tipo_docto"]),
                   "Previsto": t["valor"],
                   "💡 Sugestão do sistema": texto,
                   "Ligar": False,
                   "Pagamento": rotulo_por_id.get(s["id"], OP_NENHUM) if s else OP_NENHUM,
                   "Δ (pago-prev)": t["_diferenca"]})

df = pd.DataFrame(linhas)
# Δ vem só de None quando nada casou → vira coluna de texto e o editor escreve
# "None" em cada linha (o que o Filipe viu). Como número, célula vazia é vazia.
df["Δ (pago-prev)"] = pd.to_numeric(df["Δ (pago-prev)"], errors="coerce")

# ─── OS 3 NÚMEROS ────────────────────────────────────────────────────────────
# Total pago FORA aplicação/resgate E transferência entre empresas (dinheiro andando
# entre as contas do grupo — não é pagamento de verdade; Filipe confirmou tirar,
# a lista `_EXCLUIR_PAGO` está lá em cima junto das outras regras da tela).
saidas_reais = [s for s in saidas
                if not any(k in _norm(s["plano"] or "") for k in _EXCLUIR_PAGO)]
tot_pago = sum(s["valor"] for s in saidas_reais)


# ⚠️ O PREVISTO É O QUE VENCE NO PERÍODO (17/08/2026) — o Filipe viu na tela:
# «esse valor do que está previsto no CPR ainda está errado». Estava mesmo: a
# métrica somava os `titulos_db`, que vêm por vencimento ±7 dias (a margem que o
# CASAMENTO precisa, porque se paga adiantado/atrasado alguns dias). Num período de
# 01–14/08 isso somava 876 títulos vencendo de 25/07 a 21/08 = R$ 2.321.611,73
# contra R$ 1.575.696,96 de saídas de 14 dias — 28 dias de previsto contra 14 de
# pago, e a "Diferença" virava um número sem sentido (−R$ 745.914,77).
# Agora o previsto conta SÓ o que vence dentro do período. A margem continua viva
# onde ela serve: `titulos_db` (e o casamento) segue com os ±7 dias.
def _vence_no_periodo(v) -> bool:
    return bool(v) and d_ini.isoformat() <= str(v)[:10] <= d_fim.isoformat()


_venc_per = [t for t in titulos_db if _vence_no_periodo(t["vencimento"])]
n_venc_per = len(_venc_per)
v_venc_per = float(sum(t["valor"] for t in _venc_per))
tot_cpr = v_venc_per
tot_dif = tot_pago - tot_cpr
k = st.columns(3)
k[0].metric("💸 Total de saídas (fora aplicações)", brl(tot_pago), f"{len(saidas_reais)} saídas")
k[1].metric("📋 Previsto no CPR (vence no período)", brl(tot_cpr),
            f"{n_venc_per} títulos",
            help=f"Títulos do Contas a Pagar com vencimento entre "
                 f"{d_ini.strftime('%d/%m')} e {d_fim.strftime('%d/%m')} — pagos ou "
                 f"não. Pra casar com o pagamento a tela ainda carrega 7 dias a mais "
                 f"pra cada lado ({len(titulos_db)} títulos), mas esse excedente não "
                 f"entra na conta.")
k[2].metric("↔️ Diferença", brl(tot_dif), delta_color="off",
            help="Total pago − o previsto que vence no período. Positivo = paguei mais "
                 "do que vencia (título de outro vencimento, ou coisa fora do CPR). "
                 "Negativo = vencia mais do que saiu (parte não paga, ou paga em "
                 "dinheiro/conta não importada).")

# ─── TODAS AS SAÍDAS (fora aplicações), com flag "está no CPR?" ──────────────
# Mostra TODA saída (menos aplicação/transferência interna) e marca "No CPR?".
# ✅ SÓ quando a saída CASOU de verdade com um título (nome+valor) OU já tem baixa.
# Tudo que não casou = ❌ não está — SEM exceção por categoria (pedido do Filipe):
# a tela é conferência crua do que está e do que não está; nada de "✅" por dedução.
sem_titulo_ids = {s["id"] for s in res["saidas_sem_titulo"]}
cobertos_ids = ({s["id"] for s in saidas_livres if s["id"] not in sem_titulo_ids}
                | usadas_baixa)

linhas_saidas = []
for s in sorted(saidas_reais, key=lambda x: -x["valor"]):
    coberto = s["id"] in cobertos_ids
    linhas_saidas.append({
        "_ok": coberto,
        "_natureza": (not coberto) and _natureza(s["plano"]),
        "Data": pd.to_datetime(s["data"]).strftime("%d/%m/%Y"),
        "Empresa": s["empresa_apelido"], "Valor": s["valor"],
        "Fornecedor/Contraparte": s["contraparte"] or s["descricao"],
        "Categoria": s["plano"],
        "No CPR?": "✅ está" if coberto else "❌ NÃO está"})

st.divider()
n_fora = sum(1 for r in linhas_saidas if not r["_ok"])
v_fora = sum(r["Valor"] for r in linhas_saidas if not r["_ok"])
st.subheader("🧾 Todas as saídas do período (fora aplicações)")
mc = st.columns(3)
mc[0].metric("❌ NÃO está no CPR", brl(v_fora), f"{n_fora} saídas",
             delta_color="off")
mc[1].metric("✅ Está no CPR", brl(tot_pago - v_fora), f"{len(linhas_saidas) - n_fora} saídas",
             delta_color="off")
mc[2].metric("💸 Total (fora aplicações)", brl(tot_pago), f"{len(linhas_saidas)} saídas",
             delta_color="off")
st.caption("Toda saída que saiu das contas (menos aplicação/transferência interna). "
           "**No CPR?**: ✅ **está** = casou com um título do CPR por **nome + valor** "
           "(ou já foi conferida). **❌ NÃO está** = não achei título pra ela.")

_base_df = pd.DataFrame(linhas_saidas)
_col_ordem = [c for c in _base_df.columns if c not in ("_ok", "_natureza")]


def _fatia(mask) -> pd.DataFrame:
    return (_base_df[mask].sort_values("Valor", ascending=False)[_col_ordem]
            if len(_base_df) else _base_df)


def _mostra(dfx) -> None:
    """Desenha a tabela com o dinheiro no padrão BR (R$ 1.234,56), alinhado à direita.
    O `format="R$ %.2f"` do Streamlit escrevia "R$ 4754.79" (ponto decimal, sem milhar)
    e o `format="localized"` come os centavos ("3.947,3") — os dois desalinhavam a
    coluna. O DataFrame original continua NUMÉRICO: é ele que vai pro Excel."""
    st.dataframe(dfx.assign(Valor=dfx["Valor"].map(brl)), hide_index=True,
                 use_container_width=True,
                 column_config={"Valor": st.column_config.TextColumn(
                     width="small", alignment="right"),
                     "Fornecedor/Contraparte": st.column_config.TextColumn(width="large")})


conferir_df = _fatia((~_base_df["_ok"]) & (~_base_df["_natureza"])) if len(_base_df) else _base_df
natureza_df = _fatia(_base_df["_natureza"]) if len(_base_df) else _base_df
dentro_df = _fatia(_base_df["_ok"]) if len(_base_df) else _base_df
v_conf = float(conferir_df["Valor"].sum()) if len(conferir_df) else 0.0
v_nat = float(natureza_df["Valor"].sum()) if len(natureza_df) else 0.0

st.markdown(f"#### 🔎 Fora do CPR — **sem explicação: conferir** · {len(conferir_df)} "
            f"saída(s) · {brl(v_conf)}")
st.caption("É a fila de trabalho: saída que não casou com título **e** não é de uma "
           "categoria que passa por fora do CPR. Se está aqui, ou falta lançar no "
           "Argos, ou o título existe e precisa ser conciliado lá embaixo.")
if len(conferir_df):
    _mostra(conferir_df)
else:
    st.success("Nada pendente de explicação neste período. 🎉")

with st.expander(f"🏦 Fora do CPR — **por natureza** (não passa no Argos) · "
                 f"{len(natureza_df)} saída(s) · {brl(v_nat)}"):
    st.caption("Tarifa/juros, folha via SISPAG, devolução/estorno, taxa de cartão, "
               "amortização de empréstimo e consórcio: pagas direto pelo banco, sem "
               "título no Contas a Pagar. Em julho inteiro essas categorias casaram com "
               "título em ~0% das vezes — por isso saem da fila de conferir. Continuam "
               "contadas como ❌ nos números acima: **nada aqui está marcado como ✅**.")
    _mostra(natureza_df)

with st.expander(f"✅ Está no CPR · {len(dentro_df)} saída(s) · {brl(tot_pago - v_fora)}"):
    _mostra(dentro_df)

saidas_df = _base_df.sort_values(["_ok", "_natureza", "Valor"],
                                 ascending=[True, True, False]).copy()
saidas_df.insert(len(_col_ordem), "Fila",
                 ["✅ está no CPR" if ok else ("🏦 fora por natureza" if nat else "🔎 conferir")
                  for ok, nat in zip(saidas_df["_ok"], saidas_df["_natureza"])])
saidas_df = saidas_df.drop(columns=["_ok", "_natureza"])

# ─── O OUTRO LADO: o que está no CPR e NÃO foi pago (17/08/2026) ─────────────
# Pedido do Filipe: "na visão da conciliação só vemos o referente o que foi pago, mas
# preciso que a planilha gere tbm o que tá no CPR e não está pago nas contas". Até
# aqui a tela inteira (e as 3 abas do Excel) olhavam do lado do EXTRATO — toda saída
# com a flag "No CPR?". O lado do PREVISTO só existia como linha 🔴 lá dentro do
# expander de conciliação, e não ia pro Excel. Agora é lista própria: métricas na
# tela + aba "CPR sem pagamento" na planilha.
#
# ⚠️ AS DUAS REGRAS DA LISTA (17/08/2026, reclamação do Filipe: "não faz sentido eu
# tá puxando um período e vim de outros períodos") — a 1ª versão desta seção herdava
# as janelas do resto da tela e mentia feio:
#   1) SÓ ENTRA TÍTULO QUE VENCE DENTRO DO PERÍODO. O resto da tela busca título por
#      vencimento ±7 dias (margem que o CASAMENTO precisa: paga-se adiantado/atrasado
#      alguns dias). Só que isso jogava aqui, num período de 4 dias, título vencido
#      em 06/08 — cujo pagamento nem estava carregado. MEDIDO em 13–17/08: 254
#      títulos "sem pagamento" viravam 17 com esta regra.
#   2) O PAGAMENTO É PROCURADO EM TODA A BASE, não só no período. "Não foi pago" é
#      uma afirmação sobre a empresa inteira, não sobre a janela aberta na tela:
#      saída de valor IDÊNTICO ao centavo, até 30 dias do vencimento, ainda sem
#      baixa e não usada por outro título aqui. MEDIDO: dos 17 acima, 6 tinham
#      pagamento na base → sobra REAL de 11 títulos (R$ 122.289,50). No mês inteiro
#      (01–17/08): de 144 para 67.
# O que a regra 2 acha vira AVISO na linha (com data e o quanto o nome bate) — nada
# é ligado sozinho, a conciliação continua sendo na mão lá embaixo. Valor exato de
# propósito: com valor aproximado a coluna viraria lixo, igual no `sugerir`.
_hoje = date.today()

_no_periodo = [t for t in res["titulos"]
               if t["_status"] == "sem_saida" and _vence_no_periodo(t["vencimento"])]
_vencs_sem = [t["vencimento"] for t in _no_periodo]
fora_por_tid: dict[int, dict] = {}
if _vencs_sem:
    _ji = (pd.to_datetime(min(_vencs_sem)) - pd.Timedelta(days=30)).date().isoformat()
    _jf = (pd.to_datetime(max(_vencs_sem)) + pd.Timedelta(days=30)).date().isoformat()
    _cond = ["l.tipo='saida'", "l.data BETWEEN ? AND ?"]
    _par = [_ji, _jf]
    if sel_emp != "Todas":
        _cond.append("l.empresa_id=?"); _par.append(emp_por_apelido[sel_emp])
    _por_valor: dict[float, list[dict]] = {}
    for _s in query(f"""SELECT l.id, l.data, l.valor, l.contraparte, l.descricao
                        FROM lancamentos l WHERE {' AND '.join(_cond)}""", tuple(_par)):
        # fora: o que já quitou algum título (baixa gravada) e o que o `casar` já
        # entregou a OUTRO título nesta tela — senão o mesmo pagamento seria
        # oferecido duas vezes.
        if _s["id"] in titulos_da_saida or _s["id"] in usadas_casar:
            continue
        _por_valor.setdefault(round(float(_s["valor"]), 2), []).append(_s)
    for t in _no_periodo:
        melhor = None
        for _s in _por_valor.get(round(float(t["valor"]), 2), []):
            _sim = similaridade(t["fornecedor"], _s["contraparte"] or _s["descricao"] or "")
            # desempate pela DISTÂNCIA ao vencimento: quando o nome não ajuda (o banco
            # escreve a securitizadora e o Argos o fornecedor — ENERGEX × O.S.
            # SECURITIZADORA, DIST. ALAGOANA DE BATERIAS × MOURA MACEIO), o que
            # sobra pra escolher entre dois pagamentos do mesmo valor é a data.
            _d = abs((pd.to_datetime(_s["data"]) - pd.to_datetime(t["vencimento"])).days)
            if _d > 30:
                continue
            if melhor is None or (_sim, -_d) > (melhor[1], -melhor[2]):
                melhor = (_s, _sim, _d)
        if melhor:
            fora_por_tid[t["_tid"]] = {"saida": melhor[0], "sim": melhor[1],
                                       "dias": melhor[2]}


def _texto_fora(inf: dict) -> str:
    """«12/08 · MG VIDROS · mesmo valor · nome 91% · #12984» — o aviso de que existe
    na base um pagamento de valor idêntico, ainda não conciliado."""
    s = inf["saida"]
    d = pd.to_datetime(s["data"]).strftime("%d/%m")
    nome = (s["contraparte"] or s["descricao"] or "—")[:30]
    return f"{d} · {nome} · mesmo valor · nome {inf['sim']:.0%} · #{s['id']}"


linhas_naopago = []
for t in _no_periodo:
    inf = sug_por_tid.get(t["_tid"])
    combo = combo_por_tid.get(t["_tid"])
    fora = fora_por_tid.get(t["_tid"])
    venc = pd.to_datetime(t["vencimento"]) if t["vencimento"] else None
    linhas_naopago.append({
        "_tem_sug": bool(combo or inf),
        "_tem_fora": bool(fora),
        "Vencimento": venc.strftime("%d/%m/%Y") if venc is not None else "—",
        # positivo = vencido há N dias; negativo = ainda vai vencer.
        "Atraso (dias)": (_hoje - venc.date()).days if venc is not None else None,
        "Fornecedor": t["fornecedor"],
        "Tipo": t["tipo_docto"],
        "Documento": t["documento"],
        "Loja": t["loja"],
        "Empresa": t["empresa"],
        "Previsto": t["valor"],
        "💡 Sugestão do sistema": (_texto_combo(combo) if combo else
                                   (_texto_sug(inf) if inf else SEM_SUG)),
        "⚠️ Tem pagamento igual na base": _texto_fora(fora) if fora else SEM_SUG,
    })

# `columns=` explícito: sem título nenhum na fila o DataFrame vazio ainda precisa
# ter as colunas, senão a aba do Excel sai em branco e o `drop` abaixo estoura.
naopago_df = pd.DataFrame(linhas_naopago, columns=[
    "_tem_sug", "_tem_fora", "Vencimento", "Atraso (dias)", "Fornecedor", "Tipo",
    "Documento", "Loja", "Empresa", "Previsto", "💡 Sugestão do sistema",
    "⚠️ Tem pagamento igual na base"])
# 🔴 (o trabalho real) primeiro, ⚠️ (já tem pagamento, falta conciliar) depois —
# dentro de cada fila, do maior valor pro menor.
naopago_df = naopago_df.sort_values(["_tem_fora", "Previsto"], ascending=[True, False])
n_np = len(naopago_df)
v_np = float(naopago_df["Previsto"].sum()) if n_np else 0.0
n_sug = int(naopago_df["_tem_sug"].sum()) if n_np else 0
v_sug = float(naopago_df.loc[naopago_df["_tem_sug"], "Previsto"].sum()) if n_np else 0.0
n_fp = int(naopago_df["_tem_fora"].sum()) if n_np else 0
v_fp = float(naopago_df.loc[naopago_df["_tem_fora"], "Previsto"].sum()) if n_np else 0.0
naopago_df.insert(0, "Fila", ["⚠️ tem pagamento igual · conciliar" if f
                              else "🔴 sem pagamento" for f in naopago_df["_tem_fora"]])
naopago_df = naopago_df.drop(columns=["_tem_sug", "_tem_fora"])
# título sem loja/empresa (é o caso dos da "BRAGA PEÇAS E SERVIÇOS", que não tem
# empresa no app) escrevia a palavra "None" na célula — vazio é vazio.
for _c in ("Tipo", "Documento", "Loja", "Empresa"):
    naopago_df[_c] = naopago_df[_c].fillna("—")

st.divider()
st.subheader(f"📋 Vence de {d_ini.strftime('%d/%m')} a {d_fim.strftime('%d/%m')} e NÃO "
             f"foi pago")
mn = st.columns(4)
mn[0].metric("🔴 Sem pagamento", brl(v_np - v_fp), f"{n_np - n_fp} títulos",
             delta_color="off",
             help="Nenhum pagamento em lugar nenhum da base. É a lista de verdade.")
mn[1].metric("⚠️ Tem pagamento igual na base", brl(v_fp), f"{n_fp} títulos",
             delta_color="off",
             help="Existe uma saída de valor IDÊNTICO, até 30 dias do vencimento, "
                  "ainda sem baixa — não importa em que período ela caiu. "
                  "Provavelmente já foi pago e falta só conciliar.")
mn[2].metric("💡 Com palpite do sistema", brl(v_sug), f"{n_sug} títulos",
             delta_color="off",
             help="O sistema achou um pagamento parecido DENTRO do período (valor "
                  "próximo + nome, ou duas guias que somam exato). Ligue lá embaixo, "
                  "na conciliação.")
mn[3].metric("✅ Com pagamento", brl(v_venc_per - v_np), f"{n_venc_per - n_np} títulos",
             delta_color="off",
             help=f"Dos {n_venc_per} títulos que vencem no período, os que já casaram "
                  f"com um pagamento ou têm baixa gravada.")
st.caption(f"O contrário da lista de cima. Aqui entra **só título que vence entre "
           f"{d_ini.strftime('%d/%m')} e {d_fim.strftime('%d/%m')}** — nada de outro "
           "período — e o pagamento é procurado na **base inteira**, não só no período "
           "aberto: se o dinheiro saiu, ele acha, mesmo que a saída seja de outro dia. "
           "Quem sobra 🔴 é o que realmente não foi pago: ou ainda não pagaram, ou saiu "
           "por uma conta que não importamos, ou foi pago em dinheiro.")
if n_np:
    st.dataframe(naopago_df.assign(Previsto=naopago_df["Previsto"].map(brl)),
                 hide_index=True, use_container_width=True,
                 column_config={
                     "Previsto": st.column_config.TextColumn(width="small",
                                                             alignment="right"),
                     "Atraso (dias)": st.column_config.NumberColumn(width="small"),
                     "Fornecedor": st.column_config.TextColumn(width="large"),
                     "💡 Sugestão do sistema": st.column_config.TextColumn(width="large"),
                     "⚠️ Tem pagamento igual na base": st.column_config.TextColumn(
                         width="large")})
else:
    st.success("Todo título do CPR neste período tem pagamento. 🎉")


def _excel_conc() -> bytes:
    resumo = pd.DataFrame({
        "Indicador": ["Período", "Empresa", "Total de saídas (fora aplicações)",
                      "Previsto no CPR (vence no período)", "Diferença",
                      "Saídas fora do CPR",
                      "  ↳ fora: conferir", "  ↳ fora: por natureza (não passa no Argos)",
                      "  ↳ vencem no período e não foram pagos",
                      "  ↳ destes, SEM pagamento em lugar nenhum",
                      "  ↳ destes, com pagamento igual na base (falta conciliar)",
                      "  ↳ destes, com palpite do sistema"],
        "Valor": [f"{d_ini.strftime('%d/%m/%Y')} a {d_fim.strftime('%d/%m/%Y')}",
                  sel_emp, tot_pago, tot_cpr, tot_dif, v_fora, v_conf, v_nat,
                  v_np, v_np - v_fp, v_fp, v_sug],
        "Qtde": ["", "", len(saidas_reais), n_venc_per, "", n_fora,
                 len(conferir_df), len(natureza_df), n_np,
                 n_np - n_fp, n_fp, n_sug]})
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as xl:
        resumo.to_excel(xl, sheet_name="Resumo", index=False)
        # aba nova: só a fila de trabalho (o que abrir primeiro)
        conferir_df.to_excel(xl, sheet_name="Conferir", index=False)
        # a aba de sempre continua com TUDO, agora com a coluna Fila
        saidas_df.to_excel(xl, sheet_name="Saídas (No CPR)", index=False)
        # 17/08/2026 — o lado do PREVISTO: título no CPR sem pagamento nenhum
        naopago_df.to_excel(xl, sheet_name="CPR sem pagamento", index=False)
    return buf.getvalue()


st.download_button(
    "📥 Baixar Excel", data=_excel_conc(),
    file_name=f"Saidas_x_CPR_{d_ini.strftime('%Y%m%d')}_{d_fim.strftime('%Y%m%d')}.xlsx",
    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

st.divider()
# ─── Detalhe título-a-título — ligar o pagamento a cada título (conciliar) ────
# ABERTO POR PADRÃO (03/08/2026): expander do Streamlit volta a FECHAR a cada rerun —
# ou seja, toda vez que ele salvava uma conciliação (ou trocava um filtro) a seção
# sumia e ele tinha que rolar a página e abrir de novo. É a área de trabalho da tela.
with st.expander(f"🔎 Conferir título por título / conciliar (ligar pagamento) · "
                 f"{len(sug_por_tid)} com sugestão", expanded=True):
    st.caption("**Como usar — dois caminhos:** (1) **em lote**, pela tabela: olhe a "
               "coluna **💡 Sugestão do sistema** (vem o motivo: valor igual ou a "
               "diferença, e o quanto o nome bate), marque **Ligar** nas certas e "
               "clique em salvar; (2) **um a um**, pelo **🎯 Achar o pagamento de um "
               "título** — escolha o título e o sistema lista os pagamentos mais "
               "parecidos **com ele**, do valor mais próximo pro mais distante. "
               "Use o 🎯 quando não houver sugestão: é onde antes você penava no "
               "dropdown. **Pago em duas guias** (ICMS + FECOEP separados)? No 🎯 dá "
               "pra marcar **duas linhas** e ligar as duas ao mesmo título — e quando "
               "as duas somam exato o sistema já avisa. Ordem da tabela: 🔴 com "
               "sugestão no topo, depois 🔴 sem sugestão, 🟡 só valor, 🟢 casados e "
               "conferidos.")

    def _ordem(r) -> int:
        s = r["Status"]
        if s.startswith("🔴"):
            return 0 if r["_score"] >= 0 else 1   # com sugestão primeiro
        if s.startswith("🟡"):
            return 2
        if "conferido" in s:
            return 4
        return 3                          # 🟢 sugerido (casado) — confirmar

    df_ord = (df.assign(_o=df.apply(_ordem, axis=1))
                .sort_values(["_o", "_score", "Previsto"], ascending=[True, False, False])
                .drop(columns=["_o"]))

    # Filtro: ver um grupo de cada vez. "💡 Com sugestão" é o atalho que resolve a
    # queixa — abre só os 🔴 que o sistema já achou um palpite, que é onde dá pra
    # trabalhar em lote (os demais 🔴 dependem de procurar na mão).
    _preds = {
        "💡 Com sugestão": lambda r: r["Status"].startswith("🔴") and r["_score"] >= 0,
        "🔴 Sem pagamento": lambda r: r["Status"].startswith("🔴"),
        "🟡 Só valor (rever)": lambda r: r["Status"].startswith("🟡"),
        "🟢 Casados (sugeridos)": lambda r: r["Status"].startswith("🟢") and "casado" in r["Status"],
        "🟢 Já conferidos": lambda r: "conferido" in r["Status"],
    }
    _mask = {rot: df_ord.apply(pred, axis=1) for rot, pred in _preds.items()}
    _cont = {rot: int(m.sum()) for rot, m in _mask.items()}
    # Rótulos do filtro SEM a contagem — se o número entrasse no texto, a opção mudaria
    # a cada salvamento e o st.radio perderia a seleção (voltava pra "Todos"). A contagem
    # fica numa legenda embaixo; assim o filtro continua no 🔴 depois de salvar.
    rot_sel = st.radio("Ver", ["Todos", *_preds.keys()], horizontal=True, key="conf_filtro")
    st.caption("  ·  ".join([f"Todos: **{len(df_ord)}**"]
                            + [f"{rot}: **{_cont[rot]}**" for rot in _preds]))
    df_grupo = df_ord if rot_sel == "Todos" else df_ord[_mask[rot_sel]]

    if df_grupo.empty:
        st.info("Nenhum título nesse filtro.")
        st.stop()

    # ─── PÁGINAS de 50 (03/08/2026) — por que existe ─────────────────────────
    # Filipe: "antes era tudo organizado e agora tá bagunçado" (print do dropdown com as
    # opções fora de qualquer ordem). MEDIDO: com o mês inteiro em "Todos" a coluna
    # Pagamento tinha **1.079 opções, 928 delas vindas de um `set`** (o `em_uso`, que
    # garante que o rótulo já escolhido continue na lista) — e set em Python NÃO tem
    # ordem, então aquela cauda saía embaralhada. Além de bagunçar, furava o teto de
    # 150 que existe justamente pra tela não congelar.
    # Com 50 títulos por página, o `em_uso` cai pra no máximo 50 e a lista fica curta e
    # ORDENADA (ver a ordenação do `opcoes` mais abaixo).
    PAG_TAM = 50
    n_pag = (len(df_grupo) + PAG_TAM - 1) // PAG_TAM
    if n_pag > 1:
        cpg = st.columns([1, 3])
        pag = cpg[0].number_input(f"Página (de {n_pag})", min_value=1, max_value=n_pag,
                                  value=1, step=1, key=f"conf_pag_{rot_sel}")
        ini = (int(pag) - 1) * PAG_TAM
        df_view = df_grupo.iloc[ini:ini + PAG_TAM]
        cpg[1].caption(f"Mostrando **{ini + 1}–{min(ini + PAG_TAM, len(df_grupo))}** "
                       f"de **{len(df_grupo)}** títulos. ⚠️ **Salve antes de trocar de "
                       "página** — o botão grava só o que está na página aberta.")
    else:
        pag = 1
        df_view = df_grupo

    # ─── 🎯 ACHAR O PAGAMENTO DE UM TÍTULO (03/08/2026) ───────────────────────
    # Por que existe: o dropdown da coluna Pagamento é UM SÓ pra tabela inteira
    # (limitação do SelectboxColumn do Streamlit) — ele não sabe de QUAL título é a
    # linha, então não tem como colocar o pagamento certo em cima. Por isso o Filipe
    # via, num título de R$ 2.926, uma lista começando em R$ 19.866: "não consigo
    # encontrar ou demoro muito para encontrar".
    # Aqui é o caminho inverso e é o jeito rápido: escolhe-se O TÍTULO (o selectbox
    # aceita digitar pra procurar) e o sistema traz os pagamentos MAIS PARECIDOS COM
    # ELE — do valor mais próximo pro mais distante, com a diferença e o quanto o nome
    # bate escritos na linha. Clica na linha certa e liga; grava na hora, sem depender
    # do "Salvar conciliação" da tabela de baixo.
    _pend = df_grupo[~df_grupo["Status"].str.contains("conferido")]
    with st.container(border=True):
        st.markdown("#### 🎯 Achar o pagamento de um título")
        if _pend.empty:
            st.success("Nenhum título pendente neste filtro. 🎉")
        else:
            _op_tit = {}
            for _, _r in _pend.iterrows():
                _rot = (f"{brl(_r['Previsto'])} · venc {_r['Venc. · Tipo']} · "
                        f"{_r['Fornecedor']} · nº {int(_r['_tid'])}")
                _op_tit[_rot] = int(_r["_tid"])
            _rot_tit = st.selectbox(
                f"Título a conciliar — **{len(_op_tit)}** pendente(s) neste filtro "
                "(pode digitar parte do nome ou do valor pra procurar)",
                list(_op_tit), key=f"conf_tit_{rot_sel}")
            tid_sel = _op_tit[_rot_tit]
            t_sel = next(t for t in titulos_db if t["id"] == tid_sel)

            # ─── ATALHO: este título foi pago em DUAS guias? (04/08/2026) ────
            # Quando o `sugerir_combos` acha o par (ICMS + FECOEP do mesmo dia
            # somando exato), ele aparece aqui pronto pra ligar de uma vez.
            _cb = combo_por_tid.get(tid_sel)
            if _cb:
                _dc = pd.to_datetime(_cb["dia"]).strftime("%d/%m")
                _det = " + ".join(
                    f"**{brl_md(s['valor'])}** ({(s['contraparte'] or s['descricao'] or '')[:26]})"
                    for s in _cb["saidas"])
                st.info(f"💡 **Parece pago em 2 guias no dia {_dc}:** {_det} = "
                        f"**{brl_md(sum(s['valor'] for s in _cb['saidas']))}**, exatamente o "
                        f"valor do título. (É o caso do ICMS + FECOEP.)")
                if st.button("🔗 Ligar os 2 pagamentos a este título", type="primary",
                             key=f"conf_ligar_combo_{tid_sel}"):
                    baixas.ligar(tid_sel, _cb["saidas"])
                    st.success("Conciliado com os 2 pagamentos! O título saiu da fila.")
                    st.rerun()

            # saída que o `casar` já apontou pra OUTRO título: não some da lista (pode
            # ser justamente a certa aqui), mas vem marcada pra ele saber.
            _usado_por = {t["_saida"]["id"] for t in res["titulos"] if t["_saida"]}
            _co = st.columns([2, 1, 1])
            _ordem = _co[0].radio("Ordenar os pagamentos por",
                                  ["💰 valor mais próximo", "🔤 nome mais parecido"],
                                  horizontal=True, key="conf_cand_ordem")
            # MEDIDO nos dados de julho: sem esse filtro, os 4 primeiros candidatos de
            # um título de R$ 2.926 eram "RESGATE CONTA REMUNERADA", "DÉBITO NA CONTA
            # CORRENTE" e transferência interna — dinheiro andando entre as contas do
            # grupo, que nunca quita título do Argos. Fora por padrão; se ele
            # desconfiar que a saída foi classificada errado, marca a caixinha.
            _tudo = _co[1].checkbox("Incluir aplicações e transferências internas",
                                    key="conf_cand_tudo")
            # GUIA JUNTA (04/08/2026): quando o banco paga ICMS+FECOEP numa guia só,
            # aquele ÚNICO pagamento pode quitar MAIS DE UM título do Argos. Pagamento
            # já conciliado fica fora da lista por padrão (senão os 25 candidatos de um
            # título comum viriam quase todos já usados); marcando aqui, ele aparece
            # com o nº do título que já quitou.
            _reusar = _co[2].checkbox("Incluir pagamentos já conciliados",
                                      key="conf_cand_reusar",
                                      help="Pro caso da GUIA JUNTA: um pagamento só "
                                           "que quita dois títulos do CPR.")
            _base_pool = saidas if _reusar else saidas_livres
            _pool = [s for s in _base_pool
                     if _tudo or not any(k in _norm(s["plano"] or "")
                                         for k in _EXCLUIR_PAGO)]
            _alvo_v = float(t_sel["valor"])
            _cand = [(round(s["valor"] - _alvo_v, 2),
                      similaridade(t_sel["contraparte"] or "",
                                   s["contraparte"] or s["descricao"] or ""), s)
                     for s in _pool]
            _cand.sort(key=(lambda c: (abs(c[0]), -c[1])) if _ordem.startswith("💰")
                       else (lambda c: (-c[1], abs(c[0]))))
            _cand = _cand[:25]
            # AVISO DE BUSCA VAZIA: às vezes não existe pagamento nenhum parecido — o
            # título simplesmente não foi pago no período. Dizer isso na cara poupa o
            # tempo que ele gastava rolando o dropdown atrás de algo que não existe.
            if _cand and not any(abs(d) <= 0.01 or sm >= 0.60 for d, sm, _ in _cand):
                st.warning(f"⚠️ **Nenhum pagamento bate com este título** no período: o "
                           f"valor mais próximo está a {brl(abs(_cand[0][0]))} de distância e o nome "
                           f"mais parecido dá {max(sm for _, sm, _ in _cand):.0%}. "
                           "Provavelmente este título **não foi pago** nestas datas (ou "
                           "foi pago por uma conta que ainda não foi importada).")
            def _situacao(s: dict) -> str:
                outros = [t for t in titulos_da_saida.get(s["id"], []) if t != tid_sel]
                if outros:
                    return "🔗 já quita o título nº " + ", ".join(str(o) for o in outros)
                return "🔗 já sugerido a outro" if s["id"] in _usado_por else "🆓 livre"

            _cdf = pd.DataFrame([{
                "Data": pd.to_datetime(s["data"]).strftime("%d/%m"),
                "Valor pago": s["valor"],
                "Δ p/ o título": brl(dif),
                "Favorecido no extrato": s["contraparte"] or s["descricao"],
                "Nome bate": f"{sim:.0%}",
                "Empresa": s["empresa_apelido"],
                "Situação": _situacao(s),
                "Nº": s["id"]} for dif, sim, s in _cand])
            _cdf["Valor pago"] = brl_ord(_cdf["Valor pago"])
            # MULTI-SELEÇÃO (04/08/2026): marcar DUAS linhas liga as duas guias ao
            # mesmo título — é o pedido do ICMS+FECOEP pagos separados.
            _ev = st.dataframe(
                _cdf, hide_index=True, use_container_width=True,
                on_select="rerun", selection_mode="multi-row",
                key=f"conf_cand_{tid_sel}",
                column_config={
                    "Valor pago": st.column_config.TextColumn(width="small",
                                                              alignment="right"),
                    "Δ p/ o título": st.column_config.TextColumn(
                        width="small", alignment="right",
                        help="Pago − previsto. Zero = valor igualzinho ao do título."),
                    "Favorecido no extrato": st.column_config.TextColumn(width="large"),
                    "Nome bate": st.column_config.TextColumn(width="small"),
                    "Situação": st.column_config.TextColumn(width="medium")})
            _sel = list(_ev.selection.rows) if _ev and _ev.selection else []
            if _sel:
                _escs = [_cand[i][2] for i in _sel]
                _soma = sum(s["valor"] for s in _escs)
                _delta = round(_soma - float(t_sel["valor"]), 2)
                _desc = " + ".join(
                    f"**{brl_md(s['valor'])}** de {pd.to_datetime(s['data']).strftime('%d/%m')} "
                    f"({(s['contraparte'] or s['descricao'] or '')[:24]})" for s in _escs)
                st.warning(f"**{t_sel['contraparte']}** · previsto "
                           f"{brl_md(t_sel['valor'])} → {_desc}"
                           + (f" = **{brl_md(_soma)}**" if len(_escs) > 1 else "")
                           + f" · Δ {brl_md(_delta)}"
                           + ("  ✅ **fecha exato**" if abs(_delta) <= 0.01 else ""))
                # já conciliado noutro título: pode ser a guia junta, mas ele tem que
                # ver isso escrito antes de gravar (nada some sem aviso).
                _reuso = [(s, [t for t in titulos_da_saida.get(s["id"], []) if t != tid_sel])
                          for s in _escs]
                _reuso = [(s, o) for s, o in _reuso if o]
                if _reuso:
                    st.info("ℹ️ " + " · ".join(
                        f"O pagamento #{s['id']} já quita o(s) título(s) nº "
                        + ", ".join(str(x) for x in o) for s, o in _reuso)
                        + ". Ligando aqui também, ele passa a quitar os dois (é o caso "
                          "da **guia junta**). A baixa do outro título continua de pé.")
                _rot_bt = ("🔗 Ligar este pagamento ao título" if len(_escs) == 1
                           else f"🔗 Ligar os {len(_escs)} pagamentos a este título")
                if st.button(_rot_bt, type="primary", key="conf_ligar_um"):
                    baixas.ligar(tid_sel, _escs)
                    st.success(f"Conciliado com {len(_escs)} pagamento(s)! "
                               "O título já saiu da fila.")
                    st.rerun()
            else:
                st.caption("👆 Clique na **caixinha à esquerda** da linha do pagamento "
                           "certo — aí aparece o botão pra ligar. Pode marcar **mais de "
                           "uma** (ICMS + FECOEP pagos em guias separadas): o sistema "
                           "soma e mostra se fecha o valor do título. Nada é ligado sozinho.")

    # ─── Opções do dropdown "Pagamento" (busca + teto — ver MAX_OPCOES) ───────
    busca_pg = st.text_input(
        "🔎 Procurar o pagamento (nome do favorecido ou valor)",
        key="conf_busca_pg", placeholder="ex.:  macdiesel   ·   74.500   ·   #5934",
        help="Digite parte do nome ou do valor da saída que quitou o título. "
             "Isso enxuga a lista do dropdown da coluna Pagamento — sem isso, um mês "
             "inteiro traz quase 2.000 opções e o navegador trava.")

    # BUSCA POR CAMPO (03/08/2026) — por que mudou:
    # a versão antiga comparava os dígitos do TEXTO INTEIRO do rótulo. Resultado
    # medido no print do Filipe: procurando «290» ela devolvia 60 "achados" que eram
    # a DATA ("29/07"), o Nº DO LANÇAMENTO ("#10290") e pedaços do histórico — o
    # pagamento de R$ 2.900 que ele queria ficava enterrado no meio do lixo.
    # Agora cada coisa procura no seu campo: número → só o VALOR; "#123" → só o id;
    # texto → só o nome do favorecido.
    _alvo = busca_pg.strip()
    _alvo_dig = "".join(c for c in _alvo if c.isdigit())
    _so_numero = bool(_alvo_dig) and not any(c.isalpha() for c in _alvo)

    def _casa_busca(s: dict) -> bool:
        if not _alvo:
            return True
        if _alvo.startswith("#"):
            return bool(_alvo_dig) and _alvo_dig in str(s["id"])
        if _so_numero:
            # "2926" acha R$ 2.926,10 · "290" acha R$ 2.900,00 e R$ 290,00
            return _alvo_dig in "".join(c for c in brl(s["valor"]) if c.isdigit())
        return _norm(_alvo) in _norm(s["contraparte"] or s["descricao"] or "")

    # os rótulos JÁ mostrados nas células precisam estar nas opções, senão o Streamlit
    # descarta o valor da célula e um título conferido volta a aparecer como "não pago"
    em_uso = {r for r in df_view["Pagamento"] if r != OP_NENHUM}
    # ...e o mesmo vale pras escolhas AINDA NÃO SALVAS: se ele atrelar um pagamento e
    # só depois digitar na busca, o rótulo escolhido pode cair fora do filtro — sem
    # isso ele sumiria da célula justamente antes de salvar.
    _est = st.session_state.get(f"conf_editor_{rot_sel}_{pag}") or {}
    for _ed in (_est.get("edited_rows") or {}).values():
        _r = _ed.get("Pagamento")
        if _r and _r != OP_NENHUM:
            em_uso.add(_r)
    livres_ids = {s["id"] for s in saidas_livres}
    cands = [s for s in saidas if _casa_busca(s)]
    # ainda não conciliadas primeiro (são as que faltam ligar), maiores no topo
    cands.sort(key=lambda s: (s["id"] not in livres_ids, -abs(s["valor"])))
    achados = len(cands)

    # ORDEM DA LISTA (03/08/2026): a lista sai SEMPRE do maior valor pro menor — a
    # mesma ordem do rótulo, que agora começa pelo valor. Antes era por data e o
    # `em_uso` entrava no fim direto de um `set` (sem ordem nenhuma): era a bagunça
    # do print. Aqui os dois grupos (candidatos do teto + rótulos já escolhidos na
    # página) viram um conjunto de IDs e são ordenados JUNTOS.
    ids_opcao = {s["id"] for s in cands[:MAX_OPCOES]} | {id_por_rotulo[r] for r in em_uso
                                                        if r in id_por_rotulo}
    # os rótulos de título pago em 2+ guias ("🔗 2 pagamentos · … · nº 1049") não têm
    # id de lançamento — entram à parte, senão a célula do título já conferido em duas
    # guias apareceria VAZIA e o Salvar leria como "não pago" (o bug de 03/08).
    multi_na_pagina = [r for r in df_view["Pagamento"] if r in tid_do_rotulo_multi]
    opcoes = ([OP_NENHUM] + sorted(set(multi_na_pagina))
              + [rotulo_por_id[s["id"]] for s in
                 sorted((s for s in sd_por_id.values() if s["id"] in ids_opcao),
                        key=lambda s: (abs(s["valor"]), s["data"]), reverse=True)])

    if achados > MAX_OPCOES:
        st.caption(f"A coluna **Pagamento** está com **{len(opcoes) - 1}** das "
                   f"{achados} saídas do período, **do maior valor pro menor**. "
                   "Pra achar uma específica **digite o valor na busca acima** "
                   "(ex.: `2926`) — a lista inteira no navegador é o que trava a tela. "
                   "Ou use o **🎯 Achar o pagamento** logo acima, que procura sozinho.")
    elif _alvo:
        st.caption(f"🔎 **{achados}** pagamento(s) com "
                   + (f"valor contendo **{_alvo_dig}**" if _so_numero
                      else f"nome «**{_alvo}**»") + ".")

    # ─── ARRUMAR A TABELA ANTES DE DESENHAR (03/08/2026) ─────────────────────
    # (1) A coluna Δ vira TEXTO: como número, a célula vazia (título ainda sem
    #     pagamento) era desenhada pelo Streamlit como a palavra **"None"** em todas
    #     as linhas 🔴 — foi o que apareceu no print do Filipe. Como texto, vazio é
    #     vazio de verdade.
    # (2) Se NENHUMA linha da página tem sugestão, as colunas 💡 e Ligar só roubam
    #     largura do Fornecedor e do Pagamento — que é onde ele lê e escolhe. Some
    #     com elas; era por isso que "COMPANHIA BRASILEIRA DE DISTRI" vinha cortado.
    df_view = df_view.copy()
    df_view["Δ (pago-prev)"] = ["" if pd.isna(v) else brl(v)
                                for v in df_view["Δ (pago-prev)"]]
    df_view["Previsto"] = brl_ord(df_view["Previsto"])
    if not (df_view["💡 Sugestão do sistema"] != SEM_SUG).any():
        df_view = df_view.drop(columns=["💡 Sugestão do sistema", "Ligar"])
    # Δ só faz sentido quando existe pagamento ligado; vazia em todas as linhas ela só
    # rouba largura de quem interessa.
    if not (df_view["Δ (pago-prev)"] != "").any():
        df_view = df_view.drop(columns=["Δ (pago-prev)"])

    _cfg = {
        "_tid": None, "_score": None,
        "Status": st.column_config.TextColumn(disabled=True, width=85),
        "Fornecedor": st.column_config.TextColumn(disabled=True, width=195),
        "Venc. · Tipo": st.column_config.TextColumn(disabled=True, width=130),
        "Previsto": st.column_config.TextColumn(disabled=True, width=115,
                                                alignment="right"),
        "💡 Sugestão do sistema": st.column_config.TextColumn(
            disabled=True, width=230,
            help="O pagamento mais parecido com ESTE título: mesma faixa de valor "
                 "(±5%) e o nome mais próximo. Vem o motivo: 'valor igual' ou a "
                 "diferença em reais, e o quanto o nome bate. Quando aparece "
                 "'2 pagamentos somam exato', são DUAS guias do mesmo dia (ICMS + "
                 "FECOEP) que fecham o valor do título. Nada é ligado sozinho "
                 "— você marca Ligar e salva."),
        "Ligar": st.column_config.CheckboxColumn(
            width=45, help="Marque pra gravar a baixa com o pagamento sugerido (ou "
                                "com as DUAS guias, quando a sugestão for de par). "
                                "Se você escolher algo na coluna Pagamento, aquela "
                                "escolha manda e o Ligar é ignorado."),
        "Pagamento": st.column_config.SelectboxColumn(
            options=opcoes, width=190,
            help="A lista vem do MAIOR valor pro menor e o rótulo começa pelo valor. "
                 "Pra achar rápido, use o 🎯 acima ou digite o valor na busca."),
        "Δ (pago-prev)": st.column_config.TextColumn(disabled=True, width=90,
                                                     alignment="right"),
    }
    editado = st.data_editor(
        df_view, hide_index=True, use_container_width=True,
        key=f"conf_editor_{rot_sel}_{pag}",   # por filtro E por página: edição não vaza
        column_config={c: v for c, v in _cfg.items()
                       if c in df_view.columns or v is None},
    )

    MANTER = "manter"   # a célula mostra o conjunto de 2+ guias: não mexer nele

    def _escolhido(r):
        """O que vai ser gravado nessa linha. Devolve:
             • lista de saídas  → é isso que o título passa a ter como baixa;
             • MANTER           → a célula é o rótulo do conjunto já gravado (2+ guias);
             • None             → nada escolhido (ou "— (não pago)").
        Ordem: o dropdown vence; senão, o que o **Ligar** propõe (o par de guias tem
        prioridade sobre o palpite 1-p/-1, porque a soma dele é exata)."""
        rot = r["Pagamento"]
        if rot in tid_do_rotulo_multi:
            return MANTER if tid_do_rotulo_multi[rot] == r["_tid"] else None
        sid = id_por_rotulo.get(rot)
        if sid:
            return [sd_por_id[sid]]
        if r.get("Ligar"):
            combo = combo_por_tid.get(r["_tid"])
            if combo:
                return list(combo["saidas"])
            inf = sug_por_tid.get(r["_tid"])
            return [inf["saida"]] if inf else None
        return None

    # Feedback ao vivo: a coluna Status é calculada no load e SÓ muda depois de salvar.
    # Aqui conta quantos títulos já têm um pagamento escolhido mas ainda não gravado,
    # pra não parecer que "atrelar o pagamento não fez nada".
    prontos = sum(1 for _, r in editado.iterrows()
                  if isinstance(_escolhido(r), list) and "conferido" not in r["Status"])
    if prontos:
        st.info(f"🔗 **{prontos}** título(s) com pagamento escolhido, aguardando salvar. "
                "A bolinha só vira 🟢 (e o título sai da lista dos 🔴) **depois** que você "
                "clicar em salvar.")

    if st.button("💾 Salvar conciliação", type="primary"):
        novos_vinc = {}   # tid -> [saídas] | MANTER | None
        for _, r in editado.iterrows():
            novos_vinc[r["_tid"]] = _escolhido(r)

        # MESMO PAGAMENTO EM DOIS TÍTULOS (04/08/2026): antes era ERRO e barrava o
        # salvamento inteiro. Agora é permitido — é a **guia junta** que quita dois
        # títulos do CPR — mas vem escrito na tela, com a conta aberta, porque também
        # é a cara de um engano.
        usados = [s["id"] for v in novos_vinc.values() if isinstance(v, list) for s in v]
        dups = sorted({x for x in usados if usados.count(x) > 1})
        if dups:
            _txt = []
            for sid in dups:
                _tids = [t for t, v in novos_vinc.items()
                         if isinstance(v, list) and any(s["id"] == sid for s in v)]
                _soma = sum(float(t["valor"]) for t in titulos_db if t["id"] in _tids)
                _pg = sd_por_id[sid]["valor"]
                _txt.append(f"pagamento #{sid} ({brl_md(_pg)}) → títulos "
                            + ", ".join(f"nº {t}" for t in _tids)
                            + f" = {brl_md(_soma)}"
                            + ("  ✅ fecha" if abs(_soma - _pg) <= 0.01
                               else f"  ⚠️ sobra/falta {brl_md(_pg - _soma)}"))
            st.warning("⚠️ **Um pagamento quitando mais de um título** (guia junta): "
                       + " · ".join(_txt))

        n_lig = n_desl = 0
        for tid, esc in novos_vinc.items():
            if esc is MANTER:
                continue
            atuais = baixas_por_tid.get(tid, [])
            if isinstance(esc, list):
                if [s["id"] for s in esc] == atuais:
                    continue                      # já é exatamente isso: não reescreve
                baixas.ligar(tid, esc)
                n_lig += 1
            else:
                if not atuais:
                    continue
                # TRAVA (03/08/2026): só desfaz baixa se o pagamento atual estava mesmo na
                # lista de opções — ou seja, se ele PODIA ter escolhido "— (não pago)" de
                # propósito. Sem isso, qualquer título cuja saída não coubesse na lista
                # (fora do período, fora do teto de opções) era desligado sozinho no Salvar.
                # (04/08: vale também pro conjunto de 2+ guias, cujo rótulo é o "🔗 …".)
                _rot_atual = (_rotulo_multi(tid, [sd_por_id[l] for l in atuais if l in sd_por_id])
                              if len(atuais) > 1 else rotulo_por_id.get(atuais[0]))
                if _rot_atual not in opcoes:
                    continue
                baixas.desligar(tid)
                n_desl += 1
        st.success(f"Conciliado! {n_lig} baixa(s) nova(s)"
                   + (f" · {n_desl} desfeita(s)" if n_desl else "") + ".")
        st.rerun()
