"""Word timing regression for the live trailing-silence misclassification."""
import asyncio
import pathlib
import sys
import unittest
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]/'src'))
from smart_turn import NovaTurnCoordinator

class NovaWordTimingTests(unittest.IsolatedAsyncioTestCase):
    async def capture(self, words, text='If I wanted to'):
        snapshots, errors, frames = [], [], []
        class Provider:
            async def analyze_turn(self, pcm):
                snapshots.append(pcm)
                return {'is_complete':False, 'probability':0.15}
        async def queue(frame): frames.append(frame)
        async def send(event): errors.append(event)
        c=NovaTurnCoordinator(Provider(),queue,send,lambda *a:None)
        await c.connected(1)
        await c.event({'type':'SpeechStarted','timestamp':0,'connection_generation':1})
        c.append_audio(b'\x01\x00'*17920 + b'\x00\x00'*8480)
        await c.event({'type':'Results','start':0,'duration':1.53,'is_final':True,'speech_final':True,
            'channel':{'alternatives':[{'transcript':text,'words':words}]},'connection_generation':1})
        await asyncio.sleep(.01)
        await c.close()
        return snapshots,errors

    async def test_observed_final_word_excludes_endpointing_silence(self):
        snapshots,errors=await self.capture([{'start':0,'end':.24},{'start':.24,'end':.4},
                                           {'start':.4,'end':.8},{'start':.8,'end':1.12}])
        self.assertEqual(snapshots,[b'\x01\x00'*17920])
        self.assertFalse(any(e['type']=='error' for e in errors))

    async def test_missing_or_invalid_word_times_fail_without_analysis(self):
        for words in ([],[{'start':0,'end':float('nan')}],[{'start':0,'end':1.6}],
                      [{'start':.6,'end':.8},{'start':.4,'end':.5}], [{'start':-1,'end':.4}]):
            with self.subTest(words=words):
                snapshots,errors=await self.capture(words)
                self.assertEqual(snapshots,[])
                self.assertTrue(any(e['type']=='error' for e in errors))

    async def test_empty_silence_results_preserve_pending_pause(self):
        events = []
        class Provider:
            async def analyze_turn(self, pcm):
                return {'is_complete': False, 'probability': .1}
        async def queue(frame): pass
        async def send(event): events.append(event)
        c = NovaTurnCoordinator(Provider(), queue, send, lambda *a: None)
        try:
            await c.connected(1)
            c.append_audio(bytes(32000 * 3))
            await c.event({'type':'SpeechStarted','timestamp':0,'connection_generation':1})
            await c.event({'type':'Results','start':0,'duration':1.53,'is_final':True,'speech_final':True,
                'channel':{'alternatives':[{'transcript':'If I wanted to',
                    'words':[{'start':0,'end':1.12}]}]},'connection_generation':1})
            await asyncio.sleep(.01)
            revision = c.revision
            for final in (False, True):
                await c.event({'type':'Results','start':1.53,'duration':1,'is_final':final,'speech_final':False,
                    'channel':{'alternatives':[{'transcript':'','words':[]}]},'connection_generation':1})
            self.assertEqual(c.revision, revision)
            self.assertTrue(c.active)
            self.assertEqual(c.text(), 'If I wanted to')
            self.assertFalse(any(e['type']=='error' for e in events))
        finally:
            await c.close()
