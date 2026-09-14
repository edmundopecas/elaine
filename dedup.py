"""Dedup robusto de movimentos na importação.

PROBLEMA 1 (o original): o índice UNIQUE em `lancamentos.linha_hash` (e o dedup
antigo que checava só "existe esse hash?") COLAPSAVA linhas legitimamente idênticas
no mesmo dia — ex.: 3 parcelas de consórcio de R$1.445,40 viravam 1 só. Toda parcela
repetida real era perdida na importação.

SOLUÇÃO: deduplicar por MULTISET de conteúdo (data, valor, tipo) — a Nª ocorrência
de uma linha idêntica no extrato só é considerada duplicada se o banco já tem N
ocorrências daquele mesmo conteúdo. As ocorrências extras são inseridas com um
`linha_hash` sufixado (`hash#2`, `hash#3`, ...) só pra respeitar o índice UNIQUE —
o dedup em si não depende mais do hash ser único.

PROBLEMA 2 (achado em 08/09/2026, no BTG): a CONTAGEM acima estava certa, mas o
EMPARELHAMENTO não. O BTG faz varredura automática pra conta remunerada e todo dia
com PIX tem três linhas de mesmo valor:

    PIX RECEBIDO DE <fulano>       +16.500,00  (hora real)
    DEBITO NA CONTA CORRENTE       -16.500,00  (23:59:59)
    APLICACAO CONTA REMUNERADA     +16.500,00  (23:59:59)

O PIX e a APLICAÇÃO caem na MESMA chave fraca. Quando o banco já tinha o PIX e o
export seguinte trazia os dois, a contagem dizia certo ("entra 1"), mas quem entrava
era decidido pela ORDEM: o primeiro da lista virava "o que já existe" e o segundo era
inserido. Como a aplicação vem primeiro no arquivo, entrava um segundo PIX e a
aplicação nunca entrava. Deu 10 recebimentos duplicados (R$ 54.614,00 de faturamento
inflado) sem a trava de saldo piscar, já que as duas linhas têm o mesmo valor e o
mesmo sinal.

SOLUÇÃO: casar cada movimento do arquivo com a linha do banco que ele realmente
repete, em três passadas, dentro de cada chave fraca:

    1. mesmo DOCUMENTO   (identificador forte: EndToEnd do PIX, CHECKNUM do OFX)
    2. mesma DESCRIÇÃO   (pega o mesmo movimento quando o banco reescreveu o documento)
    3. o que sobrou, na ordem (comportamento antigo)

O número de inserções continua sendo exatamente `max(0, nº no arquivo − nº no banco)`
— igual ao dedup antigo, então nenhuma leva passa a inserir mais do que inseria. O
que muda é só QUAL movimento é considerado novo.

⚠️ POR QUE A PASSADA 2 EXISTE — o SAFRA REESCREVE O DOCUMENTO. Confirmado em
08/09/2026: o "Debito De Seguro" de 10/08 R$ 3.767,43 saiu como documento
`024251848` nos exports de 10 e 11/08 e como `275281747` nos de 13/08 em diante —
mesmo lançamento, número trocado. Se o documento sozinho decidisse, esse débito
entraria de novo a cada export. Casando por descrição depois do documento, ele volta
a casar. (O Safra também alterna entre o documento real e '0' no mesmo PIX; por isso
documento vazio/zerado nunca é usado pra separar dois movimentos.)

Robusto a re-importação: como a comparação é por conteúdo (e não pela string do
hash), os sufixos `#2/#3` não atrapalham — reimportar o mesmo extrato volta a casar
tudo e não duplica nada.
"""
from __future__ import annotations

import re
import unicodedata
from collections import defaultdict
from typing import Any

# documentos que o extrato usa como "sem documento" — não identificam nada
_DOC_VAZIO = {"", "0", "none", "null", "nan", "-", "00000000"}


def _doc_norm(doc: Any) -> str:
    """Documento comparável: só alfanuméricos, minúsculo. '' quando não serve.

    Os bancos escrevem o mesmo número de jeitos diferentes entre exports
    ('11.255.060.558.792' x '11255060558792'), então a pontuação sai fora.
    Documento ausente, '0' ou só zeros é FRACO e nunca casa por documento.
    """
    if doc is None:
        return ""
    s = re.sub(r"[^0-9A-Za-z]", "", str(doc)).lower()
    if not s or s in _DOC_VAZIO or set(s) == {"0"}:
        return ""
    return s


def _desc_norm(txt: Any) -> str:
    """Descrição comparável: sem acento, sem espaço repetido, minúscula."""
    if txt is None:
        return ""
    s = unicodedata.normalize("NFKD", str(txt)).encode("ASCII", "ignore").decode()
    return re.sub(r"\s+", " ", s).strip().lower()


