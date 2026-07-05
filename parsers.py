"""
Leitura de extratos bancários → lista de movimentos normalizados.

Formatos: CSV (qualquer banco, por aliases de coluna) e OFX (.ofx/.qfx).
A lógica de CSV é adaptada do parser já validado do app Financeiro Grupo
(parse_bb_csv). PDF fica pra uma 2ª rodada (varia muito por banco).

Cada movimento é um dict normalizado:
    data (date) | valor (float, sempre >0) | tipo ('entrada'/'saida')
    historico (str) | documento (str|None) | saldo_apos (float|None)
    linha_hash (str, pra dedup)
"""
from __future__ import annotations

import csv
import hashlib
import html
import io
import re
import unicodedata
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

# ── Aliases de coluna (cobre BB, Itaú, Bradesco, Caixa, Santander, Sicoob…) ──
ALIASES = {
    "data": ["data", "data movimento", "data lancamento", "data do lancamento",
             "data mov", "dt lancamento", "data da movimentacao"],
    "historico": ["historico", "descricao", "descrição", "lancamento",
                  "historico complementar", "memo", "detalhe", "transacao"],
    "documento": ["documento", "n documento", "numero documento", "doc",
                  "num doc", "numero do documento"],
    "valor": ["valor", "valor lancamento", "valor r$", "valor (r$)",
              "valor da transacao", "movimento"],
    "saldo_apos": ["saldo", "saldo apos", "saldo após", "saldo posterior",
                   "saldo do dia"],
    "valor_entrada": ["credito", "crédito", "entrada", "valor credito",
                      "valor entrada", "creditos"],
    "valor_saida": ["debito", "débito", "saida", "saída", "valor debito",
                    "valor saida", "debitos"],
    "indicador": ["c/d", "tipo", "natureza", "tipo lancamento", "d/c"],
}


def _norm(s: str | None) -> str:
    if s is None:
        return ""
    s = unicodedata.normalize("NFKD", s).encode("ASCII", "ignore").decode()
    return re.sub(r"\s+", " ", s).strip().lower()


def _parse_decimal(v: Any) -> Decimal | None:
    if v is None or v == "":
        return None
    s = str(v).strip()
    if not s or s.lower() in ("nan", "none", "-"):
        return None
    s = re.sub(r"[R$\s]", "", s)
    if "," in s and "." in s:
        s = s.replace(".", "").replace(",", ".")
    elif "," in s:
        s = s.replace(",", ".")
    s = s.replace("%", "")
    try:
        return Decimal(s)
    except InvalidOperation:
        return None


def _parse_data(v: Any) -> date | None:
    if v is None or v == "":
        return None
    s = str(v).strip().split(" ")[0]
    if not s:
        return None
    for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y", "%d/%m/%y", "%Y%m%d"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def _detectar_sep_e_encoding(file_bytes: bytes) -> tuple[str, str]:
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            texto = file_bytes.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise ValueError("Não consegui decodificar o arquivo (utf-8/latin-1).")
    primeira = next((l for l in texto.splitlines() if l.strip()), "")
    sep = ";" if primeira.count(";") > primeira.count(",") else ","
    return texto, sep


def _hash_mov(d: dict[str, Any]) -> str:
    chave = "|".join([
        str(d.get("data") or ""),
        str(d.get("valor") or ""),
        str(d.get("tipo") or ""),
        str(d.get("documento") or ""),
        (d.get("historico") or "")[:100],
    ])
    return hashlib.sha256(chave.encode()).hexdigest()


def hash_arquivo(file_bytes: bytes) -> str:
    return hashlib.sha256(file_bytes).hexdigest()


