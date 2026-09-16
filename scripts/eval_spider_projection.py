"""Exploratory re-score using exact reference-column names; never calls an LLM.

This rule was developed after examining existing pilot results; it is not a
pre-registered metric or official Spider EX. Missing/ambiguous names are
unscorable, not automatic failures. Full-row strict scores remain unchanged.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import sqlite3
import sys
import tempfile
import zipfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from scripts.eval_spider_pilot import EXPECTED_SHA, rows_match


def project_named_rows(query, reference_columns):
    """Only an unambiguous case-insensitive exact-name mapping is allowed."""
    cols = query.get("columns") or []
    normalized = [str(x).casefold() for x in cols]
    targets = [str(x).casefold() for x in reference_columns]
    if not targets or len(set(targets)) != len(targets):
        return None, "ambiguous_reference_columns"
    if any(normalized.count(target) != 1 for target in targets):
        return None, "missing_or_ambiguous_candidate_column"
    rows = query.get("data") or []
    if any(not isinstance(row, dict) or any(cols[normalized.index(t)] not in row for t in targets) for row in rows):
        return None, "candidate_row_missing_column"
    return [[row[cols[normalized.index(t)]] for t in targets] for row in rows], None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    if hashlib.sha256(args.archive.read_bytes()).hexdigest() != EXPECTED_SHA:
        raise SystemExit("Spider archive checksum mismatch")
    source = json.loads(args.source.read_text())
    output = {"source_sha256": hashlib.sha256(args.source.read_bytes()).hexdigest(),
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "method": "Exploratory post-hoc: project selected main SQL onto uniquely named SQLite reference output columns using case-insensitive exact names. No alias guessing, value-based matching, additional filtering or new model calls. Compare full projected rows and required reference ordering; require successful task and untruncated query. Unmappable cases are unscorable, not failures. Rule was designed after pilot results and is not pre-registered or official Spider EX.",
        "items": []}
    with tempfile.TemporaryDirectory(prefix="daa_projection_") as tmp, zipfile.ZipFile(args.archive) as archive:
        connections = {}
        try:
            for item in source['items']:
                if not item.get('eligible'):
                    continue
                db_id = item['db_id']
                if db_id not in connections:
                    path = Path(tmp) / (db_id + '.sqlite')
                    path.write_bytes(archive.read(f'spider_data/database/{db_id}/{db_id}.sqlite'))
                    connections[db_id] = sqlite3.connect(f'file:{path}?mode=ro', uri=True)
                cursor = connections[db_id].execute(item['reference_sql'])
                columns = [col[0] for col in cursor.description]
                task = item.get('task') or {}
                queries = task.get('queries') or []
                selected = next((q for q in queries if q.get('result_id') == task.get('selected_result_id')), None)
                query = selected or (queries[-1] if queries else None)
                record = {'index': item['index'], 'reference_columns': columns,
                          'selected_result_id': query.get('result_id') if query else None,
                          'strict_passed': item.get('passed', False)}
                if query is None:
                    record.update(scorable=False, reason='no_selected_query')
                else:
                    projected, reason = project_named_rows(query, columns)
                    if reason:
                        record.update(scorable=False, reason=reason, candidate_columns=query.get('columns'))
                    else:
                        passed = task.get('status') == 'success' and not query.get('truncated') and rows_match(projected, item['reference_rows'], item['ordered'])
                        record.update(scorable=True, passed=passed, projected_rows=projected,
                                      reference_rows=item['reference_rows'], candidate_columns=query.get('columns'))
                output['items'].append(record)
        finally:
            for connection in connections.values(): connection.close()
    output['summary'] = {'eligible': len(output['items']),
        'scorable': sum(x['scorable'] for x in output['items']),
        'passed': sum(x.get('passed', False) for x in output['items']),
        'unscorable_reasons': dict(Counter(x['reason'] for x in output['items'] if not x['scorable']))}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, ensure_ascii=False, indent=2, default=str), encoding='utf-8')
    print(json.dumps(output['summary'], ensure_ascii=False))

if __name__ == '__main__': main()
