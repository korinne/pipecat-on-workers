import test from 'node:test';
import assert from 'node:assert/strict';
import { options, isPrematureReply } from './smoke_real_voice.mjs';

test('single-user-turn acceptance is opt-in without changing normal option defaults', () => {
  assert.equal(options([]).expectSingleUserTurn, undefined);
  const parsed = options(['--pcm', 'pause.pcm', '--expect-single-user-turn', '--validate-input']);
  assert.equal(parsed.pcm, 'pause.pcm');
  assert.equal(parsed.expectSingleUserTurn, true);
  assert.equal(parsed.validate, true);
});

test('assistant partial/final transcripts and audio are premature until paced input ends', () => {
  for (const message of [{ type: 'audio' }, { type: 'transcript', role: 'assistant', final: false },
    { type: 'transcript', role: 'assistant', final: true }]) {
    assert.equal(isPrematureReply(message, 500, undefined), true);
    assert.equal(isPrematureReply(message, 999, 1000), true);
    assert.equal(isPrematureReply(message, 1000, 1000), false);
    assert.equal(isPrematureReply(message, 1001, 1000), false);
  }
  assert.equal(isPrematureReply({ type: 'transcript', role: 'user', final: true }, 500, undefined), false);
  assert.equal(isPrematureReply({ type: 'status', state: 'thinking' }, 500, undefined), false);
});