# ── CSV ──────────────────────────────────────────────────────────────────────
def parse_csv(file_bytes: bytes) -> list[dict[str, Any]]:
    texto, sep = _detectar_sep_e_encoding(file_bytes)
    reader = csv.DictReader(io.StringIO(texto), delimiter=sep)
    header = reader.fieldnames or []
    if not header:
        raise ValueError("CSV vazio ou sem cabeçalho.")

    norm_to_orig = {_norm(h): h for h in header}
    mapa: dict[str, str] = {}
    for campo, aliases in ALIASES.items():
        for alias in aliases:
            if alias in norm_to_orig:
                mapa[campo] = norm_to_orig[alias]
                break
    if "data" not in mapa:
        raise ValueError(f"Não achei a coluna de data. Cabeçalhos: {header}")

    movimentos: list[dict[str, Any]] = []
    for row in reader:
        if not any((row.get(c) or "").strip() for c in header):
            continue

        def g(campo: str) -> Any:
            col = mapa.get(campo)
            return row.get(col) if col else None

        data_mov = _parse_data(g("data"))
        if not data_mov:
            continue

        valor: Decimal | None = None
        tipo: str | None = None
        v_ent = _parse_decimal(g("valor_entrada"))
        v_sai = _parse_decimal(g("valor_saida"))
        if v_ent and v_ent != 0:
            valor, tipo = abs(v_ent), "entrada"
        elif v_sai and v_sai != 0:
            valor, tipo = abs(v_sai), "saida"
        else:
            v = _parse_decimal(g("valor"))
            if v is None or v == 0:
                continue
            ind = (g("indicador") or "").strip().upper()
            if ind in ("C", "CR", "CREDITO"):
                valor, tipo = abs(v), "entrada"
            elif ind in ("D", "DB", "DEBITO"):
                valor, tipo = abs(v), "saida"
            else:
                valor, tipo = abs(v), ("saida" if v < 0 else "entrada")

        hist = (g("historico") or "").strip() or None
        if _eh_linha_de_saldo(hist):
            continue
        cnpj_m = (_RE_CNPJ.search(hist) or _RE_CPF.search(hist)) if hist else None
        d = {
            "data": data_mov,
            "valor": float(valor),
            "tipo": tipo,
            "historico": hist,
            "contraparte": None,
            "cnpj_contraparte": _so_digitos(cnpj_m.group(1)) if cnpj_m else None,
            "documento": (g("documento") or "").strip() or None,
            "saldo_apos": float(_parse_decimal(g("saldo_apos")))
                          if _parse_decimal(g("saldo_apos")) is not None else None,
        }
        d["linha_hash"] = _hash_mov(d)
        movimentos.append(d)
    return movimentos


# ── OFX ──────────────────────────────────────────────────────────────────────
# Parser próprio (regex sobre STMTTRN). Mais robusto que o ofxparse para layouts
# que repetem <MEMO> (ex.: Safra PJ usa um MEMO pro tipo e outro pra contraparte
# com nome + CNPJ). Funciona com qualquer OFX padrão (formato SGML).

# TRNTYPE que significam saída de dinheiro (alguns bancos mandam valor positivo)
_TRNTYPE_DEBITO = {"DEBIT", "FEE", "SRVCHG", "PAYMENT", "CHECK", "ATM",
                   "POS", "DIRECTDEBIT", "CASH"}

_RE_CNPJ = re.compile(r"\b(\d{2}\.?\d{3}\.?\d{3}/?\d{4}-?\d{2})\b")
_RE_CPF = re.compile(r"\b(\d{3}\.?\d{3}\.?\d{3}-?\d{2})\b")
_TIPOS_GENERICOS = {
    "pix recebido", "pix enviado", "pix", "ted", "doc", "transferencia",
    "pagamento de boleto dda", "pagamento de boleto", "boleto", "tarifa",
    "deposito", "saque", "liberacao vinculada", "debito", "credito",
    # variantes do Banco do Brasil (o nome real vem no MEMO)
    "transferencia enviada", "transferencia recebida", "transferencia periodica",
    "cobranca", "cobranca adiantamento", "bb rende facil", "rende facil",
}

