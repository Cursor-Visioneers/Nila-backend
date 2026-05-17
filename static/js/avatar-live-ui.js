/**
 * Live Bey avatar UI: conversation transcript + browser speech hints.
 */
(function (global) {
  let recognition = null;
  let logEl = null;

  function initTranscript(containerId) {
    logEl = document.getElementById(containerId);
    if (!logEl) return;
    logEl.innerHTML =
      '<p class="empty-transcript">Connect and speak — your words appear here.</p>';
  }

  function clearTranscript() {
    if (!logEl) return;
    logEl.innerHTML =
      '<p class="empty-transcript">Waiting for conversation…</p>';
  }

  function appendLine(role, text, interim) {
    if (!logEl || !text) return;
    const empty = logEl.querySelector(".empty-transcript");
    if (empty) empty.remove();
    const who = role === "user" ? "You" : "Nila";
    const cls = role === "user" ? "line-user" : "line-agent";

    if (interim) {
      let row = document.getElementById("line-interim");
      if (!row) {
        row = document.createElement("div");
        row.id = "line-interim";
        logEl.appendChild(row);
      }
      row.className = `transcript-line ${cls} interim`;
      row.innerHTML = `<strong>${who}</strong> ${escapeHtml(text)} …`;
    } else {
      const interimRow = document.getElementById("line-interim");
      if (interimRow && role === "user") interimRow.remove();
      const div = document.createElement("div");
      div.className = `transcript-line ${cls}`;
      div.innerHTML = `<strong>${who}</strong> ${escapeHtml(text)}`;
      logEl.appendChild(div);
    }
    logEl.scrollTop = logEl.scrollHeight;
  }

  function escapeHtml(s) {
    return String(s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;");
  }

  function startSpeechRecognition(onFinal) {
    const SR = global.SpeechRecognition || global.webkitSpeechRecognition;
    if (!SR) return null;
    stopSpeechRecognition();
    recognition = new SR();
    recognition.continuous = true;
    recognition.interimResults = true;
    recognition.lang = document.documentElement.lang || "en-US";
    recognition.onresult = (event) => {
      let interim = "";
      let finalText = "";
      for (let i = event.resultIndex; i < event.results.length; i++) {
        const t = event.results[i][0].transcript;
        if (event.results[i].isFinal) finalText += t;
        else interim += t;
      }
      if (interim) appendLine("user", interim.trim(), true);
      if (finalText.trim()) {
        appendLine("user", finalText.trim(), false);
        if (typeof onFinal === "function") onFinal(finalText.trim());
      }
    };
    recognition.onerror = () => {};
    try {
      recognition.start();
    } catch (_) {
      recognition = null;
    }
    return recognition;
  }

  function stopSpeechRecognition() {
    if (recognition) {
      try {
        recognition.stop();
      } catch (_) {}
      recognition = null;
    }
    const row = document.getElementById("line-interim");
    if (row) row.remove();
  }

  global.NilaAvatarLiveUI = {
    initTranscript,
    clearTranscript,
    appendLine,
    startSpeechRecognition,
    stopSpeechRecognition,
  };
})(typeof window !== "undefined" ? window : globalThis);
