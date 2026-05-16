/**
 * Beyond Presence LiveKit helpers (browser).
 * Requires livekit-client UMD: LivekitClient or LiveKitClient on window.
 */
(function (global) {
  function getLK() {
    return global.LivekitClient || global.LiveKitClient;
  }

  function micErrorMessage(err) {
    const name = err?.name || "";
    if (name === "NotAllowedError" || name === "PermissionDeniedError") {
      return (
        "Microphone blocked. Use the button below (required once), then allow mic " +
        "in the browser address bar (lock icon → Microphone → Allow)."
      );
    }
    if (name === "NotFoundError") {
      return "No microphone found. Plug in a mic or check system settings.";
    }
    return err?.message || String(err);
  }

  async function connectWithMicrophone(url, token) {
    const LK = getLK();
    if (!LK) throw new Error("LiveKit client failed to load from CDN");

    const room = new LK.Room({
      audioCaptureDefaults: {
        echoCancellation: true,
        noiseSuppression: true,
      },
    });

    await room.connect(url, token);

    try {
      const micOn = await room.localParticipant.setMicrophoneEnabled(true);
      if (!micOn) {
        throw new Error("Microphone could not be enabled.");
      }
    } catch (err) {
      await room.disconnect();
      throw new Error(micErrorMessage(err));
    }

    if (typeof room.startAudio === "function") {
      try {
        await room.startAudio();
      } catch (_) {
        /* autoplay policy — video element may still play after gesture */
      }
    }

    return { room, LK };
  }

  function attachAgentTracks(room, LK, mediaEl) {
    const attach = (track) => {
      if (!track || (track.kind !== "video" && track.kind !== "audio")) return;
      track.attach(mediaEl);
      mediaEl.muted = false;
    };

    room.on(LK.RoomEvent.TrackSubscribed, (track) => attach(track));

    room.remoteParticipants.forEach((participant) => {
      participant.trackPublications.forEach((pub) => {
        if (pub.track) attach(pub.track);
      });
    });
  }

  global.NilaLiveKitBey = {
    connectWithMicrophone,
    attachAgentTracks,
    micErrorMessage,
  };
})(typeof window !== "undefined" ? window : globalThis);