# Prefixo de data/hora que o BB põe na frente do MEMO: "01/06 18:01 NOME" ou "10/06 NOME"
_RE_PREFIXO_DATA = re.compile(r"^\d{2}/\d{2}(\s+\d{2}:\d{2})?\s+")
# Prefixo de tipo que o BTG põe na frente do nome: "PIX ENVIADO PARA NOME",
# "PIX RECEBIDO DE NOME", "PAGAMENTO DE BOLETO ENVIADO PARA NOME", "TED ... PARA NOME"
_RE_PREFIXO_TIPO = re.compile(
    r"^(pix\s+(enviado|recebido)\s+(para|de)|"
    r"pagamento\s+de\s+boleto(\s+enviado)?\s+(para|de)|"
    r"ted\s+\w+\s+(para|de)|doc\s+\w+\s+(para|de))\s+",
    re.IGNORECASE,
)
# Cauda de dígitos longos que alguns PIX trazem após o nome (chave/identificador)
_RE_CAUDA_DIGITOS = re.compile(r"\s+\d{12,}\s*$")
# Asaas: "Cobranca recebida - fatura nr. 813011135 NOME" / "Taxa de boleto - fatura
# nr. NNN NOME" / "Taxa de notificacao ... da cobranca 818707851 NOME" → sobra NOME
_RE_ASAAS_PREFIXO = re.compile(r"^.*?(fatura nr\.?\s*\d+|cobranca\s+\d+)\s+", re.IGNORECASE)


def _limpar_contraparte(p: str) -> str:
    """Extrai só o nome da contraparte de um texto de extrato, removendo o
    metadado '| Banco | CNPJ' (BTG), o prefixo de data/hora (BB), o prefixo de
    tipo ('PIX ENVIADO PARA' etc.), o prefixo de fatura (Asaas), CNPJ/CPF
    embutidos e cauda de dígitos."""
    s = p.split("|")[0]                       # BTG: "NOME | Banco 341 | CNPJ ..."
    s = _RE_ASAAS_PREFIXO.sub("", s)          # Asaas: "... fatura nr. NNN NOME"
    s = _RE_PREFIXO_DATA.sub("", s)
    s = _RE_PREFIXO_TIPO.sub("", s)
    s = _RE_CAUDA_DIGITOS.sub("", s)
    s = _RE_CNPJ.sub("", _RE_CPF.sub("", s))
    return s.strip(" -–—.")


def _so_digitos(s: str) -> str:
    return re.sub(r"\D", "", s or "")


def _eh_tipo_generico(s: str | None) -> bool:
    """True se o texto é só o tipo da transação (PIX/TED/Transf…), não a contraparte.
    Ignora pontuação para casar 'Pix - Enviado' com 'pix enviado'."""
    n = re.sub(r"[^a-z0-9 ]", " ", _norm(s))
    return re.sub(r"\s+", " ", n).strip() in _TIPOS_GENERICOS


def _eh_linha_de_saldo(texto: str | None) -> bool:
    """Linhas de SALDO (TOTAL/ANTERIOR/DO DIA/INICIAL…) são a marcação do saldo
    da conta no extrato, não um movimento — não devem virar lançamento."""
    return _norm(texto).startswith("saldo")


def _tag(bloco: str, tag: str) -> str | None:
    m = re.search(rf"<{tag}>([^<\r\n]*)", bloco, re.IGNORECASE)
    return m.group(1).strip() if m else None


def _todos(bloco: str, tag: str) -> list[str]:
    return [x.strip() for x in re.findall(rf"<{tag}>([^<\r\n]*)", bloco, re.IGNORECASE)
            if x.strip()]


def _parse_data_ofx(raw: str | None) -> date | None:
    if not raw:
        return None
    m = re.match(r"(\d{8})", raw.strip())  # AAAAMMDD no início
    if not m:
        return None
    try:
        return datetime.strptime(m.group(1), "%Y%m%d").date()
    except ValueError:
        return None


def _candidatos_encoding_ofx(file_bytes: bytes) -> list[str]:
    """Ordem de tentativa de decode. Sempre tenta UTF-8 primeiro: ele falha alto
    em bytes que não são UTF-8 válido (cai pra cp1252/latin-1), enquanto latin-1
    nunca falha e mascara mojibake. NÃO dá pra confiar no cabeçalho: o BTG declara
    CHARSET:1252 mas exporta UTF-8; o Safra exporta latin-1. cp1252 antes de
    latin-1 (superset que cobre aspas/traços do Windows)."""
    return ["utf-8", "cp1252", "latin-1"]


