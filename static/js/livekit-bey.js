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
      const pubs = room.localParticipant.audioTrackPublications;
      if (pubs && pubs.size < 1) {
        throw new Error("Microphone track did not publish — try Connect again.");
      }
    } catch (err) {
      await room.disconnect();
      throw new Error(micErrorMessage(err));
    }

    if (typeof room.startAudio === "function") {
      try {
        await room.startAudio();
      } catch (_) {
        /* autoplay policy — Connect click counts as gesture */
      }
    }

    return { room, LK };
  }

  function attachAgentTracks(room, LK, mediaEl, audioEl) {
    const videoTarget = mediaEl;
    const audioTarget = audioEl || mediaEl;

    const attach = (track, participant) => {
      if (!track || (track.kind !== "video" && track.kind !== "audio")) return;
      if (participant?.isLocal) return;
      const target = track.kind === "video" ? videoTarget : audioTarget;
      track.attach(target);
      if (target.muted !== undefined) target.muted = false;
      if (track.kind === "video" && videoTarget?.style) {
        videoTarget.style.display = "block";
        videoTarget.playsInline = true;
        videoTarget.play?.().catch(() => {});
      }
      if (track.kind === "audio" && typeof room.startAudio === "function") {
        room.startAudio().catch(() => {});
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

  global.NilaLiveKitBey = {
    connectWithMicrophone,
    attachAgentTracks,
    micErrorMessage,
  };
})(typeof window !== "undefined" ? window : globalThis);
