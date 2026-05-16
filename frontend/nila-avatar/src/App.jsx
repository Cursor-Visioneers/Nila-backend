import { useCallback, useEffect, useRef, useState } from "react";
import { Room, RoomEvent } from "livekit-client";

const defaultApi =
  typeof window !== "undefined" && window.location.port === "5173"
    ? ""
    : `${window.location.protocol}//${window.location.host}`;

export default function App() {
  const [apiBase, setApiBase] = useState(defaultApi);
  const [question, setQuestion] = useState(
    "How do I register a birth in Sri Lanka?"
  );
  const [language, setLanguage] = useState("en");
  const [status, setStatus] = useState("");
  const [reply, setReply] = useState("");
  const [resources, setResources] = useState([]);
  const [audioUrl, setAudioUrl] = useState("");
  const [avatarError, setAvatarError] = useState("");
  const [agents, setAgents] = useState([]);
  const [agentId, setAgentId] = useState("");
  const [embedUrl, setEmbedUrl] = useState("");
  const [avatarMode, setAvatarMode] = useState("livekit");
  const [micOn, setMicOn] = useState(false);
  const [loading, setLoading] = useState(false);
  const videoRef = useRef(null);
  const roomRef = useRef(null);

  const api = useCallback(
    (path, options) =>
      fetch(`${apiBase.replace(/\/$/, "")}${path}`, {
        headers: { "Content-Type": "application/json", ...options?.headers },
        ...options,
      }).then(async (res) => {
        const data = await res.json().catch(() => ({}));
        return { ok: res.ok, status: res.status, data };
      }),
    [apiBase]
  );

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const { data: setup } = await api("/api/avatar/setup", { method: "POST" });
        if (cancelled) return;
        if (setup.agent_id) {
          setAgentId(setup.agent_id);
          setEmbedUrl(setup.embed_url || `https://bey.chat/${setup.agent_id}`);
          localStorage.setItem("nila_bey_agent_id", setup.agent_id);
        }
        if (setup.message) setStatus(setup.message);
        else if (setup.error) setStatus(setup.error);
      } catch (e) {
        if (!cancelled) setStatus(e.message);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [api]);

  const loadAgents = async () => {
    setStatus("Loading agents…");
    const { data } = await api("/api/avatar/agents");
    if (data.error) {
      setStatus(data.error);
      return;
    }
    setAgents(data.agents || []);
    if (data.agents?.length) {
      const first = data.agents[0];
      setAgentId(first.id || "");
      setEmbedUrl(first.embed_url || `https://bey.chat/${first.id}`);
      setStatus(`Found ${data.agents.length} agent(s). Pick one below.`);
    }
  };

  const disconnectLiveKit = async () => {
    setMicOn(false);
    if (roomRef.current) {
      await roomRef.current.disconnect();
      roomRef.current = null;
    }
    if (videoRef.current) {
      videoRef.current.srcObject = null;
    }
  };

  const micHelp = (err) => {
    const name = err?.name || "";
    if (name === "NotAllowedError" || name === "PermissionDeniedError") {
      return (
        "Microphone blocked. Click “Enable microphone & talk”, then allow mic in the " +
        "browser bar (lock icon → Microphone → Allow)."
      );
    }
    return err?.message || String(err);
  };

  const connectLiveKit = async () => {
    setAvatarError("");
    setMicOn(false);
    setStatus("Creating session… (mic prompt appears after you click)");
    try {
      const { data } = await api("/api/avatar/livekit-session", { method: "POST" });
      if (!data.ok) {
        setAvatarError(data.error || "LiveKit session failed");
        if (data.embed_url) setEmbedUrl(data.embed_url);
        setStatus("Use iframe embed instead (agent id may be wrong).");
        return;
      }
      await disconnectLiveKit();
      setStatus("Allow microphone when your browser asks…");
      const room = new Room({
        audioCaptureDefaults: {
          echoCancellation: true,
          noiseSuppression: true,
        },
      });
      roomRef.current = room;
      room.on(RoomEvent.TrackSubscribed, (track) => {
        if (
          videoRef.current &&
          (track.kind === "video" || track.kind === "audio")
        ) {
          track.attach(videoRef.current);
          videoRef.current.muted = false;
        }
      });
      await room.connect(data.livekit_url, data.livekit_token);
      const enabled = await room.localParticipant.setMicrophoneEnabled(true);
      if (!enabled) {
        throw new Error("Microphone could not be enabled.");
      }
      try {
        await room.startAudio();
      } catch (_) {
        /* ignore autoplay quirks */
      }
      setMicOn(true);
      if (data.agent_id) {
        setAgentId(data.agent_id);
        setEmbedUrl(`https://bey.chat/${data.agent_id}`);
      }
      setStatus(
        "Microphone on — speak to the avatar. Use Ask Nila for Supabase answers + resources."
      );
    } catch (e) {
      setAvatarError(micHelp(e));
      setStatus("Click the button again after allowing microphone access.");
      await disconnectLiveKit();
    }
  };

  const askNila = async () => {
    const q = question.trim();
    if (!q) return;
    setLoading(true);
    setAvatarError("");
    setReply("");
    setResources([]);
    setAudioUrl("");
    setStatus("Supabase RAG + ElevenLabs…");

    try {
      const { data } = await api("/api/avatar/ask", {
        method: "POST",
        body: JSON.stringify({
          message: q,
          language,
          history: [],
          session_id: localStorage.getItem("nila_avatar_session"),
        }),
      });

      if (data.session_id) {
        localStorage.setItem("nila_avatar_session", data.session_id);
      }

      setReply(data.reply || "");
      setResources(data.resources || []);

      if (data.audio_base64) {
        setAudioUrl(`data:audio/mpeg;base64,${data.audio_base64}`);
      }

      if (data.embed_url) {
        setEmbedUrl(data.embed_url);
      }

      if (data.avatar_error) {
        setAvatarError(data.avatar_error);
        setStatus(
          "RAG + voice OK. Avatar API failed — use iframe embed on the right."
        );
      } else if (data.livekit_url && data.provider?.livekit_token) {
        setStatus("Answer ready. Connect LiveKit or use embed.");
      } else {
        setStatus("Answer ready — play audio below.");
      }
    } catch (e) {
      setStatus(e.message);
    } finally {
      setLoading(false);
    }
  };

  const onAgentPick = (id) => {
    setAgentId(id);
    localStorage.setItem("nila_bey_agent_id", id);
    setEmbedUrl(`https://bey.chat/${id}`);
  };

  return (
    <div>
      <header style={{ padding: "1rem", maxWidth: 1280, margin: "0 auto" }}>
        <h1 style={{ margin: 0 }}>Nila · Avatar + RAG</h1>
        <p className="muted" style={{ margin: "0.35rem 0 0" }}>
          Supabase RAG + resources on the left. Real-time avatar via Beyond Presence LiveKit
          (speech-to-speech). iframe embed is a fallback.
        </p>
        <label style={{ marginTop: "0.75rem" }}>API base (empty = proxy to :8000)</label>
        <input value={apiBase} onChange={(e) => setApiBase(e.target.value)} />
        <p className={status.includes("fail") ? "err" : "muted"}>{status}</p>
      </header>

      <div className="layout">
        <section>
          <div className="card">
            <label>Your question</label>
            <textarea value={question} onChange={(e) => setQuestion(e.target.value)} />
            <label style={{ marginTop: "0.5rem" }}>Language</label>
            <select value={language} onChange={(e) => setLanguage(e.target.value)}>
              <option value="en">English</option>
              <option value="si">Sinhala</option>
              <option value="ta">Tamil</option>
              <option value="auto">Auto</option>
            </select>
            <div className="row">
              <button className="primary" disabled={loading} onClick={askNila}>
                Ask Nila (RAG + voice)
              </button>
              <button type="button" onClick={loadAgents}>
                List BP agents
              </button>
            </div>
          </div>

          {reply && (
            <div className="card">
              <h3 style={{ marginTop: 0 }}>Answer (Supabase)</h3>
              <p style={{ margin: 0 }}>{reply}</p>
              {audioUrl && (
                <audio controls src={audioUrl} style={{ width: "100%", marginTop: "0.75rem" }} />
              )}
            </div>
          )}

          {avatarError && (
            <div className="card">
              <p className="err" style={{ margin: 0 }}>
                {avatarError}
              </p>
            </div>
          )}

          {agents.length > 0 && (
            <div className="card">
              <h3 style={{ marginTop: 0 }}>Beyond Presence agents</h3>
              {agents.map((a) => (
                <button
                  key={a.id}
                  type="button"
                  style={{
                    display: "block",
                    width: "100%",
                    marginBottom: "0.35rem",
                    textAlign: "left",
                  }}
                  onClick={() => onAgentPick(a.id)}
                >
                  {a.name || "Agent"} — <code>{a.id}</code>
                </button>
              ))}
            </div>
          )}
        </section>

        <section>
          <div className="card">
            <h3 style={{ marginTop: 0 }}>Avatar video</h3>
            <div className="row">
              <label>
                <input
                  type="radio"
                  checked={avatarMode === "livekit"}
                  onChange={() => setAvatarMode("livekit")}
                />{" "}
                LiveKit (real-time talk)
              </label>
              <label>
                <input
                  type="radio"
                  checked={avatarMode === "embed"}
                  onChange={() => {
                    disconnectLiveKit();
                    setAvatarMode("embed");
                  }}
                />{" "}
                iframe embed
              </label>
            </div>
            <label style={{ marginTop: "0.5rem" }}>Agent ID</label>
            <input
              value={agentId}
              onChange={(e) => {
                setAgentId(e.target.value);
                setEmbedUrl(`https://bey.chat/${e.target.value.trim()}`);
              }}
              placeholder="from app.bey.chat/myAgents"
            />
            <div className="row">
              {avatarMode === "livekit" ? (
                <>
                  <button type="button" className="primary" onClick={connectLiveKit}>
                    {micOn ? "Reconnect" : "Enable microphone & talk"}
                  </button>
                  <button type="button" onClick={disconnectLiveKit}>
                    Disconnect
                  </button>
                </>
              ) : (
                <a href={embedUrl} target="_blank" rel="noreferrer">
                  Open bey.chat
                </a>
              )}
            </div>
            <div className="video-wrap" style={{ marginTop: "0.75rem" }}>
              {avatarMode === "embed" && embedUrl ? (
                <iframe
                  title="Beyond Presence"
                  src={embedUrl}
                  allow="camera; microphone; fullscreen"
                />
              ) : (
                <video ref={videoRef} autoPlay playsInline />
              )}
            </div>
            <p className="muted" style={{ marginTop: "0.5rem" }}>
              {micOn ? "Mic is live — the avatar can hear you." : "Click the button above to connect and allow the microphone (required by the browser)."}
            </p>
          </div>
        </section>

        <aside>
          <div className="card">
            <h3 style={{ marginTop: 0 }}>Resources</h3>
            {!resources.length ? (
              <p className="muted">Ask a government question to load forms & offices.</p>
            ) : (
              resources.map((r, i) => (
                <div className="resource" key={`${r.type}-${i}`}>
                  <strong>{r.label || r.type}</strong>
                  {r.name}
                  {r.url && (
                    <div>
                      <a href={r.url} target="_blank" rel="noreferrer">
                        Open
                      </a>
                    </div>
                  )}
                </div>
              ))
            )}
          </div>
        </aside>
      </div>
    </div>
  );
}
