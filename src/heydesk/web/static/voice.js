// The Call button: the microphone to the server over WebRTC, the agent's voice back.
const button = document.getElementById("call");
let pc = null;

const hangUp = () => {
  pc?.getSenders().forEach((s) => s.track?.stop());
  pc?.close();
  pc = null;
  button.textContent = "Call";
};

const call = async () => {
  button.textContent = "Connecting…";
  pc = new RTCPeerConnection();
  const mic = await navigator.mediaDevices.getUserMedia({ audio: true });
  pc.addTransceiver(mic.getAudioTracks()[0], { direction: "sendrecv" });  // must be first
  const speaker = new Audio();
  speaker.autoplay = true;
  pc.ontrack = (e) => { speaker.srcObject = e.streams[0] ?? new MediaStream([e.track]); };
  const channel = pc.createDataChannel("chat");  // pings let the server notice a closed tab
  const ping = setInterval(() => channel.readyState === "open" && channel.send("ping"), 1000);
  pc.onconnectionstatechange = () => {
    if (pc?.connectionState === "connected") button.textContent = "Hang up";
    if (["failed", "closed", "disconnected"].includes(pc?.connectionState ?? "closed")) {
      clearInterval(ping);
      hangUp();
    }
  };

  await pc.setLocalDescription(await pc.createOffer());
  await new Promise((done) => {  // send every ICE candidate in the offer: no PATCH needed
    if (pc.iceGatheringState === "complete") return done();
    pc.onicegatheringstatechange = () => pc.iceGatheringState === "complete" && done();
  });
  const res = await fetch("/api/voice/offer", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ sdp: pc.localDescription.sdp, type: pc.localDescription.type }),
  });
  if (!res.ok) throw new Error(`offer failed: ${res.status}`);
  await pc.setRemoteDescription(await res.json());
};

button.addEventListener("click", () => {
  if (pc) return hangUp();
  call().catch((error) => {
    console.error(error);
    hangUp();
    button.textContent = "Call failed: try again";
  });
});
