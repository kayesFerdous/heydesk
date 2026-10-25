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

const el = (tag, text, cls = "") => {
  const node = document.createElement(tag);
  node.textContent = text;
  node.className = cls;
  return node;
};

const button = (text, onclick) => {
  const node = el("button", text);
  node.type = "button";
  node.onclick = onclick;
  return node;
};

// A label on the left, a value on the right: "2 × Chicken Biryani   ৳900"
const pair = (label, value, cls = "") => {
  const node = el("div", "", "pair " + cls);
  node.append(el("span", label), el("span", value));
  return node;
};

// A tool's card (D110). Every value is Python's: the page only lays it out
const drawCard = (kind, d) => {
  const box = add("info", "");
  const heading = [d.title, d.number].filter(Boolean).join(" · ");
  if (heading) box.append(el("b", heading));
  for (const line of d.lines ?? []) {
    box.append(pair(`${line.qty ? line.qty + " × " : ""}${line.label}`, line.amount?.text ?? ""));
  }
  for (const row of d.rows ?? []) box.append(pair(row.label, row.value));
  if (d.total) box.append(pair("Total", d.total.text, "total"));
  for (const step of d.steps ?? []) {
    box.append(el("div", `${step.done ? "●" : "○"} ${step.label}`, step.current ? "now" : ""));
  }
  for (const doc of d.doctors ?? []) {
    const item = el("div", `${doc.name}, ${doc.speciality}`, "item");
    item.append(el("div", `${doc.days} · ${doc.hours} · fee ${doc.fee.text}`, "dim"));
    item.append(button("Book", () => say(doc.book)));  // a tap is only its words (D99)
    box.append(item);
  }
  for (const text of [d.status, d.note].filter(Boolean)) box.append(el("div", text, "dim"));
  if (d.pay_url) {
    const pay = el("a", "Pay now");
    pay.href = d.pay_url;
    pay.target = "_blank";
    box.append(pay);
  }
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
    case "photos": {  // products a tool showed (D57): a card each in a row (D125)
      const row = add("photos", "");
      for (const item of data.items) {
        const card = el("div", "", "photo");
        const page = document.createElement("a");  // opens the product's page (D121)
        page.href = `/p/${AGENT_KEY}/${encodeURIComponent(item.code)}?from=web`;
        page.target = "_blank";
        if (item.url) {  // a product with no photo is a card with no picture
          const img = document.createElement("img");
          img.src = item.url;
          img.alt = item.name;
          page.append(img);
        }
        page.append(el("div", `${item.name} · ${item.price}`));
        // Order this says "Order [code]", as the button on Messenger does (D123)
        const taps = el("div", "", "taps");
        const details = el("a", "Details");
        details.href = page.href;
        details.target = "_blank";
        taps.append(button("Order this", () => say(`Order [${item.code}]`)), details);
        if (item.video) {  // its first video (D125)
          const video = el("a", "Video");
          video.href = item.video;
          video.target = "_blank";
          taps.append(video);
        }
        card.append(page, taps);
        row.append(card);
      }
      break;
    }
    case "card":  // what a tool showed (D110): a summary, a receipt, tracking or doctors
      drawCard(data.kind, data.data);
      reply.node = null;  // the words after it go under it
      break;
    case "choices": {  // buttons under the reply: a tap sends its words (D99)
      const row = add("choices", "");
      for (const text of data.items) row.append(button(text, () => { row.remove(); say(text); }));
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

function say(message) {
  if ($("send").disabled) return;  // a reply is still coming
  add("you", message);
  send(message);
}

$("form").addEventListener("submit", (e) => {
  e.preventDefault();
  const message = $("input").value.trim();
  if (!message) return;
  $("input").value = "";
  say(message);
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
    $("call").hidden = !agent.voice;  // only a receptionist or a clinic takes calls (D108)
    greeting = agent.greeting;
    add("agent", greeting);
  } catch (failure) {
    add("err", `Couldn't load the agent: ${failure.message}`);
  }
})();