def parse_ofx(file_bytes: bytes) -> list[dict[str, Any]]:
    for enc in _candidatos_encoding_ofx(file_bytes):
        try:
            texto = file_bytes.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise ValueError("Não consegui decodificar o OFX.")

    blocos = re.findall(r"<STMTTRN>(.*?)</STMTTRN>", texto, re.DOTALL | re.IGNORECASE)
    if not blocos:
        raise ValueError("Nenhuma transação (STMTTRN) encontrada no OFX.")

    movimentos: list[dict[str, Any]] = []
    for bloco in blocos:
        raw_valor = _tag(bloco, "TRNAMT")
        valor = _parse_decimal(raw_valor)
        if valor is None or valor == 0:
            continue
        data_mov = _parse_data_ofx(_tag(bloco, "DTPOSTED"))
        if not data_mov:
            continue

        # Entrada x saída: o sinal do TRNAMT é o ideal, MAS alguns bancos (ex.: este
        # export do Safra) mandam TRNTYPE=DEBIT com valor POSITIVO. Então: é saída se
        # o valor é negativo OU se o TRNTYPE é de débito.
        trntype = (_tag(bloco, "TRNTYPE") or "").strip().upper()
        eh_saida = valor < 0 or trntype in _TRNTYPE_DEBITO

        # Junta NAME + todos os MEMO, sem duplicar, preservando ordem
        # (desescapa entidades HTML: Asaas manda "A &amp; E" = "A & E")
        partes: list[str] = []
        for p in _todos(bloco, "NAME") + _todos(bloco, "MEMO"):
            p = html.unescape(p)
            if p and p not in partes:
                partes.append(p)
        texto_full = " — ".join(partes) if partes else None
        if _eh_linha_de_saldo(texto_full):
            continue

        # CNPJ/CPF da contraparte (procura em todas as partes)
        cnpj = None
        for p in partes:
            m = _RE_CNPJ.search(p) or _RE_CPF.search(p)
            if m:
                cnpj = _so_digitos(m.group(1))
                break

        # Contraparte = a 1ª parte que, depois de limpa, não é só o tipo genérico.
        contraparte = None
        for p in partes:
            limpa = _limpar_contraparte(p)
            if limpa and not _eh_tipo_generico(limpa):
                contraparte = limpa
                break

        d = {
            "data": data_mov,
            "valor": float(abs(valor)),
            "tipo": "saida" if eh_saida else "entrada",
            "historico": texto_full,
            "contraparte": contraparte,
            "cnpj_contraparte": cnpj,
            "documento": (_tag(bloco, "CHECKNUM") or _tag(bloco, "FITID") or "").strip() or None,
            "saldo_apos": None,
        }
        d["linha_hash"] = _hash_mov(d)
        movimentos.append(d)
    return movimentos


# ── Dispatcher ───────────────────────────────────────────────────────────────
# ─────────────────────────────────────────────────────────────────────────────
# Extratos em EXCEL (.xlsx) — um layout por banco
# ─────────────────────────────────────────────────────────────────────────────
# Diferente do OFX (padronizado), cada banco exporta o Excel do seu jeito. Cada
# leitor devolve a MESMA lista de movimentos (data/valor/tipo/historico/
# contraparte/cnpj/documento/linha_hash) que os parsers de OFX/CSV — então a tela
# de Importar Extrato trata tudo igual. `detectar_banco_xlsx` reconhece o layout
# sozinho pra a Elaine não precisar escolher o banco.

def _norm_xls(s: Any) -> str:
    return (unicodedata.normalize("NFKD", str("" if s is None else s))
            .encode("ASCII", "ignore").decode().strip().lower())


def _xls_data(v: Any) -> date | None:
    """Aceita célula de data como Timestamp do Excel OU texto 'DD/MM/AAAA[ HH:MM]'."""
    import pandas as pd
    if v is None or (not isinstance(v, str) and pd.isna(v)):
        return None
    if hasattr(v, "year") and hasattr(v, "month") and hasattr(v, "day"):
        try:
            return date(v.year, v.month, v.day)
        except Exception:
            pass
    m = re.match(r"(\d{2})/(\d{2})/(\d{4})", str(v).strip())
    return date(int(m.group(3)), int(m.group(2)), int(m.group(1))) if m else None


