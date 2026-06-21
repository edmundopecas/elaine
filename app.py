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
saidas = st.Page("views/saidas.py", title="Saídas", icon="💸", default=True)
entradas = st.Page("views/entradas.py", title="Entradas", icon="💰")
painel = st.Page("views/apresentacao.py", title="Painel de Saídas", icon="📊")
painel_ent = st.Page("views/painel_entradas.py", title="Painel de Entradas", icon="📈")
comparativo = st.Page("views/comparativo.py", title="Comparativo", icon="⚖️")
transfer = st.Page("views/transferencias.py", title="Transferências entre Contas", icon="🔄")
contas = st.Page("views/contas.py", title="Contas Bancárias", icon="🏦")
base = st.Page("views/base_tipos.py", title="Base de Tipos", icon="📒")

pg = st.navigation([importar, boletos, saidas, entradas, painel, painel_ent,
                    comparativo, transfer, contas, base])
pg.run()
