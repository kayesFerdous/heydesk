// Who this browser is, for the chat's limits (D51): a random token kept in the browser.
// The web chat and the Call button both send it.
export const visitorId = (() => {
  try {
    let id = localStorage.getItem("heydesk-visitor");
    if (!id) localStorage.setItem("heydesk-visitor", (id = crypto.randomUUID()));
    return id;
  } catch {
    return crypto.randomUUID();  // storage is blocked: a new visitor each time the page loads
  }
})();
