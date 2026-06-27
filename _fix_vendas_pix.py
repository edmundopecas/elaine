"""Corrige PIX de venda marcados como Transferencia entre Empresas.
   Venda = id31 entrada + CNPJ remetente == CNPJ da PROPRIA empresa da conta (Teste 1)
           + SEM perna de saida de mesmo valor em outra conta do grupo (Teste 2).
   As que pareiam ficam como transferencia (entre contas proprias). Backup JSON antes."""
import os, json, sys, collections, datetime
os.environ["ELAINE_DATABASE_URL"] = "postgresql://postgres.qifctqeifdrwpwoljsjc:Dinossauro9383-@aws-1-sa-east-1.pooler.supabase.com:6543/postgres"
from db import query, execute

def brl(v):
    v = v or 0
    return f"R$ {v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
def ddiff(a,b):
    return abs((datetime.date.fromisoformat(a)-datetime.date.fromisoformat(b)).days)

rv = query("SELECT id, entra_dre FROM plano_contas WHERE nome='Receita de Vendas'")
assert len(rv) == 1, rv
RV_ID = rv[0]['id']
print(f"Receita de Vendas = id {RV_ID} (entra_dre={rv[0]['entra_dre']})")

emp = {e['id']: (e['cnpj'] or '').replace('.','').replace('/','').replace('-','')
       for e in query("SELECT id, cnpj FROM empresas")}
saidas = query("SELECT data, valor, conta_bancaria_id FROM lancamentos WHERE tipo='saida'")
sai = collections.defaultdict(list)
for s in saidas: sai[round(s['valor'],2)].append(s)

alvos = query("""SELECT l.id,l.data,l.valor,l.plano_conta_id,l.classificado,l.regra_id,l.observacao,
                        l.cnpj_contraparte,l.conta_bancaria_id,c.empresa_id,e.apelido
                 FROM lancamentos l JOIN contas_bancarias c ON c.id=l.conta_bancaria_id
                 JOIN empresas e ON e.id=c.empresa_id
                 WHERE l.plano_conta_id=31 AND l.tipo='entrada'""")
own = [r for r in alvos if (r['cnpj_contraparte'] or '')==emp.get(r['empresa_id'],'X')]
vendas = []
for r in own:
    cand=[s for s in sai.get(round(r['valor'],2),[])
          if s['conta_bancaria_id']!=r['conta_bancaria_id'] and ddiff(s['data'],r['data'])<=2]
    if not cand: vendas.append(r)

total = sum(r['valor'] for r in vendas)
print(f"\nVenda a reclassificar (sem par): {len(vendas)}  {brl(total)}")
pe=collections.defaultdict(lambda:[0,0.0])
for r in vendas: pe[r['apelido']][0]+=1; pe[r['apelido']][1]+=r['valor']
for ap,(n,v) in pe.items(): print(f"   {ap:<16} n={n:>4} {brl(v)}")

if "--commit" not in sys.argv:
    print("\n(preview — rode com --commit pra aplicar)")
    sys.exit(0)

bkp = r"C:\Users\Elaine\elaine\backup_fix_vendas_pix_20260623.json"
with open(bkp,"w",encoding="utf-8") as f:
    json.dump([{k:r[k] for k in ('id','plano_conta_id','classificado','regra_id','observacao')} for r in vendas],
              f, ensure_ascii=False, indent=2)
print(f"\nBackup salvo: {bkp}")

nota = "corrigido 23/06: venda no PIX da loja (CNPJ proprio, sem perna de saida), estava em Transf entre Empresas"
n=0
for r in vendas:
    n += execute("""UPDATE lancamentos SET plano_conta_id=?, classificado=1, regra_id=NULL,
                    observacao=COALESCE(observacao,'') || ' | ' || ? WHERE id=?""", (RV_ID, nota, r['id']))
print(f"Atualizados: {n} -> Receita de Vendas")

b = query("SELECT tipo,COUNT(*) n,SUM(valor) t FROM lancamentos WHERE plano_conta_id=31 GROUP BY tipo")
print("\nNovo balanço Transferência entre Empresas (id31):")
for r in b: print(f"   {r['tipo']:<8} n={r['n']:>4} {brl(r['t'])}")
fat = query("SELECT SUM(l.valor) t FROM lancamentos l JOIN plano_contas p ON p.id=l.plano_conta_id WHERE l.tipo='entrada' AND p.entra_dre=1")
print(f"\nFaturamento (entradas entra_dre=1) agora: {brl(fat[0]['t'])}")
