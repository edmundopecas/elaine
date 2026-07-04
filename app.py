"""
Elaine — Financeiro (foco nas Saídas).
Roteador das páginas. A ordem do menu é definida aqui: **Importar Extrato** vem
primeiro (é o primeiro passo do fluxo), mas o app abre direto em **Saídas**.
"""
from __future__ import annotations

import streamlit as st

from auth import exigir_login
from db import init_db

st.set_page_config(page_title="Elaine — Financeiro", page_icon="💸", layout="wide")
exigir_login()      # bloqueia até autenticar (no-op em dev local, sem usuários)
init_db()

importar = st.Page("views/importar.py", title="Importar Extrato", icon="📥")
boletos = st.Page("views/boletos_dda.py", title="Detalhar Boletos DDA", icon="📄")
diretoria = st.Page("views/diretoria.py", title="Visão da Diretoria", icon="📋")
saidas = st.Page("views/saidas.py", title="Saídas", icon="💸", default=True)
entradas = st.Page("views/entradas.py", title="Entradas", icon="💰")
pendencias = st.Page("views/pendencias.py", title="Pendências", icon="⏳")
painel = st.Page("views/apresentacao.py", title="Painel de Saídas", icon="📊")
painel_ent = st.Page("views/painel_entradas.py", title="Painel de Entradas", icon="📈")
analise_fat = st.Page("views/faturamento.py", title="Análise do Faturamento", icon="🔎")
comparativo = st.Page("views/comparativo.py", title="Comparativo", icon="⚖️")
conferencia = st.Page("views/conferencia.py", title="Conferência Contas a Pagar", icon="🔗")
emprestimos = st.Page("views/emprestimos.py", title="Empréstimos", icon="🏦")
transfer = st.Page("views/transferencias.py", title="Transferências entre Contas", icon="🔄")
contas = st.Page("views/contas.py", title="Contas Bancárias", icon="🏦")
base = st.Page("views/base_tipos.py", title="Base de Tipos", icon="📒")

# Menu em seções: a de cima é o destaque gerencial (Elaine começa por aqui);
# a de baixo é o operacional do dia a dia. Saídas segue como página default.
pg = st.navigation({
    "⭐ Para a Diretoria": [diretoria, comparativo],
    "Operação": [saidas, entradas, pendencias, importar, boletos, conferencia,
                 painel, painel_ent, analise_fat, emprestimos, transfer, contas, base],
})
pg.run()
