"""Exclusive physical-clock source reviews and actual platform refusal through CLI/UI."""
from contextlib import redirect_stdout
from datetime import datetime, timezone
import argparse
import hashlib
import io
import json
from pathlib import Path
import socket
import sys
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from market_checker_app.specialist_evidence_runner import main
from streamlit.testing.v1 import AppTest
PLANS = ['P_instrument_ke_konkretnimu_dni.json','PFE_datovane_zaznamy_applicant.json',
 'DOW_program_a_provozovna_EPA.json','LMT_uplnost_puvodnich_stranek_zakazek.json','MSFT_role_k_datu_transakce.json']
DB = ROOT.parent/'JOHNY-batch4-integration-20261013/outputs/market_checker_history.db'
OUT = ROOT/'evidence/batch11_coordinator_20261018/runtime'

def pin(path):
    raw=path.read_bytes();return {'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest()}

def save(name,value):
    path=OUT/name; raw=(json.dumps(value,ensure_ascii=False,indent=2,sort_keys=True,allow_nan=False)+'\n').encode()
    with path.open('xb') as handle:handle.write(raw)
    return {'path':path.relative_to(ROOT).as_posix(),**pin(path)}

def db_pins():
    return {suffix:pin(Path(str(DB)+suffix)) if Path(str(DB)+suffix).is_file() else None for suffix in ('','-journal','-shm','-wal')}

def main_capture(phase):
    OUT.mkdir(exist_ok=True)
    assert not (OUT/(phase+'_capture.json')).exists()
    started=datetime.now(timezone.utc).isoformat();before=db_pins()
    assert before['']['sha256']=='6d6a7450d9a66bb0fbc3c006deb3aca6c595fcdf1ca682561b476a2f4d9400af'
    counters={'network_attempts':0,'SQL_attempts':0};actions=[]
    def no_network(*args,**kwargs):
        counters['network_attempts']+=1;raise AssertionError('No runtime collection allowed')
    def no_sql(*args,**kwargs):
        counters['SQL_attempts']+=1;raise AssertionError('No runtime SQL allowed; retained journal never opened')
    with patch.object(socket.socket,'connect',no_network),patch('sqlite3.connect',no_sql):
        if phase=='source':
            for name in PLANS:
                stream=io.StringIO()
                with redirect_stdout(stream):
                    main(['review','--source-root',str(ROOT),'--plan','evidence/specialist_review_plans/'+name,
                          '--archive-root',str(OUT/'source_archives')])
                report=json.loads(stream.getvalue())
                assert report['source_http_requests']==report['registry_adoptions']==0
                assert not report['full_specialist_acceptance'] and not report['scoring_applied']
                archive=OUT/'source_archives'/report['archive_filename']
                assert pin(archive)['sha256']==report['archive_sha256']
                saved=save(name[:-5]+'_CLI.json',report)
                actions.append({'interface':'CLI','kind':report['kind'],'plan':name,'recorded_at':report['recorded_at'],'output':saved})
            code=('from pathlib import Path\nimport streamlit as st\n'
                'from market_checker_app.specialist_evidence_ui import render_specialist_evidence_actions\n'
                f'render_specialist_evidence_actions(st,source_root=Path({str(ROOT)!r}),db_path=Path({str(DB)!r}),output_dir=Path({str(OUT)!r}))\n')
            app=AppTest.from_string(code).run(timeout=40);assert not app.exception
            for name in PLANS:
                next(w for w in app.selectbox if w.key=='captured_identity_plan').set_value('evidence/specialist_review_plans/'+name)
                app.run(timeout=40)
                next(b for b in app.button if b.label=='Zkontrolovat vybrané uložené doklady').click().run(timeout=40)
                assert not app.exception and not app.error
                report=app.session_state['captured_identity_review']['report']
                archive=OUT/'captured_identity_reviews'/report['archive_filename']
                assert pin(archive)['sha256']==report['archive_sha256']
                assert report['source_http_requests']==report['registry_adoptions']==0
                assert not report['full_specialist_acceptance'] and not report['scoring_applied']
                saved=save(name[:-5]+'_UI.json',{'report':report,'rendered_captions':[row.value for row in app.caption],'UI_exceptions':[],'UI_errors':[]})
                actions.append({'interface':'STREAMLIT_UI','kind':report['kind'],'plan':name,'recorded_at':report['recorded_at'],'output':saved})
        elif phase=='native':
            manifest=OUT/'unavailable_market_manifest.json'; market_root=OUT/'unavailable_market_originals'
            stream=io.StringIO()
            with redirect_stdout(stream):
                main(['windows-analysis','--enable','--output',str(OUT/'actual_linux_CLI_analysis_refusal.json'),
                      '--tickers','AAPL','--db-path',str(DB),'--market-manifest',str(manifest),'--market-archive-root',str(market_root)])
            cli=json.loads(stream.getvalue());save('native_CLI.json',cli)
            code=('from pathlib import Path\nimport streamlit as st\n'
                'from market_checker_app.specialist_evidence_ui import render_user_windows_analysis\n'
                f'render_user_windows_analysis(st,db_path=Path({str(DB)!r}),output_dir=Path({str(OUT)!r}))\n')
            app=AppTest.from_string(code).run(timeout=40)
            assert not app.exception and not app.checkbox[0].value and app.button[0].disabled
            app.checkbox[0].set_value(True)
            app.text_input[0].set_value('AAPL');app.text_input[1].set_value(str(manifest));app.text_input[2].set_value(str(market_root))
            app.run(timeout=40);app.button[0].click().run(timeout=40)
            assert not app.exception and not app.error
            ui=app.session_state['saved_user_windows_analysis_capture']['report'];save('native_UI.json',{'report':ui,'UI_exceptions':[],'UI_errors':[]})
            for interface,report in [('CLI',cli),('STREAMLIT_UI',ui)]:
                assert report['receipt_created'] is True and report['pipeline_invoked'] is False
                receipt=report['receipt']
                assert receipt['user_windows_end_to_end_verified'] is False and receipt['full_specialist_acceptance'] is False
                assert receipt['native_windows_selected_analysis_verified'] is False
                actions.append({'interface':interface,'status':report['status'],'pipeline_invoked':False,
                                'receipt_pin':pin(Path(report['receipt_path']))})
            assert not manifest.exists() and not market_root.exists()
        else:raise ValueError('Select source or native phase')
    assert before==db_pins();assert all(v==0 for v in counters.values())
    result={'schema_version':'batch11_actual_runtime_capture_v1','phase':phase,'started_at':started,'ended_at':datetime.now(timezone.utc).isoformat(),
      'actions':actions,'retained_database_before_after':before,'SQL_opened':False,'journal_replayed_or_removed':False,
      'database_table_and_forecast_counts':None,'database_counts_status':'UNOBSERVED','counters':counters,
      'genuine_forecasts_registrations_labels_created':0,'source_adoptions':0,'full_specialist_acceptance':'0/21',
      'user_windows_end_to_end_verified':False,'historical_OOS_verified':False}
    output=save(phase+'_capture.json',result)
    print(json.dumps({'phase':phase,'actions':len(actions),'counters':counters,'output':output}))

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('phase',choices=['source','native']);main_capture(parser.parse_args().phase)
