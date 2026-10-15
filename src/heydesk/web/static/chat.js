// The dev chat page: POST a message, read the SSE reply from the response body
// (EventSource only does GET). Retired when the Vue app lands.
import { visitorId } from "./visitor.js";

const $ = (id) => document.getElementById(id);
const AGENT_KEY = document.querySelector('meta[name="agent-key"]').content;  // baked in by GET /
let conversationId = null;
let greeting = "";
let teamShown = 0;  // the team's lines already on the page

const add = (cls, text) => {
  const node = document.createElement("div");
  node.className = "msg " + cls;
  node.textContent = text;
  $("log").append(node);
  node.scrollIntoView({ block: "end" });
  return node;
};

const busy = (on) => {
  $("input").disabled = $("send").disabled = on;
  $("waiting").textContent = "";
  $("waiting").className = on ? "dots" : "";
  if (!on) $("input").focus();
};

const handle = (event, data, reply) => {
  switch (event) {
    case "open":
      conversationId = data.conversation_id;
      break;
    case "token":
      $("waiting").textContent = "";
      $("waiting").className = "";
      reply.node ??= add("agent", "");
      reply.node.textContent += data.text;
      reply.node.scrollIntoView({ block: "end" });
      break;
    case "status":
      $("waiting").textContent = data.label;
      break;
    case "handoff": {
      const card = add("card", data.message);
      for (const how of [data.phone, data.email].filter(Boolean)) card.append("\n" + how);
      break;
    }
    case "done":
      reply.done = true;
      break;
    case "error":
      throw new Error(data.message);
  }
};

async function send(message) {
  busy(true);
  const reply = {};
  try {
    const res = await fetch(`/api/chat/${AGENT_KEY}`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ message, conversation_id: conversationId, visitor_id: visitorId }),
    });
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      throw new Error(body.error?.message ?? `${res.status} ${res.statusText}`);
    }
    const reader = res.body.pipeThrough(new TextDecoderStream()).getReader();
    let buffer = "";
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += value;
      let split;
      while ((split = buffer.indexOf("\n\n")) >= 0) {
        const frame = buffer.slice(0, split);
        buffer = buffer.slice(split + 2);
        const name = frame.match(/^event: (.+)$/m)?.[1];
        const data = frame.match(/^data: (.*)$/m)?.[1];
        if (name) handle(name, data ? JSON.parse(data) : {}, reply);
      }
    }
    if (!reply.done) throw new Error("The reply was cut off.");
  } catch (failure) {
    const err = add("err", failure.message);
    const retry = document.createElement("button");
    retry.textContent = "Retry";
    retry.onclick = () => { err.remove(); reply.node?.remove(); send(message); };
    err.append(retry);
  } finally {
    busy(false);
  }
}

$("form").addEventListener("submit", (e) => {
  e.preventDefault();
  const message = $("input").value.trim();
  if (!message) return;
  $("input").value = "";
  add("you", message);
  send(message);
});

// What the business said meanwhile (a payment came in, the order is ready): the server adds it
// to the conversation, and the page asks for it every few seconds (D43, D66)
setInterval(async () => {
  if (!conversationId) return;
  const res = await fetch(`/api/chat/${AGENT_KEY}/${conversationId}/team`).catch(() => null);
  if (!res?.ok) return;
  const { replies } = await res.json();
  for (const line of replies.slice(teamShown)) add("agent", line);
  teamShown = replies.length;
}, 4000);

$("restart").addEventListener("click", () => {
  conversationId = null;
  teamShown = 0;
  $("log").replaceChildren();
  if (greeting) add("agent", greeting);
  $("input").focus();
});

// The greeting shows at once; the first turn stores it as the first assistant message (D8)
(async () => {
  try {
    const res = await fetch(`/api/chat/${AGENT_KEY}`);
    if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
    const agent = await res.json();
    $("agent-name").textContent = agent.name;
    greeting = agent.greeting;
    add("agent", greeting);
  } catch (failure) {
    add("err", `Couldn't load the agent: ${failure.message}`);
  }
})();
