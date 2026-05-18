/**
 * Queued 24 kHz PCM playback for Gemini Live UIs.
 * Tracks active chunks so the mic is not left muted after the agent finishes.
 */
(function (global) {
  let playbackCtx = null;
  let nextPlayTime = 0;
  let activeChunks = 0;
  let unmuteTimer = null;
  let speakingWatchdog = null;
  let idleCallback = null;
  const MAX_SPEAK_MS = 90000;

  function isAgentSpeaking() {
    return activeChunks > 0;
  }

  function clearAgentSpeaking() {
    activeChunks = 0;
    if (unmuteTimer) {
      clearTimeout(unmuteTimer);
      unmuteTimer = null;
    }
    if (speakingWatchdog) {
      clearTimeout(speakingWatchdog);
      speakingWatchdog = null;
    }
  }

  function armSpeakingWatchdog() {
    if (speakingWatchdog) clearTimeout(speakingWatchdog);
    speakingWatchdog = setTimeout(clearAgentSpeaking, MAX_SPEAK_MS);
  }

  function scheduleUnmute() {
    if (activeChunks > 0) return;
    if (!playbackCtx) {
      clearAgentSpeaking();
      return;
    }
    if (unmuteTimer) clearTimeout(unmuteTimer);
    const delayMs = Math.max(
      100,
      (nextPlayTime - playbackCtx.currentTime) * 1000 + 150
    );
    unmuteTimer = setTimeout(clearAgentSpeaking, delayMs);
  }

  function chunkEnded() {
    activeChunks = Math.max(0, activeChunks - 1);
    if (activeChunks === 0) {
      scheduleUnmute();
      if (idleCallback) {
        const cb = idleCallback;
        idleCallback = null;
        cb();
      }
    } else {
      armSpeakingWatchdog();
    }
  }

  function whenIdle(callback) {
    if (typeof callback !== "function") return;
    if (activeChunks === 0) callback();
    else idleCallback = callback;
  }

  async function playPcm24k(pcmBytes, onStart) {
    if (!playbackCtx) playbackCtx = new AudioContext({ sampleRate: 24000 });
    if (playbackCtx.state === "suspended") {
      try {
        await playbackCtx.resume();
      } catch (_) {}
    }

    const int16 = new Int16Array(
      pcmBytes.buffer,
      pcmBytes.byteOffset,
      pcmBytes.byteLength / 2
    );
    if (int16.length < 1) return;

    const float32 = new Float32Array(int16.length);
    for (let i = 0; i < int16.length; i++) float32[i] = int16[i] / 32768;
    const buffer = playbackCtx.createBuffer(1, float32.length, 24000);
    buffer.copyToChannel(float32, 0);
    const source = playbackCtx.createBufferSource();
    source.buffer = buffer;
    source.connect(playbackCtx.destination);
    const start = Math.max(playbackCtx.currentTime, nextPlayTime);
    const endTime = start + buffer.duration;

    activeChunks += 1;
    armSpeakingWatchdog();
    source.onended = () => chunkEnded();
    source.start(start);
    nextPlayTime = endTime;

    if (typeof onStart === "function") onStart();
  }

  function resetPlayback() {
    idleCallback = null;
    clearAgentSpeaking();
    nextPlayTime = 0;
    if (playbackCtx) {
      playbackCtx.close().catch(() => {});
      playbackCtx = null;
    }
  }

  global.NilaGeminiPlayback = {
    playPcm24k,
    isAgentSpeaking,
    clearAgentSpeaking,
    whenIdle,
    resetPlayback,
  };
})(typeof window !== "undefined" ? window : globalThis);
