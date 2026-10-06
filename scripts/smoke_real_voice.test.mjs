import test from 'node:test';
import assert from 'node:assert/strict';
import { options, isPrematureReply, isCompletedResponse, isAllowedBase } from './smoke_real_voice.mjs';

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


test('completion needs standard text, matching response end and every elapsed receipt', () => {
  const complete = { userFinals: 1, assistantSentences: 2, received: 42, acknowledged: 42,
    queueLength: 0, replyGeneration: 4, listeningGeneration: 4, generation: 4 };
  assert.equal(isCompletedResponse(complete), true);
  for (const change of [{ userFinals: 0 }, { assistantSentences: 0 }, { received: 0, acknowledged: 0 },
    { acknowledged: 41 }, { queueLength: 1 }, { listeningGeneration: undefined },
    { listeningGeneration: 3 }, { generation: 5 }, { replyGeneration: undefined, listeningGeneration: undefined }]) {
    assert.equal(isCompletedResponse({ ...complete, ...change }), false, JSON.stringify(change));
  }
});


test('HTTP live probes are limited to exact loopback origins', () => {
  for (const value of ['http://127.0.0.1:8794/', 'http://localhost:8794/', 'http://[::1]:8794/', 'https://example.com/']) {
    assert.equal(isAllowedBase(new URL(value)), true);
  }
  for (const value of ['http://example.com/', 'http://localhost.example.com/', 'http://192.168.1.1/',
    'https://user:pass@example.com/', 'https://example.com/path', 'http://127.0.0.1/?token=x', 'https://example.com/#fragment']) {
    assert.equal(isAllowedBase(new URL(value)), false);
  }
});
