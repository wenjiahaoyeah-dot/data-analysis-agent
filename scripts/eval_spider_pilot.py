"""Reproducible Spider dev pilot through the actual CSV upload + tool Agent path.

Spider uses SQLite while this project uses CSV uploads and DuckDB. This is an
adapted pilot, not the official Spider evaluation or a leaderboard submission.
"""
from __future__ import annotations
import hashlib
import argparse
import json
import random
import sqlite3
import subprocess
import sys
import tempfile
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import pandas as pd
import sqlglot
from sqlglot import exp
from app.config import settings
from app.repository.factory import reset_repository
from app.services.analyze_service import run_analysis
from app.services.upload_service import create_dataset, get_db
from app.tools.chart_tool import _jsonify
from scripts.run_eval import compare_rows

EXPECTED_SHA = '00636695dabed6b5f4b8328a16b13e069a2f16591d5efcce57660669c85b121b'

def sqlite_to_duckdb_reference(sql, column_names):
    """Handle SQLite's double-quoted string fallback for unknown unqualified names.

    The query still must pass an independent SQLite/DuckDB result precheck.
    Known columns and projection aliases remain identifiers.
    """
    tree=sqlglot.parse_one(sql,read='sqlite')
    known={c.casefold() for c in column_names}
    known.update(a.alias.casefold() for a in tree.find_all(exp.Alias) if a.alias)
    def convert(node):
        if isinstance(node,exp.Column) and not node.table and isinstance(node.this,exp.Identifier):
            if node.this.args.get('quoted') and node.name.casefold() not in known:
                return exp.Literal.string(node.name)
        return node
    return tree.transform(convert).sql(dialect='duckdb')

def save(obj, output):
    output.parent.mkdir(parents=True, exist_ok=True)
    tmp=output.with_suffix('.tmp')
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2, default=str),encoding='utf-8')
    tmp.replace(output)