def _num_br(v: Any) -> float | None:
    """'1.234,56' -> 1234.56 (robusto a valor já numérico vindo do Excel)."""
    import pandas as pd
    if v is None or (not isinstance(v, str) and pd.isna(v)):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace("R$", "").replace(" ", "").replace(".", "").replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


def _bb_valor(v: Any) -> float | None:
    """BB: '1.234,56 C' -> +1234.56 · '-3.608,85 D' -> -3608.85 (sufixo C/D manda)."""
    import pandas as pd
    if v is None or (not isinstance(v, str) and pd.isna(v)):
        return None
    s = str(v).strip()
    neg = s.endswith("D") or s.startswith("-")
    s = re.sub(r"[CD]\s*$", "", s).replace("R$", "").strip().replace(".", "").replace(",", ".")
    try:
        val = abs(float(s))
    except ValueError:
        return None
    return -val if neg else val


def _mov_xls(data, valor_assinado, historico, contraparte, cnpj, documento) -> dict:
    d = {
        "data": data,
        "valor": round(abs(valor_assinado), 2),
        "tipo": "saida" if valor_assinado < 0 else "entrada",
        "historico": historico,
        "contraparte": contraparte or None,
        "cnpj_contraparte": cnpj,
        "documento": documento or None,
        "saldo_apos": None,
    }
    d["linha_hash"] = _hash_mov(d)
    return d


def _achar_header_xls(file_bytes: bytes, *chaves: str) -> int | None:
    """Índice da linha de cabeçalho = a 1ª que contém TODAS as `chaves` (robusto às
    linhas de metadados que variam por banco: Itaú tem 9 no topo, Santander tem a
    linha AGENCIA, etc.)."""
    import pandas as pd
    raw = pd.read_excel(io.BytesIO(file_bytes), header=None, nrows=30)
    for i in range(len(raw)):
        cels = [_norm_xls(x) for x in raw.iloc[i].tolist()]
        if all(any(k in c for c in cels) for k in chaves):
            return i
    return None


def _col(df, *chaves: str):
    for c in df.columns:
        n = _norm_xls(c)
        if any(k in n for k in chaves):
            return c
    return None


def _ler_por_nome(file_bytes: bytes, *chaves_header: str):
    """Acha o cabeçalho por palavra-chave e devolve o DataFrame já com colunas nomeadas."""
    import pandas as pd
    hi = _achar_header_xls(file_bytes, *chaves_header)
    if hi is None:
        return None
    return pd.read_excel(io.BytesIO(file_bytes), header=hi)


def parse_bb_xlsx(file_bytes: bytes) -> list[dict[str, Any]]:
    """BB 'Extrato conta corrente': Data | Lançamento | Detalhes | Nº doc | Valor | Tipo."""
    df = _ler_por_nome(file_bytes, "lancamento", "detalhe")
    if df is None:
        return []
    c_data, c_lanc = _col(df, "data"), _col(df, "lancamento")
    c_det, c_doc = _col(df, "detalhe"), _col(df, "documento")
    c_val, c_tipo = _col(df, "valor"), _col(df, "tipo lancamento", "tipo")
    movs = []
    for _, row in df.iterrows():
        data, valor = _xls_data(row.get(c_data)), _bb_valor(row.get(c_val))
        lanc = re.sub(r"\s+", " ", str(row.get(c_lanc)).strip())
        if data is None or valor is None or _norm_xls(lanc).startswith("saldo") \
                or _norm_xls(lanc) == "s a l d o":
            continue
        # sinal de reforço: se o Valor não trouxe sinal, respeita a coluna Tipo (D=débito)
        if valor > 0 and c_tipo and _norm_xls(row.get(c_tipo)).startswith("d"):
            valor = -valor
        det = row.get(c_det)
        detalhes = "" if det is None or (isinstance(det, float)) else re.sub(r"\s+", " ", str(det).strip())
        doc = re.sub(r"\s+", "", str(row.get(c_doc)).strip()) if c_doc else ""
        doc = None if doc.lower() in ("", "0", "nan") else doc
        cnpj_m = _RE_CNPJ.search(detalhes) or _RE_CPF.search(detalhes)
        cp = _limpar_contraparte(detalhes) if detalhes else ""
        historico = (f"{lanc} {cp}".strip() if cp else lanc).title()
        movs.append(_mov_xls(
            data, valor, historico,
            (cp.title() if cp and not cp.isdigit() else (cp or None)),
            _so_digitos(cnpj_m.group(1)) if cnpj_m else None, doc))
    return movs


