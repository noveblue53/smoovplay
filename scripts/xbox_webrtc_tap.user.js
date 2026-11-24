// ==UserScript==
// @name         SmoovPlay Xbox WebRTC Frame Tap
// @namespace    https://github.com/smoovplay
// @version      0.1.0
// @description  Streams raw compressed WebRTC H.264 frames from xbox.com/play (or Better xCloud / Greenlight) directly into SmoovPlay's low-latency frame tap before browser decode.
// @match        https://play.xbox.com/*
// @match        https://www.xbox.com/*
// @run-at       document-start
// @grant        none
// ==/UserScript==

(function () {
  "use strict";

  const WS_URL = window.SMOOVPLAY_TAP_URL || "ws://127.0.0.1:8088";
  const FLAG_KEYFRAME = 1;
  const FLAG_RECOVERED = 2;
  // Set window.SMOOVPLAY_SKIP_BROWSER_DECODE = true to skip browser video decoding while SmoovPlay is connected.
  const skipDecode = () => Boolean(window.SMOOVPLAY_SKIP_BROWSER_DECODE);
  // Route WebRTC audio straight to AudioContext / dedicated <audio> by default so audio plays even if
  // play.xbox.com starts muted or Skip browser decode stalls <video>'s A/V sync clock.
  const directAudioEnabled = () => window.SMOOVPLAY_DIRECT_AUDIO !== false;

  let ws = null;
  let wsOpen = false;
  let started = false;
  let streamWidth = 1920;
  let streamHeight = 1080;
  let streamFps = 60;
  let audioCtx = null;
  let audioDelayNode = null;
  let audioPuller = null;
  let audioPc = null;
  let currentAudioDelaySec = 0;
  let directAudioActive = false;
  let framesSent = 0;
  let lastKeyframe = null; // { data: Uint8Array, timestamp: number } for instant mid-stream attach
  let videoPc = null;
  const seenMedia = new Set();

  function log(...args) {
    console.log("[smoovplay-tap]", ...args);
  }

  function muteForeignMediaElement(el) {
    if (!el || el.__smoovplayAudio) return;
    seenMedia.add(el);
    if (!el.__smoovplayMuteHooked) {
      el.__smoovplayMuteHooked = true;
      el.addEventListener("volumechange", () => {
        if (directAudioActive && !el.muted) el.muted = true;
      });
    }
    if (directAudioActive && !el.muted) {
      el.muted = true;
    }
  }

  function muteAllPageMedia() {
    if (!directAudioActive) return;
    for (const el of seenMedia) {
      if (!el.__smoovplayAudio && !el.muted) el.muted = true;
    }
    if (document.querySelectorAll) {
      for (const el of document.querySelectorAll("video, audio")) {
        muteForeignMediaElement(el);
      }
    }
  }

  function resumeAudioIfNeeded() {
    if (audioCtx && audioCtx.state === "suspended") {
      audioCtx.resume().catch(() => {});
    }
    if (audioPuller && audioPuller.paused) {
      audioPuller.play().catch(() => {});
    }
  }

  if (window.HTMLMediaElement && HTMLMediaElement.prototype.play) {
    const origPlay = HTMLMediaElement.prototype.play;
    HTMLMediaElement.prototype.play = function (...args) {
      muteForeignMediaElement(this);
      return origPlay.apply(this, args);
    };
  }
  for (const evt of ["pointerdown", "keydown", "click"]) {
    window.addEventListener(evt, resumeAudioIfNeeded, { capture: true, passive: true });
  }

  function connect() {
    if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) {
      return;
    }
    try {
      ws = new WebSocket(WS_URL);
      ws.binaryType = "arraybuffer";
    } catch (err) {
      log("WebSocket creation failed (CSP?):", err);
      setTimeout(connect, 1000);
      return;
    }
    ws.onopen = () => {
      wsOpen = true;
      started = false;
      log("connected to", WS_URL);
      // If Xbox is already streaming, replay the latest keyframe immediately so SmoovPlay attaches at once
      // without waiting for the next keyframe or refreshing the tab.
      if (lastKeyframe) {
        ensureStarted();
        sendRawFrame(lastKeyframe.data, lastKeyframe.timestamp, FLAG_KEYFRAME);
        log("replayed cached keyframe for instant mid-stream attach");
      }
    };
    ws.onmessage = (ev) => {
      if (typeof ev.data !== "string") return;
      try {
        const msg = JSON.parse(ev.data);
        if (msg.type === "audio_delay") {
          const sec = Math.max(0, Math.min(0.5, (Number(msg.ms) || 0) / 1000));
          currentAudioDelaySec = sec;
          if (audioDelayNode) {
            audioDelayNode.delayTime.value = sec;
          }
        }
      } catch {
        // ignore malformed control message
      }
    };
    ws.onclose = () => {
      wsOpen = false;
      started = false;
      if (videoPc !== null) {
        setTimeout(connect, 500);
      }
    };
    ws.onerror = () => {
      try { ws.close(); } catch { /* noop */ }
    };
  }

  function detectVideoSize() {
    const videos = document.querySelectorAll("video");
    for (const v of videos) {
      if (v.videoWidth > 0 && v.videoHeight > 0) {
        return [v.videoWidth, v.videoHeight];
      }
    }
    return [streamWidth, streamHeight];
  }

  function ensureStarted() {
    if (!wsOpen || started) return;
    const [w, h] = detectVideoSize();
    streamWidth = w;
    streamHeight = h;
    ws.send(JSON.stringify({
      type: "start",
      codec: "h264",
      width: streamWidth,
      height: streamHeight,
      fps: streamFps,
      audio_sync: Boolean(audioDelayNode || window.SMOOVPLAY_AUDIO_SYNC),
      probe: true,
    }));
    started = true;
    log(`stream started (${streamWidth}x${streamHeight} @ ${streamFps} fps)`);
  }

  function sendRawFrame(payload, timestamp, flags) {
    if (!wsOpen || payload.byteLength === 0) return;
    // Drop frames if the local socket is somehow backed up rather than adding latency.
    if (ws.bufferedAmount > 2 * 1024 * 1024) return;
    ensureStarted();
    const out = new Uint8Array(12 + payload.byteLength);
    const view = new DataView(out.buffer);
    view.setUint32(0, flags, true);
    view.setUint32(4, timestamp >>> 0, true);
    view.setUint32(8, 0, true); // frames_lost (bridge infers from RTP timestamp gaps)
    out.set(payload, 12);
    ws.send(out.buffer);
    framesSent += 1;
  }

  function sendEncodedFrame(frame) {
    const payload = new Uint8Array(frame.data);
    if (payload.byteLength === 0) return;
    const isKey = frame.type === "key";
    if (isKey) {
      lastKeyframe = { data: payload.slice(), timestamp: frame.timestamp >>> 0 };
    }
    if (!wsOpen) return;
    sendRawFrame(payload, frame.timestamp >>> 0, isKey ? FLAG_KEYFRAME : 0);
  }

  function attachVideoReceiver(receiver) {
    if (receiver.__smoovplayTapped) return;
    receiver.__smoovplayTapped = true;
    if (typeof receiver.createEncodedStreams !== "function") {
      log("receiver.createEncodedStreams() is not available in this browser");
      return;
    }
    try {
      const { readable, writable } = receiver.createEncodedStreams();
      const transform = new TransformStream({
        transform(encodedFrame, controller) {
          sendEncodedFrame(encodedFrame);
          if (!wsOpen || !skipDecode() || encodedFrame.type === "key") {
            controller.enqueue(encodedFrame);
          }
        },
      });
      readable.pipeThrough(transform).pipeTo(writable).catch((err) => {
        log("encoded stream ended:", err && err.message ? err.message : err);
      });
      log("attached WebRTC encoded video stream tap");
      connect();
    } catch (err) {
      log("failed to attach encoded stream:", err);
    }
  }

  function passThroughAudioReceiver(receiver) {
    if (!receiver || receiver.__smoovplayAudioTapped) return;
    receiver.__smoovplayAudioTapped = true;
    if (typeof receiver.createEncodedStreams !== "function") return;
    try {
      const { readable, writable } = receiver.createEncodedStreams();
      readable.pipeTo(writable).catch((err) => {
        log("encoded audio stream ended:", err && err.message ? err.message : err);
      });
      log("attached WebRTC encoded audio pass-through");
    } catch (err) {
      log("failed to pass through encoded audio stream:", err);
    }
  }

  function detachAudio() {
    directAudioActive = false;
    audioDelayNode = null;
    audioPc = null;
    if (audioPuller) {
      try {
        audioPuller.pause();
        audioPuller.srcObject = null;
        if (audioPuller.parentNode) audioPuller.parentNode.removeChild(audioPuller);
      } catch { /* noop */ }
      audioPuller = null;
    }
    if (audioCtx) {
      try { audioCtx.close(); } catch { /* noop */ }
      audioCtx = null;
    }
  }

  function attachAudioTrack(track, pc) {
    if (!directAudioEnabled()) return;
    detachAudio();
    try {
      const stream = new MediaStream([track]);
      // Play the WebRTC audio track directly on a dedicated <audio> element so it stays
      // completely independent of <video> (Skip browser decode won't stall A/V sync) and
      // bypasses play.xbox.com starting muted.
      const el = document.createElement("audio");
      el.__smoovplayAudio = true;
      el.autoplay = true;
      el.muted = false;
      el.volume = 1.0;
      el.srcObject = stream;
      el.style.display = "none";
      if (document.body) {
        document.body.appendChild(el);
      } else if (document.documentElement) {
        document.documentElement.appendChild(el);
      }
      audioPuller = el;
      audioPc = pc;
      window.__smoovplayAudio = el;

      if (window.SMOOVPLAY_AUDIO_SYNC) {
        const AudioCtx = window.AudioContext || window.webkitAudioContext;
        if (AudioCtx) {
          try {
            const ctx = new AudioCtx();
            const src = ctx.createMediaStreamSource(stream);
            const delay = ctx.createDelay(0.5);
            delay.delayTime.value = currentAudioDelaySec;
            src.connect(delay);
            delay.connect(ctx.destination);
            audioCtx = ctx;
            audioDelayNode = delay;
            el.muted = true;
            if (ctx.state === "suspended") {
              ctx.resume().catch(() => {});
            }
            log("routed WebRTC audio via AudioContext + DelayNode");
          } catch (err) {
            log("AudioContext failed, using dedicated <audio>:", err);
            el.muted = false;
            el.volume = 1.0;
          }
        }
      } else {
        log("routed WebRTC audio via dedicated <audio> element (independent of <video>)");
      }

      directAudioActive = true;
      el.play().catch((err) => log("dedicated <audio> play() failed:", err));
      muteAllPageMedia();

      track.addEventListener("unmute", () => {
        resumeAudioIfNeeded();
        muteAllPageMedia();
      });
      track.addEventListener("ended", () => {
        if (audioPuller === el) {
          detachAudio();
        }
      });
    } catch (err) {
      log("direct audio routing failed:", err);
    }
  }

  const NativePC = window.RTCPeerConnection;
  if (!NativePC) {
    log("RTCPeerConnection not present");
    return;
  }

  function SmoovPlayRTCPeerConnection(config, constraints) {
    const nextCfg = Object.assign({}, config || {}, { encodedInsertableStreams: true });
    const pc = constraints !== undefined ? new NativePC(nextCfg, constraints) : new NativePC(nextCfg);
    pc.addEventListener("track", (ev) => {
      if (ev.track && ev.track.kind === "video" && ev.receiver) {
        videoPc = pc;
        attachVideoReceiver(ev.receiver);
      } else if (ev.track && ev.track.kind === "audio") {
        if (ev.receiver) passThroughAudioReceiver(ev.receiver);
        attachAudioTrack(ev.track, pc);
      }
    });
    pc.addEventListener("connectionstatechange", () => {
      if (pc.connectionState === "closed" || pc.connectionState === "disconnected") {
        if (pc === audioPc) {
          detachAudio();
        }
        if (pc === videoPc) {
          videoPc = null;
          lastKeyframe = null;
          if (wsOpen && started) {
            ws.send(JSON.stringify({ type: "stop" }));
            started = false;
          }
          if (ws) {
            try { ws.close(); } catch { /* noop */ }
            ws = null;
          }
        }
      }
    });
    return pc;
  }
  SmoovPlayRTCPeerConnection.prototype = NativePC.prototype;
  Object.setPrototypeOf(SmoovPlayRTCPeerConnection, NativePC);
  window.RTCPeerConnection = SmoovPlayRTCPeerConnection;

  log("hook installed; waiting for WebRTC video track ->", WS_URL);
})();