def rows_match(actual, expected, ordered):
    if ordered:
        return len(actual)==len(expected) and all(compare_rows([a],[b]) for a,b in zip(actual,expected))
    return compare_rows(actual,expected)

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('archive',type=Path);parser.add_argument('output',type=Path)
    parser.add_argument('--seed',type=int,default=20261101);parser.add_argument('--sample-size',type=int,default=30)
    parser.add_argument('--exclude',type=Path,help='Previous artifact whose selected indices must not be reused')
    parser.add_argument('--retry-excluded',type=Path,help='Re-run only adapter-excluded indices from a previous artifact')
    parser.add_argument('--indices-from',type=Path,help='Reuse all sampled indices from an earlier artifact')
    parser.add_argument('--preflight-only',action='store_true',help='Check SQLite/DuckDB reference equivalence without model calls')
    args=parser.parse_args()
    digest=hashlib.sha256(args.archive.read_bytes()).hexdigest()
    if digest != EXPECTED_SHA: raise SystemExit('Spider archive checksum mismatch')
    if not args.preflight_only and settings.use_mock_llm: raise SystemExit('Real tool-calling model required')
    if not args.preflight_only and settings.llm_model != 'deepseek-flash': raise SystemExit('This evaluation is restricted to deepseek-flash')
    settings.repository_backend='json'
    settings.metrics_enabled=False
    commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()
    diff=subprocess.check_output(['git','diff','--binary'],cwd=ROOT)
    report={'benchmark':'Spider 1.0 dev adapted CSV/DuckDB pilot', 'archive_sha256':digest,
      'sample_seed':args.seed,'sample_size':args.sample_size,'sample_population':1034,
      'commit':commit,'tracked_diff_sha256':hashlib.sha256(diff).hexdigest(),
      'model':settings.llm_model,'temperature':settings.llm_temperature,
      'started_utc':datetime.now(timezone.utc).isoformat(),
      'method':'Uniform random sample of dev indices; SQLite tables exported to CSV then normal project upload. Reference SQLite SQL is transpiled to DuckDB and must return equivalent rows before model call. Main score requires Agent success and returned main result rows matching reference (selected result if specified, else last query); order checked when reference has ORDER BY. Excluded cases and reasons retained. Not official Spider EX.',
      'items':[]}
    with zipfile.ZipFile(args.archive) as z, tempfile.TemporaryDirectory(prefix='daa_spider_pilot_') as tmp:
        data=json.loads(z.read('spider_data/dev.json'))
        excluded_indices=set(json.loads(args.exclude.read_text())['selected_indices']) if args.exclude else set()
        frame=[i for i in range(len(data)) if i not in excluded_indices]
        if args.indices_from and args.retry_excluded: raise SystemExit('Choose only one index source')
        if args.indices_from:
            indices=sorted(json.loads(args.indices_from.read_text())['selected_indices'])
            report['index_source_sha256']=hashlib.sha256(args.indices_from.read_bytes()).hexdigest()
            args.sample_size=len(indices);report['sample_size']=len(indices)
        elif args.retry_excluded:
            previous=json.loads(args.retry_excluded.read_text())
            indices=sorted(a['index'] for a in previous['items'] if not a.get('eligible'))
            report['retry_source_sha256']=hashlib.sha256(args.retry_excluded.read_bytes()).hexdigest()
            report['retry_reason']='Precheck only: SQLite double-quoted string fallback translated; original SQLite result still verified.'
            args.sample_size=len(indices);report['sample_size']=len(indices)
        else:
            if not 1 <= args.sample_size <= len(frame): raise SystemExit('Invalid sample size')
            indices=sorted(random.Random(args.seed).sample(frame,args.sample_size))
        report['sample_frame_size']=len(frame)
        report['excluded_prior_indices']=sorted(excluded_indices)
        report['selected_indices']=indices
        settings.storage_dir=Path(tmp)/'workspace';settings.ensure_dirs();reset_repository()
        loaded={}
        for idx in indices:
            case=data[idx];dbid=case['db_id']; result={'index':idx,'db_id':dbid,'question':case['question'],'reference_sql':case['query']}
            try:
                if dbid not in loaded:
                    filename=f'spider_data/database/{dbid}/{dbid}.sqlite'
                    dbpath=Path(tmp)/(dbid+'.sqlite');dbpath.write_bytes(z.read(filename))
                    conn=sqlite3.connect(f'file:{dbpath}?mode=ro',uri=True)
                    tables=[r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
                    files=[]; column_names=[]
                    for table in tables:
                        quoted='"'+table.replace('"','""')+'"'
                        df=pd.read_sql_query('SELECT * FROM '+quoted,conn)
                        column_names.extend(str(c) for c in df.columns)
                        files.append((table+'.csv',df.to_csv(index=False).encode('utf-8')))
                    meta=create_dataset(files)
                    loaded[dbid]=(conn,meta,get_db(meta.dataset_id),column_names)
                conn,meta,db,column_names=loaded[dbid]
                expected=[[_jsonify(v) for v in row] for row in conn.execute(case['query']).fetchall()]
                if len(expected)>settings.max_result_rows: raise ValueError('reference_over_result_limit')
                converted=sqlite_to_duckdb_reference(case['query'],column_names)
                gold=db.execute(converted,max_rows=10000)
                normalized=[[_jsonify(v) for v in row] for row in gold['rows']]
                ordered=bool(sqlglot.parse_one(case['query'],read='sqlite').args.get('order'))
                if gold['truncated'] or not rows_match(normalized,expected,ordered): raise ValueError('sqlite_duckdb_reference_mismatch')
                result['eligible']=True;result['reference_rows']=expected;result['reference_duckdb_sql']=converted;result['ordered']=ordered
                if args.preflight_only:
                    result['preflight_only']=True
                    report['items'].append(result);save(report,args.output)
                    print(f"{len(report['items']):02d}/{args.sample_size} index={idx} db={dbid} eligible",flush=True)
                    continue
                start=time.monotonic()
                task=run_analysis(meta.dataset_id,case['question'],mode='agent')
                actual=[list(row.values()) for row in task.get('data') or []]
                equal=rows_match(actual,expected,ordered)
                passed=task['status']=='success' and not task.get('truncated') and equal
                result.update({'passed':passed,'ordered':ordered,'actual_rows':actual,'elapsed_seconds':round(time.monotonic()-start,3),
                  'task':{k:task.get(k) for k in ('status','error_type','error','sql','selected_result_id','queries','tool_calls','usage','analysis','trace','truncated','retry_count')}})
            except Exception as exc:
                if result.get('eligible'):
                    result.update(passed=False,runner_error=f'{type(exc).__name__}: {str(exc)[:300]}')
                else:
                    result.update(eligible=False,exclusion=f'{type(exc).__name__}: {str(exc)[:300]}')
            report['items'].append(result);save(report,args.output)
            print(f"{len(report['items']):02d}/{args.sample_size} index={idx} db={dbid} " + ('pass' if result.get('passed') else 'fail' if result.get('eligible') else 'excluded'),flush=True)
        for conn,_,_,_ in loaded.values(): conn.close()
        reset_repository()
    report['finished_utc']=datetime.now(timezone.utc).isoformat();save(report,args.output)
    eligible=[x for x in report['items'] if x.get('eligible')]
    print('eligible',len(eligible),'passed',sum(x.get('passed',False) for x in eligible),'excluded',args.sample_size-len(eligible),flush=True)

if __name__=='__main__': main()