_SANT_PREFIXOS = [
    "RESGATE CONTAMAX AUTOMATICO", "APLICACAO CONTAMAX", "DEBITO EMPRESTIMO",
    "RENDIMENTO LIQUIDO DE CONTAMAX", "TARIFA PIX RECEBIDO QR CHECKOUT",
    "TARIFA AVULSA ENVIO PIX", "PRESTACAO CONSORCIO", "PIX RECEBIDO", "PIX ENVIADO",
]


def parse_santander_xlsx(file_bytes: bytes) -> list[dict[str, Any]]:
    """Santander: Data | Histórico | Documento | Valor | Saldo (valor com sinal)."""
    df = _ler_por_nome(file_bytes, "historico", "valor")
    if df is None:
        return []
    c_data, c_hist = _col(df, "data"), _col(df, "historico")
    c_doc, c_val = _col(df, "documento"), _col(df, "valor")
    movs = []
    for _, row in df.iterrows():
        data, valor = _xls_data(row.get(c_data)), _num_br(row.get(c_val))
        if data is None or valor is None or _norm_xls(row.get(c_hist)).startswith("saldo"):
            continue
        hist_up = re.sub(r"\s+", " ", str(row.get(c_hist)).strip()).upper()
        doc = re.sub(r"\s+", "", str(row.get(c_doc)).strip()) if c_doc else ""
        doc = None if doc in ("", "0", "000000", "nan", "NAN") else doc
        cp = hist_up
        for pre in _SANT_PREFIXOS:
            if hist_up.startswith(pre):
                cp = hist_up[len(pre):].strip() or None
                break
        cnpj_m = _RE_CNPJ.search(hist_up) or _RE_CPF.search(hist_up)
        movs.append(_mov_xls(
            data, valor, hist_up.title(),
            (cp.title() if cp and not cp.isdigit() else cp),
            _so_digitos(cnpj_m.group(1)) if cnpj_m else None, doc))
    return movs


def parse_itau_xlsx(file_bytes: bytes) -> list[dict[str, Any]]:
    """Itaú: Data | Lançamento | Ag/origem | Razão Social | CPF/CNPJ | Valor | Saldo.
    O Itaú entrega o nome (Razão Social) e o CPF/CNPJ em colunas próprias — melhor que
    garimpar do histórico; cai pro histórico quando vierem vazios (ex.: tarifas)."""
    df = _ler_por_nome(file_bytes, "razao social", "valor")
    if df is None:
        return []
    c_data, c_lanc = _col(df, "data"), _col(df, "lancamento", "historico")
    c_rs, c_cnpj = _col(df, "razao social"), _col(df, "cpf", "cnpj")
    c_val = _col(df, "valor")
    movs = []
    for _, row in df.iterrows():
        data, valor = _xls_data(row.get(c_data)), _num_br(row.get(c_val))
        lanc = re.sub(r"\s+", " ", str(row.get(c_lanc)).strip())
        if data is None or valor is None or _norm_xls(lanc).startswith("saldo"):
            continue
        rs = row.get(c_rs) if c_rs else None
        rs = re.sub(r"\s+", " ", str(rs).strip()) if rs is not None and str(rs).strip().lower() != "nan" else ""
        cnpj_raw = _so_digitos(str(row.get(c_cnpj))) if c_cnpj and row.get(c_cnpj) is not None else ""
        if not cnpj_raw:
            m = _RE_CNPJ.search(lanc) or _RE_CPF.search(lanc)
            cnpj_raw = _so_digitos(m.group(1)) if m else None
        cp = rs or _limpar_contraparte(lanc)
        historico = (f"{lanc} {rs}".strip() if rs else lanc).title()
        movs.append(_mov_xls(data, valor, historico,
                             (cp.title() if cp and not str(cp).isdigit() else (cp or None)),
                             cnpj_raw or None, None))
    return movs


