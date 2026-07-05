"""
Motor da **Conferência Contas a Pagar × Pagamentos**.

Ideia: a planilha "A Pagar Geral" do Argos é o PREVISTO (cada linha = um título a
pagar); as saídas do extrato são o REALIZADO. Aqui a gente:
  1) lê a planilha (parse_a_pagar) — acha o cabeçalho sozinho e joga fora o
     rodapé de TOTAL (a armadilha que dobra a soma);
  2) casa cada título com uma saída (casar) por **valor arredondado a reais**
     (pedido do Filipe: ignora centavos) **+ nome do fornecedor** (fuzzy),
     carimbando FORTE / VALOR (nome fraco) / SEM_SAÍDA.

O vínculo em si mora na tabela `titulos` (coluna `lancamento_id` = a baixa);
este módulo só é a lógica pura, sem tocar no banco — a tela `views/conferencia.py`
faz a persistência. Assim dá pra testar por linha de comando.
"""
from __future__ import annotations

import hashlib
import re
import unicodedata
from difflib import SequenceMatcher

import pandas as pd


# ─── Helpers de texto ────────────────────────────────────────────────────────
def _norm(s) -> str:
    s = "" if s is None else str(s)
    s = unicodedata.normalize("NFKD", s).encode("ASCII", "ignore").decode()
    return re.sub(r"\s+", " ", s).strip().lower()


def _digitos(s) -> str:
    return re.sub(r"\D", "", str(s or ""))


def bucket_categoria(texto: str) -> str:
    """Agrupa tipo_docto (CPR) e plano de contas (banco) nos MESMOS grupos, pra casar
    por categoria. Ex.: CPR 'PESSOAL' e banco 'Férias'/'Salários' caem em 'Folha e
    Pessoal'; CPR 'MERCADORIA' e banco 'Custo de Mercadoria' em 'Mercadoria'."""
    n = _norm(texto or "")
    if any(k in n for k in ("mercadoria", "cmv", "frete", "dacte")):
        return "Mercadoria"
    if any(k in n for k in ("pessoal", "pagamentos fun", "folha", "salario", "ferias",
                            "mao de obra", "13", "rescis", "pensao", "adiantament")):
        return "Folha e Pessoal"
    if any(k in n for k in ("energia", "agua")):
        return "Energia e Água"
    if any(k in n for k in ("constru", "obra", "reforma")):
        return "Construção"
    if any(k in n for k in ("icms", "tributo", "imposto", "gnre", "fecoep", "pis",
                            "cofins", "fgts", "gps", "inss")):
        return "Tributos"
    return "Outros / Administrativo"


# Palpite loja do Argos → apelido de empresa do app. O casamento real é por
# valor+nome; a loja é só um sinal fraco (e segmentação na tela). Braga não é
# empresa do app → fica sem empresa. Ajustável sem medo.
LOJA_EMPRESA = {
    "edmundo pecas e servicos": "Edmundo Matriz",
    "edmundo auto pecas e servicos": "Edmundo Filial",
}


def empresa_da_loja(loja: str) -> str | None:
    n = _norm(loja)
    for chave, apelido in LOJA_EMPRESA.items():
        if chave in n:
            return apelido
    return None


# ─── Leitura da planilha "A Pagar Geral" ─────────────────────────────────────
def _achar_cabecalho(bruto: pd.DataFrame) -> int | None:
    """Linha do cabeçalho = a que tem 'Fornecedor' e 'Vencimento'."""
    for i in range(min(15, len(bruto))):
        celulas = [_norm(x) for x in bruto.iloc[i].tolist()]
        tem_forn = any("fornecedor" in c for c in celulas)
        tem_venc = any("vencimento" in c for c in celulas)
        if tem_forn and tem_venc:
            return i
    return None


def _coluna(cols, *chaves: str):
    for c in cols:
        n = _norm(c)
        if any(k in n for k in chaves):
            return c
    return None


def _linha_hash(fornecedor: str, documento: str, vencimento: str, valor: float) -> str:
    base = f"argos|{_norm(fornecedor)}|{_norm(documento)}|{vencimento}|{valor:.2f}"
    return hashlib.sha256(base.encode()).hexdigest()


def parse_a_pagar(file) -> list[dict]:
    """
    Lê a 'A Pagar Geral' (xlsx). Aceita as variações de export (com ou sem as 4
    colunas em branco na frente, com 'Mês Ref'/'Operação Desc.' a mais).
    Devolve um título por linha: fornecedor, vencimento (ISO), documento,
    tipo_docto, valor, valor_aberto, loja, empresa (palpite), historico, linha_hash.
    """
    bruto = pd.read_excel(file, header=None)
    hi = _achar_cabecalho(bruto)
    if hi is None:
        raise ValueError("Não achei o cabeçalho (esperava colunas 'Fornecedor' e "
                         "'Vencimento'). Confirme que é o relatório A Pagar Geral.")

    df = pd.read_excel(file, header=hi)
    cols = list(df.columns)
    c_forn = _coluna(cols, "fornecedor")
    c_venc = _coluna(cols, "vencimento")
    c_doc = _coluna(cols, "documento")
    c_orig = _coluna(cols, "valor original", "valor orig")
    c_abert = _coluna(cols, "valor em abert", "aberto")
    c_tipo = _coluna(cols, "tipo docto", "tipo doc")
    c_loja = _coluna(cols, "loja")
    c_hist = _coluna(cols, "historico", "histórico")
    c_val = c_orig or c_abert  # casa pelo valor de face; sem original usa aberto
    if not (c_forn and c_venc and c_val):
        raise ValueError("Faltam colunas essenciais (Fornecedor / Vencimento / Valor).")

    # ARMADILHA: rodapé de TOTAL não tem fornecedor → dropna mata o total duplo.
    df = df.dropna(subset=[c_forn, c_val]).copy()
    df["_valor"] = pd.to_numeric(df[c_val], errors="coerce")
    df = df[df["_valor"].notna() & (df["_valor"] > 0)]

    titulos: list[dict] = []
    for _, r in df.iterrows():
        venc = pd.to_datetime(r[c_venc], dayfirst=True, errors="coerce")
        venc_iso = venc.strftime("%Y-%m-%d") if pd.notna(venc) else None
        forn = str(r[c_forn]).strip()
        doc = str(r[c_doc]).strip() if c_doc and pd.notna(r[c_doc]) else None
        loja = str(r[c_loja]).strip() if c_loja and pd.notna(r[c_loja]) else None
        valor = round(float(r["_valor"]), 2)
        aberto = (round(float(pd.to_numeric(r[c_abert], errors="coerce")), 2)
                  if c_abert and pd.notna(r[c_abert]) else valor)
        titulos.append({
            "fornecedor": forn,
            "vencimento": venc_iso,
            "documento": doc,
            "tipo_docto": (str(r[c_tipo]).strip() if c_tipo and pd.notna(r[c_tipo]) else None),
            "valor": valor,
            "valor_aberto": aberto,
            "loja": loja,
            "empresa": empresa_da_loja(loja) if loja else None,
            "historico": (str(r[c_hist]).strip() if c_hist and pd.notna(r[c_hist]) else None),
            "linha_hash": _linha_hash(forn, doc or "", venc_iso or "", valor),
        })
    return titulos


