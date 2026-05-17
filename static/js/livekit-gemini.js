/**
 * Gemini voice + Beyond Presence speech-to-video (browser).
 * - Subscribes to Bey avatar video/audio on LiveKit
 * - Publishes Gemini PCM (24 kHz) into the room for lip-sync
 * Requires livekit-client UMD on window (LivekitClient / LiveKitClient).
 */
(function (global) {
  function getLK() {
    return global.LivekitClient || global.LiveKitClient;
  }

  async function connectAvatarRoom(url, token) {
    const LK = getLK();
    if (!LK) throw new Error("LiveKit client failed to load from CDN");

    const room = new LK.Room({
      adaptiveStream: true,
      dynacast: true,
    });
    await room.connect(url, token);

    if (typeof room.startAudio === "function") {
      try {
        await room.startAudio();
      } catch (_) {
        /* needs user gesture — Connect button satisfies this */
      }
    }

    return { room, LK };
  }

  /** Subscribe to Bey agent video only (Gemini voice stays on WebSocket). */
  async function connectAvatarViewer(url, token) {
    const LK = getLK();
    if (!LK) throw new Error("LiveKit client failed to load from CDN");

    const room = new LK.Room({
      adaptiveStream: true,
      dynacast: true,
    });
    await room.connect(url, token);

    if (typeof room.startAudio === "function") {
      try {
        await room.startAudio();
      } catch (_) {}
    }

    return { room, LK };
  }

  function attachAgentTracks(room, LK, mediaEl, onVideo, options) {
    const opts = options || {};
    // Gemini speaks via WebSocket; ignore Bey managed-agent audio (English STS).
    const videoOnly = opts.videoOnly !== false;

    const attach = (track, participant) => {
      if (!track || (track.kind !== "video" && track.kind !== "audio")) return;
      if (participant?.isLocal) return;
      if (videoOnly && track.kind === "audio") return;
      track.attach(mediaEl);
      mediaEl.muted = false;
      mediaEl.playsInline = true;
      if (track.kind === "video") {
        mediaEl.style.display = "block";
        if (typeof onVideo === "function") onVideo();
        mediaEl.play().catch(() => {});
        if (typeof room.startAudio === "function") {
          room.startAudio().catch(() => {});
        }
      }
    };

    room.on(LK.RoomEvent.TrackSubscribed, (track, _pub, participant) =>
      attach(track, participant)
    );

    room.on(LK.RoomEvent.TrackPublished, (pub, participant) => {
      if (pub.track) attach(pub.track, participant);
    });

    room.on(LK.RoomEvent.ParticipantConnected, (participant) => {
      participant.trackPublications.forEach((pub) => {
        if (pub.track) attach(pub.track, participant);
      });
    });

    room.remoteParticipants.forEach((participant) => {
      participant.trackPublications.forEach((pub) => {
        if (pub.track) attach(pub.track, participant);
      });
    });
  }

  async function createGeminiPcmPublisher(room, sampleRate = 24000) {
    const LK = getLK();
    if (!LK) throw new Error("LiveKit client failed to load");

    if (typeof LK.AudioSource === "function" && LK.LocalAudioTrack?.createAudioTrack) {
      const source = new LK.AudioSource(sampleRate, 1);
      const track = LK.LocalAudioTrack.createAudioTrack("gemini-voice", source);
      const sourceType = LK.Track?.Source?.Microphone ?? LK.Track?.Source?.SOURCE_MICROPHONE;
      await room.localParticipant.publishTrack(track, { source: sourceType });
      const pending = [];

      const flush = async () => {
        while (pending.length) {
          const frame = pending.shift();
          await source.captureFrame(frame);
        }
      };

      return {
        sampleRate,
        async pushPcmBytes(bytes) {
          const int16 = new Int16Array(
            bytes.buffer,
            bytes.byteOffset,
            bytes.byteLength / 2
          );
          if (int16.length < 1) return;
          pending.push(int16);
          await flush();
        },
        async close() {
          try {
            track.stop();
          } catch (_) {}
        },
      };
    }

    const ctx = new AudioContext({ sampleRate });
    const dest = ctx.createMediaStreamDestination();
    const track = new LK.LocalAudioTrack(dest.stream.getAudioTracks()[0]);
    await room.localParticipant.publishTrack(track);
    let nextTime = ctx.currentTime;

    return {
      sampleRate,
      async pushPcmBytes(bytes) {
        const int16 = new Int16Array(
          bytes.buffer,
          bytes.byteOffset,
          bytes.byteLength / 2
        );
        if (int16.length < 1) return;
        const float32 = new Float32Array(int16.length);
        for (let i = 0; i < int16.length; i++) float32[i] = int16[i] / 32768;
        const buffer = ctx.createBuffer(1, float32.length, sampleRate);
        buffer.copyToChannel(float32, 0);
        const src = ctx.createBufferSource();
        src.buffer = buffer;
        src.connect(dest);
        const start = Math.max(ctx.currentTime, nextTime);
        src.start(start);
        nextTime = start + buffer.duration;
      },
      async close() {
        try {
          track.stop();
        } catch (_) {}
        await ctx.close();
      },
    };
  }

  global.NilaLiveKitGemini = {
    connectAvatarRoom,
    connectAvatarViewer,
    attachAgentTracks,
    createGeminiPcmPublisher,
  };
})(typeof window !== "undefined" ? window : globalThis);
