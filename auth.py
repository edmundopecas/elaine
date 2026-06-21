"""
Login simples pro app publicado. Usuários e senhas (hash SHA-256) ficam no
`.streamlit/secrets.toml`, seção [usuarios]:

    [usuarios.filipe]
    nome = "Filipe"
    senha_hash = "<hash sha256 da senha>"

    [usuarios.diretoria]
    nome = "Diretoria"
    senha_hash = "..."

Pra gerar o hash de uma senha:
    python auth.py "minhaSenha123"

Em DEV local (sem a seção [usuarios] no secrets) o login fica DESATIVADO, pra não
atrapalhar o fluxo de importação/classificação na máquina.
"""
from __future__ import annotations

import hashlib
import sys


def _hash(senha: str) -> str:
    return hashlib.sha256(senha.encode("utf-8")).hexdigest()


def _usuarios() -> dict:
    try:
        import streamlit as st
        return dict(st.secrets.get("usuarios", {}))
    except Exception:
        return {}


def exigir_login() -> None:
    """Bloqueia o app até autenticar. No-op se não houver usuários configurados."""
    import streamlit as st

    usuarios = _usuarios()
    if not usuarios:                       # dev local: sem login
        return

    if st.session_state.get("auth_ok"):
        with st.sidebar:
            st.caption(f"👤 {st.session_state.get('auth_nome', '')}")
            if st.button("Sair", use_container_width=True):
                st.session_state.clear()
                st.rerun()
        return

    st.title("🔒 Elaine — Financeiro")
    st.caption("Acesso restrito. Entre com seu usuário e senha.")
    with st.form("login"):
        user = st.text_input("Usuário").strip().lower()
        pwd = st.text_input("Senha", type="password")
        ok = st.form_submit_button("Entrar", use_container_width=True)
    if ok:
        u = usuarios.get(user)
        if u and _hash(pwd) == str(u.get("senha_hash", "")):
            st.session_state["auth_ok"] = True
            st.session_state["auth_nome"] = u.get("nome", user)
            st.rerun()
        else:
            st.error("Usuário ou senha inválidos.")
    st.stop()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit('Uso: python auth.py "<senha>"  ->  imprime o hash pro secrets.toml')
    print(_hash(sys.argv[1]))
