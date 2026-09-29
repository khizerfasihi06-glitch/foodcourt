(function () {
  const launcher = document.getElementById("chatLauncher");
  const panel = document.getElementById("chatPanel");
  const closeBtn = document.getElementById("chatClose");
  const form = document.getElementById("chatForm");
  const input = document.getElementById("chatInput");
  const messages = document.getElementById("chatMessages");

  const BOT_ICON = `
    <svg viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
      <rect x="4" y="8" width="16" height="11" rx="3" stroke="white" stroke-width="1.8"/>
      <path d="M12 8V5" stroke="white" stroke-width="1.8" stroke-linecap="round"/>
      <circle cx="12" cy="3.5" r="1.1" fill="white"/>
      <circle cx="9" cy="13.5" r="1.2" fill="white"/>
      <circle cx="15" cy="13.5" r="1.2" fill="white"/>
    </svg>`;

  function toggle(open) {
    panel.classList.toggle("open", open);
    if (open) input.focus();
  }

  launcher.addEventListener("click", () => toggle(!panel.classList.contains("open")));
  closeBtn.addEventListener("click", () => toggle(false));

  function scrollToBottom() {
    messages.scrollTop = messages.scrollHeight;
  }

  function addMessage(role, text) {
    const row = document.createElement("div");
    row.className = `msg ${role}`;

    const avatar = document.createElement("span");
    avatar.className = "avatar";
    avatar.innerHTML = role === "bot" ? BOT_ICON : "You";

    const bubble = document.createElement("span");
    bubble.className = "bubble";
    bubble.textContent = text;

    row.appendChild(avatar);
    row.appendChild(bubble);
    messages.appendChild(row);
    scrollToBottom();
    return row;
  }

  function addTyping() {
    const row = document.createElement("div");
    row.className = "msg bot typing";
    row.innerHTML = `<span class="avatar">${BOT_ICON}</span><span class="bubble">Thinking…</span>`;
    messages.appendChild(row);
    scrollToBottom();
    return row;
  }

  form.addEventListener("submit", async function (e) {
    e.preventDefault();
    const text = input.value.trim();
    if (!text) return;

    addMessage("user", text);
    input.value = "";
    const typingRow = addTyping();

    try {
      const res = await fetch("/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: text }),
      });
      const data = await res.json();
      typingRow.remove();

      if (data.error) {
        addMessage("bot", data.error);
        if (data.debug) console.error("Chat error detail:", data.debug);
      } else {
        addMessage("bot", data.response);
      }
    } catch (err) {
      typingRow.remove();
      addMessage("bot", "I couldn't reach the assistant. Check your connection and try again.");
    }
  });
})();