-- ─────────────────────────────────────────────────────────────────────────────
-- Elaine — schema Postgres (Supabase). Espelha schema.sql (SQLite) com os tipos
-- traduzidos. Fica num schema próprio "elaine" pra conviver com os outros apps no
-- mesmo projeto Supabase. Notas de tradução:
--   INTEGER PRIMARY KEY AUTOINCREMENT  -> SERIAL PRIMARY KEY
--   REAL                               -> DOUBLE PRECISION  (= float do Python,
--                                         igual ao SQLite; evita Decimal)
--   data/criado_em como TEXT           -> mantidos TEXT (mesmo comportamento do app)
--   booleanos seguem INTEGER 0/1       (o app compara =1 e grava 1/0)
-- ─────────────────────────────────────────────────────────────────────────────

CREATE SCHEMA IF NOT EXISTS elaine;
SET search_path TO elaine, public;

-- Empresas do grupo ----------------------------------------------------------
CREATE TABLE IF NOT EXISTS empresas (
    id           SERIAL PRIMARY KEY,
    razao_social TEXT NOT NULL,
    apelido      TEXT NOT NULL,
    cnpj         TEXT,
    ativa        INTEGER NOT NULL DEFAULT 1,
    criada_em    TEXT NOT NULL DEFAULT (now()::text)
);

-- Contas bancárias -----------------------------------------------------------
CREATE TABLE IF NOT EXISTS contas_bancarias (
    id          SERIAL PRIMARY KEY,
    empresa_id  INTEGER NOT NULL REFERENCES empresas(id),
    banco       TEXT NOT NULL,
    descricao   TEXT,
    ativa       INTEGER NOT NULL DEFAULT 1
);

-- Centros de custo -----------------------------------------------------------
CREATE TABLE IF NOT EXISTS centros_custo (
    id     SERIAL PRIMARY KEY,
    nome   TEXT NOT NULL,
    ativo  INTEGER NOT NULL DEFAULT 1
);

-- Plano de contas ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS plano_contas (
    id         SERIAL PRIMARY KEY,
    codigo     TEXT,
    nome       TEXT NOT NULL,
    grupo      TEXT NOT NULL,
    tipo       TEXT NOT NULL,
    entra_dre  INTEGER NOT NULL DEFAULT 1,
    ordem      INTEGER NOT NULL DEFAULT 0,
    ativo      INTEGER NOT NULL DEFAULT 1
);

-- Regras de classificação ----------------------------------------------------
CREATE TABLE IF NOT EXISTS regras_classificacao (
    id              SERIAL PRIMARY KEY,
    padrao          TEXT NOT NULL,
    tipo_match      TEXT NOT NULL DEFAULT 'contem',
    empresa_id      INTEGER REFERENCES empresas(id),
    aplica_tipo     TEXT,
    plano_conta_id  INTEGER REFERENCES plano_contas(id),
    centro_custo_id INTEGER REFERENCES centros_custo(id),
    prioridade      INTEGER NOT NULL DEFAULT 0,
    vezes_aplicada  INTEGER NOT NULL DEFAULT 0,
    ativa           INTEGER NOT NULL DEFAULT 1,
    criada_em       TEXT NOT NULL DEFAULT (now()::text)
);

-- Importações ----------------------------------------------------------------
CREATE TABLE IF NOT EXISTS importacoes (
    id                 SERIAL PRIMARY KEY,
    empresa_id         INTEGER REFERENCES empresas(id),
    conta_bancaria_id  INTEGER REFERENCES contas_bancarias(id),
    arquivo_nome       TEXT,
    arquivo_hash       TEXT,
    formato            TEXT,
    linhas_total       INTEGER,
    linhas_importadas  INTEGER,
    linhas_duplicadas  INTEGER,
    criado_em          TEXT NOT NULL DEFAULT (now()::text)
);

-- Lançamentos ----------------------------------------------------------------
CREATE TABLE IF NOT EXISTS lancamentos (
    id                SERIAL PRIMARY KEY,
    empresa_id        INTEGER REFERENCES empresas(id),
    conta_bancaria_id INTEGER REFERENCES contas_bancarias(id),
    data              TEXT NOT NULL,
    descricao         TEXT,
    contraparte       TEXT,
    cnpj_contraparte  TEXT,
    documento         TEXT,
    valor             DOUBLE PRECISION NOT NULL,
    tipo              TEXT NOT NULL,
    plano_conta_id    INTEGER REFERENCES plano_contas(id),
    centro_custo_id   INTEGER REFERENCES centros_custo(id),
    classificado      INTEGER NOT NULL DEFAULT 0,
    origem            TEXT NOT NULL DEFAULT 'extrato',
    regra_id          INTEGER REFERENCES regras_classificacao(id),
    importacao_id     INTEGER REFERENCES importacoes(id),
    linha_hash        TEXT,
    saldo_apos        DOUBLE PRECISION,
    observacao        TEXT,
    criado_em         TEXT NOT NULL DEFAULT (now()::text)
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_lanc_hash
    ON lancamentos(linha_hash) WHERE linha_hash IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_lanc_data    ON lancamentos(data);
CREATE INDEX IF NOT EXISTS idx_lanc_empresa ON lancamentos(empresa_id);

-- Títulos (contas a pagar / receber) -----------------------------------------
CREATE TABLE IF NOT EXISTS titulos (
    id              SERIAL PRIMARY KEY,
    empresa_id      INTEGER NOT NULL REFERENCES empresas(id),
    tipo            TEXT NOT NULL,
    descricao       TEXT NOT NULL,
    contraparte     TEXT,
    plano_conta_id  INTEGER REFERENCES plano_contas(id),
    centro_custo_id INTEGER REFERENCES centros_custo(id),
    valor           DOUBLE PRECISION NOT NULL,
    vencimento      TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'aberto',
    data_baixa      TEXT,
    lancamento_id   INTEGER REFERENCES lancamentos(id),
    criado_em       TEXT NOT NULL DEFAULT (now()::text)
);
CREATE INDEX IF NOT EXISTS idx_titulos_venc    ON titulos(vencimento);
CREATE INDEX IF NOT EXISTS idx_titulos_empresa ON titulos(empresa_id);

-- Conferência "A Pagar Geral" (Argos) × pagamentos do extrato -----------------
-- Reaproveita `titulos` como o PREVISTO; a baixa (lancamento_id) é o VÍNCULO com
-- o pagamento realizado. Colunas extras vindas do relatório do Argos.
ALTER TABLE titulos ALTER COLUMN empresa_id DROP NOT NULL;  -- Braga não é empresa do app
ALTER TABLE titulos ADD COLUMN IF NOT EXISTS documento  TEXT;
ALTER TABLE titulos ADD COLUMN IF NOT EXISTS tipo_docto TEXT;   -- MERCADORIA, TRIBUTOS, PESSOAL...
ALTER TABLE titulos ADD COLUMN IF NOT EXISTS loja       TEXT;   -- loja do Argos (razão)
ALTER TABLE titulos ADD COLUMN IF NOT EXISTS origem     TEXT DEFAULT 'manual';  -- 'argos'
ALTER TABLE titulos ADD COLUMN IF NOT EXISTS linha_hash TEXT;   -- dedup em reimport
CREATE UNIQUE INDEX IF NOT EXISTS idx_titulos_hash
    ON titulos(linha_hash) WHERE linha_hash IS NOT NULL;
