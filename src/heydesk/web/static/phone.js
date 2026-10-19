// The buyer's phone for the shop's confirmation call (D88). It waits for one mobile number's
// calls, rings, and on Answer opens the same WebRTC call as the chat page's Call button.
import { visitorId } from "./visitor.js";

const $ = (id) => document.getElementById(id);
const show = (id) => {
  document.querySelectorAll("section").forEach((s) => s.classList.toggle("shown", s.id === id));
};

let rings = null;  // the stream of calls for this number
let ringing = null;  // the call ringing now: {call, business, order}
let pc = null;  // the call in progress
let audio = null;  // made on the first tap: a phone only plays sound after one
let wakeLock = null;

// Standby: the time, like a phone's lock screen

const tick = () => {
  const now = new Date();
  $("clock").textContent = now.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  $("date").textContent = now.toLocaleDateString([], { weekday: "long", day: "numeric", month: "long" });
};
setInterval(tick, 1000);
tick();

const saved = (() => {
  try { return localStorage.getItem("heydesk-phone") ?? ""; } catch { return ""; }
})();
$("number").value = saved;

$("setup-form").addEventListener("submit", (event) => {
  event.preventDefault();
  audio ??= new AudioContext();
  navigator.wakeLock?.request("screen").then((lock) => { wakeLock = lock; }).catch(() => {});
  listen($("number").value.trim());
});

$("change").addEventListener("click", (event) => {
  event.preventDefault();
  rings?.close();
  rings = null;
  wakeLock?.release();
  show("setup");
});

const listen = (number) => {
  $("setup-error").textContent = "";
  rings?.close();
  rings = new EventSource(`/api/phone/rings?${new URLSearchParams({ number })}`);
  rings.addEventListener("ready", (e) => {
    const { number: mobile } = JSON.parse(e.data);
    try { localStorage.setItem("heydesk-phone", mobile); } catch {}
    $("my-number").textContent = mobile;
    online(true);
    if (!pc && !ringing) show("standby");
  });
  rings.addEventListener("ring", (e) => ring(JSON.parse(e.data)));
  rings.onerror = async () => {
    online(false);
    if (rings.readyState !== EventSource.CLOSED) return;  // the browser tries again by itself
    // Refused (not a mobile number): ask again to read why
    const res = await fetch(`/api/phone/rings?${new URLSearchParams({ number })}`).catch(() => null);
    const body = await res?.json().catch(() => ({}));
    $("setup-error").textContent = body?.error?.message ?? "Can't reach the server.";
    show("setup");
  };
};

const online = (on) => {
  $("dot").className = `dot ${on ? "on" : "off"}`;
  $("line").textContent = on ? "Waiting for calls" : "Reconnecting…";
};

// Ringing: a ringtone and vibration until Answer or Decline

let tone = null;

const ring = (call) => {
  if (pc || ringing) return;  // busy
  ringing = call;
  const initial = call.business.trim()[0]?.toUpperCase() ?? "?";
  $("ring-initial").textContent = $("call-initial").textContent = initial;
  $("ring-business").textContent = $("call-business").textContent = call.business;
  $("ring-about").textContent = `About your order ${call.order}`;
  show("ringing");
  playTone();
  tone = setInterval(playTone, 3000);
};

const playTone = () => {
  navigator.vibrate?.([800, 400, 800]);
  if (!audio) return;
  const now = audio.currentTime;
  for (const start of [0, 0.4]) {  // two short rings, like a mobile's
    const gain = audio.createGain();
    gain.gain.setValueAtTime(0.0001, now + start);
    gain.gain.exponentialRampToValueAtTime(0.25, now + start + 0.02);
    gain.gain.exponentialRampToValueAtTime(0.0001, now + start + 0.35);
    gain.connect(audio.destination);
    for (const hz of [440, 480]) {
      const osc = audio.createOscillator();
      osc.frequency.value = hz;
      osc.connect(gain);
      osc.start(now + start);
      osc.stop(now + start + 0.36);
    }
  }
};

const stopRinging = () => {
  clearInterval(tone);
  navigator.vibrate?.(0);
  ringing = null;
};

$("decline").addEventListener("click", () => {
  const call = ringing;
  stopRinging();
  show("standby");
  // The shop tries again later (D89)
  if (call) fetch(`/api/phone/decline?${new URLSearchParams({ call: call.call })}`, { method: "POST" });
});

$("answer").addEventListener("click", () => {
  const call = ringing;
  if (!call) return;
  stopRinging();
  $("call-status").textContent = "Connecting…";
  show("in-call");
  answer(call.call).catch((error) => {
    console.error(error);
    hangUp(error.message || "Call failed");
  });
});

// The call: the microphone up, the shop's agent back (as voice.js)

let started = null;
let timer = null;

const answer = async (callId) => {
  const iceServers = await (await fetch("/api/voice/ice")).json();
  pc = new RTCPeerConnection({ iceServers });
  const mic = await navigator.mediaDevices.getUserMedia({ audio: true });
  pc.addTransceiver(mic.getAudioTracks()[0], { direction: "sendrecv" });  // must be first
  const speaker = new Audio();
  speaker.autoplay = true;
  pc.ontrack = (e) => { speaker.srcObject = e.streams[0] ?? new MediaStream([e.track]); };
  const channel = pc.createDataChannel("chat");  // pings let the server notice a hang-up
  const ping = setInterval(() => channel.readyState === "open" && channel.send("ping"), 1000);
  pc.onconnectionstatechange = () => {
    if (pc?.connectionState === "connected") {
      started = Date.now();
      timer = setInterval(showTime, 1000);
      showTime();
    }
    if (["failed", "closed", "disconnected"].includes(pc?.connectionState ?? "closed")) {
      clearInterval(ping);
      hangUp("Call ended");
    }
  };

  await pc.setLocalDescription(await pc.createOffer());
  await new Promise((done) => {  // send every ICE candidate in the offer: no PATCH needed
    if (pc.iceGatheringState === "complete") return done();
    pc.onicegatheringstatechange = () => pc.iceGatheringState === "complete" && done();
  });
  const query = new URLSearchParams({ visitor: visitorId, call: callId });
  const res = await fetch(`/api/voice/offer?${query}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ sdp: pc.localDescription.sdp, type: pc.localDescription.type }),
  });
  if (!res.ok) {  // a 404: the call ended before it was answered
    const body = await res.json().catch(() => ({}));
    throw new Error(body.error?.message ?? `offer failed: ${res.status}`);
  }
  await pc.setRemoteDescription(await res.json());
};

const showTime = () => {
  const s = Math.floor((Date.now() - started) / 1000);
  $("call-status").textContent = `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
};

const hangUp = (said) => {
  pc?.getSenders().forEach((s) => s.track?.stop());
  pc?.close();
  pc = null;
  clearInterval(timer);
  $("call-status").textContent = said;
  setTimeout(() => !pc && !ringing && show("standby"), 1500);
};

$("hang-up").addEventListener("click", () => hangUp("Call ended"));
