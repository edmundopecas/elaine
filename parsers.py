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
def parse_extrato(file_bytes: bytes, nome_arquivo: str) -> list[dict[str, Any]]:
    ext = nome_arquivo.lower().rsplit(".", 1)[-1] if "." in nome_arquivo else ""
    if ext == "csv" or ext == "txt":
        return parse_csv(file_bytes)
    if ext in ("ofx", "qfx"):
        return parse_ofx(file_bytes)
    raise ValueError(
        f"Formato '.{ext}' ainda não suportado. Use CSV ou OFX "
        "(o PDF entra na próxima rodada)."
    )
