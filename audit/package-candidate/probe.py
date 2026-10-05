#!/usr/bin/env python3
"""Check published Pipecat modules and speech reference in isolated CPython.

Install normally first. This probe refuses editable or modified Pipecat source.
The import/thread guards identify dependencies; they do not emulate Workers.
The speech reference uses simulated HTTP and audio writes, never model inference.
"""
from __future__ import annotations
import argparse
import asyncio
import hashlib
import importlib
import importlib.abc
import importlib.metadata
import json
import pathlib
import platform
import runpy
import subprocess
import sys
import threading
import traceback
import zipfile
from datetime import datetime, timezone

COMPONENTS = {
    'stt': 'pipecat.services.stt_service',
    'llm': 'pipecat.services.llm_service',
    'tts': 'pipecat.services.tts_service',
    'output': 'pipecat.transports.base_output',
    'context': 'pipecat.processors.aggregators.llm_context',
    'aggregators': 'pipecat.processors.aggregators.llm_response_universal',
    'hosted_analyzer_base': 'pipecat.audio.turn.base_turn_analyzer',
    'turn_start': 'pipecat.turns.user_start.vad_user_turn_start_strategy',
    'turn_stop': 'pipecat.turns.user_stop.turn_analyzer_user_turn_stop_strategy',
    'worker': 'pipecat.pipeline.worker',
}
DENIED = {'audioop', 'numpy', 'PIL', 'soxr', 'loudness', 'onnxruntime', 'numba', 'soundfile', 'resampy', 'aiohttp', 'openai'}

class GuardRejection(RuntimeError):
    pass

class DenyImports(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.', 1)[0] in DENIED:
            raise GuardRejection('import denied by local probe: ' + fullname)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def provenance(wheel):
    import pipecat
    dist = importlib.metadata.distribution('pipecat-ai')
    root = pathlib.Path(pipecat.__file__).resolve().parent
    assert root == pathlib.Path(dist.locate_file('pipecat')).resolve(), str(root)
    direct = json.loads(dist.read_text('direct_url.json') or '{}')
    assert not direct.get('dir_info', {}).get('editable'), direct
    checked = {}
    with zipfile.ZipFile(wheel) as source:
        for name in source.namelist():
            if name.startswith('pipecat/') and name.endswith('.py'):
                path = pathlib.Path(dist.locate_file(name)).resolve()
                assert path.is_relative_to(root), str(path)
                assert path.read_bytes() == source.read(name), str(path)
                checked[name] = digest(path)
    loaded = {}
    for name, module in list(sys.modules.items()):
        path = getattr(module, '__file__', None)
        if (name == 'pipecat' or name.startswith('pipecat.')) and path and path.endswith('.py'):
            path = pathlib.Path(path).resolve()
            assert path.is_relative_to(root), str(path)
            loaded[name] = {'path':str(path),'sha256':digest(path)}
    return {'distribution': dist.metadata['Name'], 'version':dist.version,
            'root':str(root),'direct_url':direct,'wheel':str(wheel.resolve()),
            'wheel_sha256':digest(wheel),'all_installed_python_sources_match_wheel':True,
            'installed_source_hashes':checked,'loaded_sources':loaded,
            'dependencies':{d.metadata['Name']:d.version for d in sorted(importlib.metadata.distributions(),key=lambda d:d.metadata['Name'].lower())}}


def child(args):
    attempts=[]
    try:
        if args.case.startswith('guard-import:'):
            sys.meta_path.insert(0,DenyImports())
            module=importlib.import_module(COMPONENTS[args.case.split(':',1)[1]])
            observation={'imported':module.__name__,'path':module.__file__}
        else:
            reference=runpy.run_path(str(args.reference))
            reference['logger'].remove()
            reference['logger'].add(sys.stderr,level='ERROR')
            def deny(thread,*a,**kw):
                attempts.append({'name':thread.name,'stack':traceback.format_stack()})
                raise GuardRejection('threading.Thread.start denied by local probe')
            threading.Thread.start=deny
            # The pinned 1.11 reference assertions remain unchanged. A candidate
            # difference is recorded for review and never changed into a pass.
            observation=asyncio.run(reference['scenario'](args.case))
        result={'ok':True,'observation':observation}
    except Exception as exc:
        result={'ok':False,'exception':{'type':type(exc).__name__,'message':str(exc),'traceback':traceback.format_exc()}}
    result['thread_start_attempts']=attempts
    print(json.dumps(result))


def main(args):
    if args.output.exists():
        raise SystemExit('Refusing to overwrite evidence')
    if not sys.flags.isolated or not __debug__:
        raise SystemExit('Run with -I and assertions enabled')
    imports={}
    for key,name in COMPONENTS.items():
        module=importlib.import_module(name)
        imports[key]={'module':name,'path':module.__file__}
    cases=['guard-import:'+key for key in COMPONENTS]
    cases += ['complete','interrupt_before_output','interrupt_mid_first_sentence','interrupt_after_first_sentence',
              'error_before_audio','error_mid_first_sentence','error_after_first_sentence',
              'error_only_sentence_before_audio','error_only_sentence_partial','interrupt_before_buffered_tail_write']
    observations=[]
    for case in cases:
        command=[sys.executable,'-I','-B',str(pathlib.Path(__file__).resolve()),'--case',case,'--reference',str(args.reference.resolve())]
        try:
            process=subprocess.run(command,cwd=args.output.parent,text=True,capture_output=True,timeout=40)
            result={'case':case,'exit_code':process.returncode,'stderr':process.stderr,'raw':json.loads(process.stdout)}
        except Exception as exc:
            result={'case':case,'test_error':str(exc)}
        observations.append(result)
    report={'schema':'pipecat-package-candidate-probe-v1','timestamp_utc':datetime.now(timezone.utc).isoformat(),
            'readiness':'not_established','runtime':{'python':sys.version,'platform':platform.platform(),'executable':sys.executable,'isolated':bool(sys.flags.isolated),'assertions_enabled':__debug__},
            'normal_component_imports':imports,'provenance':provenance(args.wheel),
            'probe_sha256':digest(pathlib.Path(__file__)),'reference_path':str(args.reference.resolve()),'reference_sha256':digest(args.reference),
            'observations':observations,'denied_import_roots':sorted(DENIED),
            'limits':['CPython only. No Workers runtime or provider request is exercised.',
                      'Import guards identify eager dependencies; denial alone does not prove Workers incompatibility.',
                      'The thread guard covers threading.Thread.start during the speech scenario, after its normal imports.',
                      'The unchanged Task 1 reference simulates HTTP responses and output writes; accepted writes do not establish physical playback.',
                      'All candidate package files and dependency modules are unmodified.']}
    args.output.write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps({'output':str(args.output),'source_files_checked':len(report['provenance']['installed_source_hashes']),
                      'observations':[{'case':r['case'],'ok':r.get('raw',{}).get('ok'),'status':r.get('raw',{}).get('observation',{}).get('status'),'test_error':r.get('test_error')} for r in observations]}))

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--wheel',type=pathlib.Path)
    parser.add_argument('--reference',type=pathlib.Path,required=True)
    parser.add_argument('--output',type=pathlib.Path)
    parser.add_argument('--case')
    args=parser.parse_args()
    child(args) if args.case else main(args)