# ─── Casamento título ↔ saída ────────────────────────────────────────────────
def similaridade(a: str, b: str) -> float:
    """0..1 entre dois nomes normalizados (difflib). Bônus se um contém o outro."""
    na, nb = _norm(a), _norm(b)
    if not na or not nb:
        return 0.0
    if na in nb or nb in na:
        return max(0.85, SequenceMatcher(None, na, nb).ratio())
    return SequenceMatcher(None, na, nb).ratio()


def _reais(v: float) -> int:
    return int(round(float(v)))


def _nome_saida(s: dict) -> str:
    return s.get("contraparte") or s.get("descricao") or ""


def casar(titulos: list[dict], saidas: list[dict], *,
          limiar_nome: float = 0.60, piso_sugestao: float = 0.34) -> dict:
    """
    Casa por **valor a reais** + **nome fuzzy**, um-para-um (guloso por similaridade).

    Regra de aceite (nessa ordem, guloso do melhor par pro pior):
      • sim ≥ limiar_nome        → 'casado' (FORTE: valor e nome batem);
      • piso ≤ sim < limiar      → 'valor'  (valor bate, nome fraco: rever);
      • sim < piso, mas é a ÚNICA saída daquele valor ainda livre → 'valor'
        (nome diferente é provável razão social × nome do PIX);
      • senão                    → 'sem_saida' (não sugere lixo).

    saidas: dicts com id, data, valor, contraparte, descricao, empresa_apelido.
    Devolve:
      titulos: cada título anotado com _status, _saida (dict|None), _sim, _diferenca.
      saidas_sem_titulo: saídas que nenhum título usou.
    """
    por_valor: dict[int, list[dict]] = {}
    for s in saidas:
        por_valor.setdefault(_reais(s["valor"]), []).append(s)

    # Categoria (bucket) de cada título (tipo_docto do CPR) e de cada saída (plano).
    bkt_t = [bucket_categoria(t.get("tipo_docto") or "") for t in titulos]
    bkt_s = {s["id"]: bucket_categoria(s.get("plano") or "") for s in saidas}

    # Pares título×saída de MESMO VALOR, com a similaridade de nome.
    pares = []
    for i, t in enumerate(titulos):
        for s in por_valor.get(_reais(t["valor"]), []):
            sim = similaridade(t["fornecedor"], _nome_saida(s))
            if t.get("empresa") and s.get("empresa_apelido") == t["empresa"]:
                sim = min(sim + 0.05, 1.0)  # bônus leve mesma loja/empresa
            pares.append((sim, i, s))
    pares.sort(key=lambda p: p[0], reverse=True)

    # CASA (guloso, do melhor pro pior) quando o valor bate E (o NOME é forte OU a
    # CATEGORIA é a mesma). Isso acha a folha (CPR no nome da empresa × banco no nome
    # do funcionário, mas ambos 'Folha e Pessoal') e a mercadoria, sem grudar folha
    # num título de mercadoria só porque o valor coincidiu.
    escolha: dict[int, tuple] = {}   # idx_titulo -> (saida, sim, casou_por_nome)
    usadas: set = set()
    for sim, i, s in pares:
        if i in escolha or s["id"] in usadas:
            continue
        forte_nome = sim >= limiar_nome
        mesma_cat = bkt_t[i] != "Outros / Administrativo" and bkt_t[i] == bkt_s[s["id"]]
        if forte_nome or mesma_cat:
            escolha[i] = (s, sim, forte_nome)
            usadas.add(s["id"])

    anotados = []
    for i, t in enumerate(titulos):
        t = dict(t)
        if i in escolha:
            s, sim, forte_nome = escolha[i]
            t["_saida"] = s
            t["_sim"] = round(min(sim, 1.0), 3)
            t["_status"] = "casado" if forte_nome else "categoria"
            t["_diferenca"] = round(s["valor"] - t["valor"], 2)
        else:
            t["_saida"], t["_sim"], t["_status"], t["_diferenca"] = None, 0.0, "sem_saida", None
        anotados.append(t)

    # Toda saída casada (por nome OU categoria) sai do "sem título".
    sem_titulo = [s for s in saidas if s["id"] not in usadas]
    return {"titulos": anotados, "saidas_sem_titulo": sem_titulo}