def parse_asaas_xlsx(file_bytes: bytes) -> list[dict[str, Any]]:
    """Asaas: acha o cabeçalho (Data+Valor) sozinho; Valor com sinal; &amp; -> &."""
    import pandas as pd
    df = _ler_por_nome(file_bytes, "data", "valor")
    if df is None:
        return []
    df = df.dropna(how="all")
    c_data, c_val = _col(df, "data"), _col(df, "valor")
    c_desc, c_doc = _col(df, "descricao"), _col(df, "transacao")
    movs = []
    for _, row in df.iterrows():
        data, valor = _xls_data(row.get(c_data)), row.get(c_val)
        if data is None or pd.isna(valor) or float(valor) == 0:
            continue
        desc = re.sub(r"\s+", " ", html.unescape(str(row.get(c_desc) or "")).strip())
        if desc.lower().startswith("saldo"):
            continue
        v = float(valor)
        cnpj_m = _RE_CNPJ.search(desc) or _RE_CPF.search(desc)
        doc = row.get(c_doc) if c_doc else None
        doc = str(int(doc)) if pd.notna(doc) and str(doc) != "" else None
        movs.append(_mov_xls(data, v, desc, _limpar_contraparte(desc) or None,
                             _so_digitos(cnpj_m.group(1)) if cnpj_m else None, doc))
    return movs


def _eh_html_disfarcado(file_bytes: bytes) -> bool:
    """Alguns bancos (Safra) exportam 'Excel' que na verdade é HTML com <table>."""
    inicio = file_bytes[:1024].lstrip().lower()
    return (inicio.startswith(b"<html") or inicio.startswith(b"<!doctype html")
            or inicio.startswith(b"<table") or b"<table" in inicio[:512])


def parse_safra_html(file_bytes: bytes) -> list[dict[str, Any]]:
    """Safra: exporta o extrato como HTML disfarçado de .xls. A tabela de movimentos
    tem Data | Situação | Tipo (Crédito/Débito) | Lançamento | Complemento | Nº Doc |
    Valor | Saldo. Complemento traz a contraparte do PIX; boleto DDA vem somado por
    dia (itemizar depois na tela de Detalhar Boletos DDA)."""
    import pandas as pd
    tabs = pd.read_html(io.BytesIO(file_bytes))
    alvo = None
    for t in tabs:
        for i in range(min(6, len(t))):
            cels = [_norm_xls(x) for x in t.iloc[i].tolist()]
            if any(c == "data" for c in cels) and any("lancamento" in c for c in cels) \
                    and any("valor" in c for c in cels):
                alvo = (t, i)
                break
        if alvo:
            break
    if alvo is None:
        return []
    t, hi = alvo
    header = [_norm_xls(x) for x in t.iloc[hi].tolist()]

    def idx(*keys, excluir=()):
        for j, h in enumerate(header):
            if any(k in h for k in keys) and not any(e in h for e in excluir):
                return j
        return None

    j_data, j_tipo = idx("data"), idx("tipo")
    # "Lançamento" ≠ "Tipo do Lançamento" (as duas têm 'lancamento') — exclui 'tipo'
    j_lanc, j_compl = idx("lancamento", excluir=("tipo",)), idx("complemento")
    j_doc, j_val = idx("documento"), idx("valor")
    movs = []
    for r in range(hi + 1, len(t)):
        row = t.iloc[r].tolist()
        data = _xls_data(row[j_data]) if j_data is not None else None
        valor = _num_br(row[j_val]) if j_val is not None else None
        if data is None or valor is None:
            continue
        lanc = re.sub(r"\s+", " ", str(row[j_lanc]).strip()) if j_lanc is not None else ""
        if _norm_xls(lanc).startswith("saldo"):
            continue
        tipo = _norm_xls(row[j_tipo]) if j_tipo is not None else ""
        if valor > 0 and tipo.startswith("deb"):      # sinal de reforço pelo Tipo
            valor = -valor
        compl = row[j_compl] if j_compl is not None else None
        compl = re.sub(r"\s+", " ", str(compl).strip()) \
            if compl is not None and str(compl).strip().lower() != "nan" else ""
        doc = re.sub(r"\D", "", str(row[j_doc])) if j_doc is not None else ""
        doc = None if doc in ("", "0", "000000000") else doc
        texto = f"{lanc} {compl}".strip()
        cnpj_m = _RE_CNPJ.search(texto) or _RE_CPF.search(texto)
        cp = _limpar_contraparte(compl) if compl else _limpar_contraparte(lanc)
        historico = (f"{lanc} {compl}".strip() if compl else lanc).title()
        movs.append(_mov_xls(
            data, valor, historico,
            (cp.title() if cp and not str(cp).isdigit() else (cp or None)),
            _so_digitos(cnpj_m.group(1)) if cnpj_m else None, doc))
    return movs


