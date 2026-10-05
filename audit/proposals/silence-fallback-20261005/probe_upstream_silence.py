import hashlib,inspect,json,importlib.metadata
from pathlib import Path
from pipecat.audio.turn.smart_turn.base_smart_turn import BaseSmartTurn,SmartTurnParams
from pipecat.audio.turn.base_turn_analyzer import EndOfTurnState
class Probe(BaseSmartTurn):
 def _predict_endpoint(self,audio):
  raise AssertionError('No model inference is permitted in this append_audio probe')
a=Probe(sample_rate=16000);a.set_sample_rate(16000)
chunk=bytes(640)
checks=[]
checks.append(['before_speech',a.append_audio(chunk,False).name])
a.append_audio(chunk,True)
for _ in range(149):state=a.append_audio(chunk,False)
checks.append(['after_2_98_seconds_observed_non_speech',state.name])
checks.append(['after_3_seconds_observed_non_speech',a.append_audio(chunk,False).name])
checks.append(['after_completion_clears',a.append_audio(chunk,False).name])
a.append_audio(chunk,True)
for _ in range(100):a.append_audio(chunk,False)
a.append_audio(chunk,True)
for _ in range(149):state=a.append_audio(chunk,False)
checks.append(['resumption_resets_2_98_seconds',state.name])
checks.append(['resumption_then_3_seconds',a.append_audio(chunk,False).name])
assert [x[1] for x in checks]==['INCOMPLETE','INCOMPLETE','COMPLETE','INCOMPLETE','INCOMPLETE','COMPLETE']
p=Path(inspect.getfile(BaseSmartTurn))
print(json.dumps({'package':importlib.metadata.version('pipecat-ai'),'origin':str(p),'sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'params':SmartTurnParams().model_dump(),'checks':checks,'inference_calls':0,'scope':'Normally installed reference package, synchronous append_audio only, controlled VAD flags and zero PCM; no model/network/Worker or real microphone'},indent=2))