def _content_key(data_iso: str, valor: Any, tipo: str | None,
                 documento: str | None = None, descricao: str | None = None) -> tuple:
    """Chave FRACA de um movimento: (data, valor, tipo) — só isso.

    É a chave de AGRUPAMENTO: dois movimentos só são candidatos a ser "o mesmo" se
    coincidem nela, e a contagem por chave é o que decide QUANTOS entram. O desempate
    de QUEM casa com QUEM (documento, descrição) acontece em `planejar_insercao`.

    NÃO inclui documento nem descrição DE PROPÓSITO: ambos variam entre exports do
    mesmo extrato (o Safra troca o documento do mesmo lançamento e alterna com '0'; o
    texto do histórico mudou quando o parser evoluiu). Se a CONTAGEM dependesse deles,
    reimportar o mesmo período geraria duplicado falso.
    `documento`/`descricao` são aceitos por compatibilidade de chamada e ignorados.
    """
    return (
        data_iso or "",
        round(float(valor or 0), 2),
        tipo or "",
    )


def _casar(existentes: list[dict], indices: list[int], movs: list[dict]) -> set[int]:
    """Quais movimentos (por índice) repetem uma linha já no banco.

    Casa em três passadas — documento, descrição, ordem — consumindo uma linha do
    banco por movimento. Sempre casa exatamente `min(len(existentes), len(indices))`
    movimentos: a contagem é a do dedup antigo, só a escolha é melhor.
    """
    livres = list(indices)
    sobra_db = list(existentes)
    casados: set[int] = set()

    def _consumir(chave_db, chave_mov) -> None:
        for r in list(sobra_db):
            k = chave_db(r)
            if k is None:
                continue
            for i in list(livres):
                if chave_mov(movs[i]) == k:
                    casados.add(i)
                    livres.remove(i)
                    sobra_db.remove(r)
                    break

    # 1) documento igual — identificador forte, é a mesma linha sem dúvida
    _consumir(lambda r: _doc_norm(r.get("documento")) or None,
              lambda m: _doc_norm(m.get("documento")))

    # 2) descrição igual — pega o mesmo movimento quando o banco trocou o documento
    _consumir(lambda r: _desc_norm(r.get("descricao")),
              lambda m: _desc_norm(m.get("historico")))

    # 3) o que sobrou, na ordem (comportamento antigo)
    while sobra_db and livres:
        sobra_db.pop(0)
        casados.add(livres.pop(0))

    return casados


def planejar_insercao(movimentos: list[dict], existentes: list[dict],
                      hashes_globais: set[str] | None = None) -> tuple[list[tuple[dict, str]], int]:
    """Decide o que inserir, deduplicando por multiset de conteúdo.

    Args:
        movimentos: parseados do extrato (cada um com data [date], valor, tipo,
            documento, historico, linha_hash).
        existentes: linhas já no banco daquela conta — dicts com data, valor, tipo,
            documento, descricao, linha_hash.
        hashes_globais: conjunto de linha_hash já usados em QUALQUER conta. O dedup
            por multiset é por conta (`existentes`), mas o índice UNIQUE em
            `linha_hash` é GLOBAL — duas contas podem ter um movimento de conteúdo
            idêntico (ex.: tarifa SafraPay R$0,15 no mesmo dia na Matriz e na Filial)
            cujo hash bate. Sem este conjunto, o sufixo só evitaria colisão dentro da
            própria conta e o INSERT estouraria UniqueViolation. Passe os hashes de
            todas as contas pra garantir sufixo único globalmente.

    Returns:
        (a_inserir, duplicados) onde a_inserir é uma lista de (movimento, linha_hash_único)
        pronta pra INSERT, e duplicados é a contagem de ocorrências já presentes no banco.
    """
    movs = sorted(movimentos, key=lambda x: x["data"])

    db_por_chave: dict[tuple, list[dict]] = defaultdict(list)
    for r in existentes:
        db_por_chave[_content_key(r["data"], r["valor"], r["tipo"])].append(r)

    mv_por_chave: dict[tuple, list[int]] = defaultdict(list)
    for i, m in enumerate(movs):
        mv_por_chave[_content_key(m["data"].isoformat(), m["valor"], m["tipo"])].append(i)

    duplicados_idx: set[int] = set()
    for chave, indices in mv_por_chave.items():
        no_banco = db_por_chave.get(chave)
        if no_banco:
            duplicados_idx |= _casar(no_banco, indices, movs)

    usados = {r["linha_hash"] for r in existentes if r.get("linha_hash")}
    if hashes_globais:
        usados |= hashes_globais

    a_inserir: list[tuple[dict, str]] = []
    for i, m in enumerate(movs):
        if i in duplicados_idx:
            continue
        # ocorrência nova → garante linha_hash único (sufixo só p/ o índice UNIQUE)
        base = m["linha_hash"]
        h = base
        k = 1
        while h in usados:
            k += 1
            h = f"{base}#{k}"
        usados.add(h)
        a_inserir.append((m, h))

    return a_inserir, len(duplicados_idx)
