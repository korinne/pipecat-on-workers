#!/usr/bin/env python3
"""Resolve published Pipecat candidates normally in isolated temporary projects.

Requires installed uv and the repository's pinned pywrangler. Does not deploy.
Every command and exit status is retained. Choose a new work directory each run.
"""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tomllib
import urllib.request

VERSIONS = ('1.11.0', '1.12.0')
WORKER = '''from workers import WorkerEntrypoint, Response
from pipecat.services.stt_service import STTService
from pipecat.services.llm_service import LLMService
from pipecat.services.tts_service import TTSService
from pipecat.transports.base_output import BaseOutputTransport
from pipecat.processors.aggregators.llm_response_universal import LLMContextAggregatorPair
from pipecat.audio.turn.base_turn_analyzer import BaseTurnAnalyzer
from pipecat.pipeline.worker import PipelineWorker
class Default(WorkerEntrypoint):
    async def fetch(self, request):
        return Response("candidate-imported")
'''


def sha(data):
    return hashlib.sha256(data).hexdigest()


def main(args):
    args.work.mkdir(parents=True, exist_ok=False)
    app_project = (args.repo / 'pyproject.toml').read_bytes()
    app_dependencies = tomllib.loads(app_project.decode())['project']['dependencies']
    env = dict(os.environ, PATH=str(args.uv.parent) + os.pathsep + os.environ['PATH'])
    # Keep cache and candidate environments out of the application checkout.
    env['UV_CACHE_DIR'] = str(args.work / 'uv-cache')
    env.pop('PYTHONPATH', None)
    report = {
        'schema':'pipecat-package-reproduction-v1',
        'timestamp_utc':datetime.now(timezone.utc).isoformat(),
        'application_source_revision':subprocess.check_output(['git','rev-parse','HEAD'],cwd=args.repo,text=True).strip(),
        'application_pyproject_sha256':sha(app_project),
        'application_dependencies':app_dependencies,
        'probe_sha256':sha(Path(__file__).read_bytes()),
        'readiness':'not_established',
        'workers_compatibility_date':'2026-09-24',
        'observations':[], 'artifacts':[],
        'scope':'Normal candidate installation and Workers packaging only; no deployment or model request.',
    }
    output = args.work / 'resolution.json'

    def save():
        output.write_text(json.dumps(report, indent=2) + '\n')

    def run(case, command, cwd):
        try:
            completed = subprocess.run([str(x) for x in command],cwd=cwd,env=env,text=True,capture_output=True,timeout=180)
            item = {'case':case,'command':[str(x) for x in command],'cwd':str(cwd),'exit_code':completed.returncode,'stdout':completed.stdout,'stderr':completed.stderr}
        except subprocess.TimeoutExpired as exc:
            def decode(value):
                return value.decode(errors='replace') if isinstance(value,bytes) else value
            item = {'case':case,'command':[str(x) for x in command],'cwd':str(cwd),'timeout_seconds':180,'stdout':decode(exc.stdout),'stderr':decode(exc.stderr),'classification':'test_error'}
        report['observations'].append(item)
        save()
        print(case,item.get('exit_code','timeout'),flush=True)
        return item

    run('uv-version',[args.uv,'--version'],args.work)
    run('pywrangler-version',[args.pywrangler,'--version'],args.work)
    with urllib.request.urlopen('https://pypi.org/pypi/pipecat-ai/json') as response:
        latest = json.load(response)
    report['pypi_latest_at_check'] = latest['info']['version']
    for version in VERSIONS:
        with urllib.request.urlopen(f'https://pypi.org/pypi/pipecat-ai/{version}/json') as response:
            data = json.load(response)
        metadata = {key:data['info'][key] for key in ('name','version','requires_dist','requires_python','project_urls')}
        metadata['files'] = [{key:f[key] for key in ('filename','url','digests','upload_time_iso_8601')} for f in data['urls']]
        report['artifacts'].append(metadata)
        wheel_metadata = next(f for f in metadata['files'] if f['filename'].endswith('.whl'))
        wheel = args.work / wheel_metadata['filename']
        wheel.write_bytes(urllib.request.urlopen(wheel_metadata['url']).read())
        assert sha(wheel.read_bytes()) == wheel_metadata['digests']['sha256']
        for profile in ('application-pins','candidate-only'):
            project = args.work / (version + '-' + profile)
            project.mkdir()
            dependencies = ['pipecat-ai==' + version]
            if profile == 'application-pins':
                dependencies += app_dependencies
            (project / 'pyproject.toml').write_text('[project]\nname = "pipecat-package-candidate"\nversion = "0.0.0"\nrequires-python = ">=3.14,<3.15"\ndependencies = ' + json.dumps(dependencies) + '\n')
            (project / 'wrangler.jsonc').write_text(json.dumps({'name':'pipecat-package-candidate','main':'worker.py','compatibility_date':'2026-09-24','compatibility_flags':['python_workers']},indent=2)+'\n')
            (project / 'worker.py').write_text(WORKER)
            label = version + ':' + profile
            if profile == 'application-pins':
                run(label + ':cpython-resolve',[args.uv,'pip','compile','pyproject.toml','--python',args.host_python,'--no-header'],project)
            run(label + ':workers-sync',[args.pywrangler,'--debug','sync'],project)
            if profile == 'candidate-only':
                run(label + ':workers-source-build',[args.pywrangler,'--debug','sync','--allow-build'],project)
        if version == '1.12.0':
            candidate_env = args.work / 'cpython314'
            created = run(version + ':cpython-venv',[args.uv,'venv',candidate_env,'--python',args.host_python],args.work)
            if created.get('exit_code') == 0:
                installed = run(version + ':cpython-install',[args.uv,'pip','install','--python',candidate_env / 'bin/python',wheel],args.work)
                if installed.get('exit_code') == 0:
                    run(version + ':cpython-probe',[candidate_env / 'bin/python','-I','-B',Path(__file__).with_name('probe.py'),'--wheel',wheel,'--reference',args.repo / 'audit/reference/speech_reference.py','--output',args.work / 'cpython-probe.json'],args.work)
    report['worker_runtime_execution'] = 'untested; inspect packaging results before attempting runtime execution'
    report['application_dependency_changes'] = 'none; the candidate-only projects deliberately omit app pins and record that difference'
    save()
    print(output)
    return 1  # This investigation never asserts release acceptance.

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo',type=Path,required=True)
    parser.add_argument('--work',type=Path,required=True)
    parser.add_argument('--uv',type=Path,required=True)
    parser.add_argument('--pywrangler',type=Path,required=True)
    parser.add_argument('--host-python',type=Path,required=True)
    args = parser.parse_args()
    for key in ('repo','work','uv','pywrangler','host_python'):
        setattr(args,key,getattr(args,key).absolute())
    raise SystemExit(main(args))