_LEITORES_XLSX = {
    "Banco do Brasil": parse_bb_xlsx,
    "Santander": parse_santander_xlsx,
    "Itaú": parse_itau_xlsx,
    "Asaas": parse_asaas_xlsx,
}


def detectar_banco_xlsx(file_bytes: bytes) -> str | None:
    """Reconhece o banco pelo cabeçalho do Excel. None = layout não suportado ainda."""
    import pandas as pd
    if _eh_html_disfarcado(file_bytes):   # Safra vem como HTML disfarçado de .xls
        return "Safra" if b"safra" in file_bytes[:3000].lower() else None
    try:
        raw = pd.read_excel(io.BytesIO(file_bytes), header=None, nrows=30)
    except Exception:
        return None
    blob = " | ".join(_norm_xls(x) for row in raw.values.tolist() for x in row)
    if "tipo de transacao" in blob or ("transacao" in blob and "saldo inicial" in blob):
        return "Asaas"
    if "razao social" in blob and ("cpf/cnpj" in blob or "cpf" in blob or "cnpj" in blob):
        return "Itaú"
    if "tipo lancamento" in blob or ("lancamento" in blob and "detalhe" in blob):
        return "Banco do Brasil"
    if "historico" in blob and "saldo" in blob:
        return "Santander"
    return None


def parse_extrato_xlsx(file_bytes: bytes) -> tuple[str, list[dict[str, Any]]]:
    """Detecta o banco e devolve (banco, movimentos). Erra claro se não reconhecer."""
    if _eh_html_disfarcado(file_bytes):
        # Safra exporta o extrato como HTML disfarçado de .xls
        if b"safra" in file_bytes[:3000].lower():
            return "Safra", parse_safra_html(file_bytes)
        raise ValueError(
            "Esse arquivo é HTML disfarçado de Excel e, nesse formato, só sei ler "
            "o do Safra. Me manda o arquivo que eu ensino.")
    banco = detectar_banco_xlsx(file_bytes)
    if banco is None:
        raise ValueError(
            "Não reconheci de qual banco é esse Excel. Já leio: "
            + ", ".join(_LEITORES_XLSX) + " e Safra. "
            "Me manda o arquivo que eu ensino o layout novo.")
    return banco, _LEITORES_XLSX[banco](file_bytes)


def parse_extrato(file_bytes: bytes, nome_arquivo: str) -> list[dict[str, Any]]:
    ext = nome_arquivo.lower().rsplit(".", 1)[-1] if "." in nome_arquivo else ""
    if ext == "csv" or ext == "txt":
        return parse_csv(file_bytes)
    if ext in ("ofx", "qfx"):
        return parse_ofx(file_bytes)
    if ext in ("xlsx", "xls"):
        return parse_extrato_xlsx(file_bytes)[1]
    raise ValueError(
        f"Formato '.{ext}' ainda não suportado. Use CSV, OFX ou Excel "
        "(BB / Santander / Asaas)."
    )
