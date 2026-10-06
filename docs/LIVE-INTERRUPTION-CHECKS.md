# Bounded direct WebSocket interruption checks

`scripts/check_real_pending.mjs` uses two prerecorded inputs in one fresh public session for a selected case. It opens no audio device. `--case thinking` interrupts on the model’s thinking status before audio; `--case tool` interrupts the fictional availability lookup; `--case speaking` waits for the first nonzero received PCM chunk before sending Interrupt. A speaking status or a silent chunk cannot trigger the speaking case.

For each case, the harness requires a newer server clear, rejects canceled-generation audio arriving after that clear, observes another second, then feeds the recovery recording. Recovery requires nonzero audio, final user and assistant text, the matching listening event, and elapsed receipts for every received recovery chunk. It sends End and checks closed-session resource counts. A selected case has a 90-second active deadline and 10 additional seconds for cleanup. Default `--case all` retains only the existing tool and thinking cases with their four-input bound.

```sh
node scripts/check_real_pending.mjs \
  --base https://YOUR-WORKER.workers.dev \
  --pcm /private/tmp/complete.pcm \
  --case speaking \
  --capture-root /private/tmp/pipecat-live \
  --evidence /private/tmp/speaking-result.json
```

Input is headerless PCM16 little-endian mono at 16 kHz, between 0.2 and 15 seconds. The tool case also needs `--tool-pcm` containing a fictional appointment request. `--validate-input` checks recordings without network requests.

Record the deployed revision with each new result. Preserve failed results and keep private audio/transcript captures outside the repository’s outputs directory. This check establishes software observations at the client. It does not measure acoustic interruption, physical playback, remote compute cancellation or performance acceptance.
