"""Small fixed Chinese Agent smoke set on synthetic CSV; not a benchmark claim.

The 10 data questions and reference SQL are declared before any model call.
All data and repositories are temporary; results are persisted to the output.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from app.config import settings
from app.repository.factory import reset_repository
from app.services.upload_service import create_dataset, get_db
from app.services.analyze_service import run_analysis
from scripts.eval_spider_pilot import rows_match

CSV = '''order_id,order_date,region,channel,product,quantity,amount,customer_id
R001,2026-05-01,华东,线上,A,1,100,C1
R002,2026-05-02,华东,门店,B,2,300,C2
R003,2026-05-02,华南,线上,A,3,300,C1
R004,2026-06-01,华北,线上,C,1,120,C3
R005,2026-06-03,华南,门店,B,1,150,C4
R006,2026-06-08,华东,线上,C,2,240,C1
'''
# All questions, reference SQL, ordering and comparators are fixed here.
CASES = [
 ('sum', '总销售金额是多少？只返回总金额一列。', 'SELECT SUM(amount) AS total FROM orders', False),
 ('region', '按地区汇总销售金额，返回地区和总金额两列。', 'SELECT region,SUM(amount) AS total FROM orders GROUP BY region', False),
 ('east', '华东地区销售金额合计是多少？只返回一列。', "SELECT SUM(amount) AS total FROM orders WHERE region='华东'", False),
 ('product_qty', '按商品统计销售数量，返回商品和数量两列。', 'SELECT product,SUM(quantity) AS total FROM orders GROUP BY product', False),
 ('customers', '去重后有多少个不同客户？只返回一个数。', 'SELECT COUNT(DISTINCT customer_id) AS total FROM orders', False),
 ('records', '每个地区各有多少行订单记录？返回地区和行数两列。', 'SELECT region,COUNT(*) AS total FROM orders GROUP BY region', False),
 ('date', '2026年6月1日至6月30日（含首尾）销售金额总和是多少？只返回一列。', "SELECT SUM(amount) AS total FROM orders WHERE order_date >= '2026-06-01' AND order_date < '2026-07-01'", False),
 ('channel', '按渠道汇总销售金额，返回渠道和总金额两列。', 'SELECT channel,SUM(amount) AS total FROM orders GROUP BY channel', False),
 ('top', '销售金额最高的单条订单是哪一条？并列时按订单号升序取第一条，只返回订单号和金额两列。', 'SELECT order_id,amount FROM orders ORDER BY amount DESC,order_id ASC LIMIT 1', True),
 ('empty', '列出西南地区的订单号，只返回订单号这一列。', "SELECT order_id FROM orders WHERE region='西南'", False),
]

def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True);p.add_argument('--case',choices=[x[0] for x in CASES]);args=p.parse_args()
 if settings.use_mock_llm or settings.llm_model != 'deepseek-flash': raise SystemExit('Only real deepseek-flash is allowed')
 report={'benchmark':'10 fixed synthetic Chinese SQL smoke cases', 'created_utc':datetime.now(timezone.utc).isoformat(),
         'model':settings.llm_model,'csv_sha256':hashlib.sha256(CSV.encode()).hexdigest(),
         'script_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
         'limitations':'Small hand-written local development smoke test, not a held-out human-annotated benchmark. One run, single tiny dataset. Strict main output row and column-count matching; order when specified. No natural-language or safety score.', 'items':[]}
 args.output.parent.mkdir(parents=True,exist_ok=True)
 with tempfile.TemporaryDirectory(prefix='daa_zh_smoke_') as tmp:
  settings.repository_backend='json'; settings.storage_dir=Path(tmp);settings.metrics_enabled=False;settings.ensure_dirs();reset_repository()
  meta=create_dataset([('orders.csv',CSV.encode())]);db=get_db(meta.dataset_id)
  selected_cases=[case for case in CASES if not args.case or case[0]==args.case]
  for case_id,question,sql,ordered in selected_cases:
   reference=db.execute(sql,max_rows=100); expected=[list(r) for r in reference['rows']]; ref_columns=reference['columns']
   start=time.monotonic();task=run_analysis(meta.dataset_id,question,mode='agent'); actual=[list(r.values()) for r in task.get('data') or []]
   passed=bool(task.get('sql')) and task['status']=='success' and not task.get('truncated') and len(task.get('columns') or [])==len(ref_columns) and rows_match(actual,expected,ordered)
   record={'id':case_id,'question':question,'reference_sql':sql,'reference_rows':expected,'ordered':ordered,'passed':passed,'actual_rows':actual,'elapsed_seconds':round(time.monotonic()-start,3),
     'task':{key:task.get(key) for key in ('status','error','error_type','sql','columns','analysis','queries','tool_calls','usage','selected_result_id','truncated')}}
   report['items'].append(record);args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
   print(f"{len(report['items'])}/{len(selected_cases)} {case_id}: {'pass' if passed else 'fail'}",flush=True)
  reset_repository()
 report['finished_utc']=datetime.now(timezone.utc).isoformat();report['passed']=sum(x['passed'] for x in report['items']);args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
 print('strict SQL matches',report['passed'],'/',len(selected_cases))
if __name__=='__main__':main()
