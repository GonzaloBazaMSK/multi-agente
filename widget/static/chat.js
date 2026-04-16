/**
 * Widget de chat embebible — Cursos Médicos
 * Uso en WordPress: <script src="https://TU_DOMINIO/widget.js" data-country="AR"></script>
 *
 * Config via atributos data- en el <script>:
 *   data-country   → código de país (AR, MX, CO, PE, CL, UY) — default: AR
 *   data-api-url   → URL del backend (default: origen del script)
 *   data-title     → título del chat (default: "Asesor de Cursos")
 *   data-color     → color primario hex (default: #1a73e8)
 *   data-greeting  → mensaje inicial del bot
 */
(function () {
  "use strict";

  // ─── Configuración desde atributos del script ───────────────────────────
  const scriptEl =
    document.currentScript ||
    document.querySelector('script[src*="chat.js"]') ||
    document.querySelector('script[src*="widget.js"]');

  // Fallback: leer datos del usuario del localStorage (test.html guarda 'customer_user')
  function _readStoredUser() {
    try {
      const raw = localStorage.getItem("customer_user");
      if (!raw) return null;
      return JSON.parse(raw);
    } catch (e) {
      return null;
    }
  }
  const _storedUser = _readStoredUser();

  const CONFIG = {
    country: (scriptEl && scriptEl.getAttribute("data-country")) || "AR",
    apiUrl:
      (scriptEl && scriptEl.getAttribute("data-api-url")) ||
      (scriptEl && new URL(scriptEl.src).origin) ||
      "",
    title:
      (scriptEl && scriptEl.getAttribute("data-title")) ||
      "Asesor de Cursos",
    color:
      (scriptEl && scriptEl.getAttribute("data-color")) || "#1a73e8",
    greeting:
      (scriptEl && scriptEl.getAttribute("data-greeting")) ||
      "¡Hola! Soy tu asesor de cursos médicos. ¿En qué especialidad estás buscando capacitarte?",
    email:
      (scriptEl && scriptEl.getAttribute("data-user-email")) ||
      (scriptEl && scriptEl.getAttribute("data-email")) ||
      (_storedUser && _storedUser.email) ||
      "",
    userName:
      (scriptEl && scriptEl.getAttribute("data-user-name")) ||
      (_storedUser && _storedUser.name) ||
      "",
    userPhone:
      (scriptEl && scriptEl.getAttribute("data-user-phone")) ||
      (_storedUser && _storedUser.phone) ||
      "",
    userCourses:
      (scriptEl && scriptEl.getAttribute("data-user-courses")) ||
      (_storedUser && Array.isArray(_storedUser.courses) ? _storedUser.courses.join(",") : "") ||
      "",
    // Slug del curso que está viendo el usuario (si aplica). Lo usa el router
    // para desambiguar preguntas de pre-compra vs cobranzas.
    pageSlug:
      (scriptEl && scriptEl.getAttribute("data-page-slug")) ||
      (typeof window !== "undefined" && window.MSK_PAGE_SLUG) ||
      "",
    quickReplies: (scriptEl && scriptEl.getAttribute("data-quick-replies")) || "Cursos online 💻|Asesoramiento 🤝",
    avatar: (scriptEl && scriptEl.getAttribute("data-avatar")) || "🩺",
    position: (scriptEl && scriptEl.getAttribute("data-position")) || "right",
  };

  // ─── Session ID (persiste en sessionStorage) ─────────────────────────────
  let sessionId =
    sessionStorage.getItem("cm_session_id") || generateUUID();
  sessionStorage.setItem("cm_session_id", sessionId);

  function generateUUID() {
    return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(
      /[xy]/g,
      function (c) {
        const r = (Math.random() * 16) | 0;
        return (c === "x" ? r : (r & 0x3) | 0x8).toString(16);
      }
    );
  }

  // ─── Mini renderer de markdown ───────────────────────────────────────────
  function renderMarkdown(text) {
    // Escapar HTML primero
    let html = text
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;");

    // Bloques de código ```...```
    html = html.replace(/```[\s\S]*?```/g, (m) => {
      const code = m.slice(3, -3).trim();
      return `<pre><code>${code}</code></pre>`;
    });

    // Negrita **texto** o __texto__
    html = html.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
    html = html.replace(/__(.+?)__/g, "<strong>$1</strong>");

    // Cursiva *texto* o _texto_
    html = html.replace(/\*([^*\n]+?)\*/g, "<em>$1</em>");
    html = html.replace(/_([^_\n]+?)_/g, "<em>$1</em>");

    // Código inline `...`
    html = html.replace(/`([^`]+)`/g, "<code>$1</code>");

    // Links [texto](url)
    html = html.replace(/\[([^\]]+)\]\((https?:\/\/[^\)]+)\)/g,
      '<a href="$2" target="_blank" rel="noopener">$1</a>');

    // Listas con - o •
    html = html.replace(/((?:^|\n)[•\-] .+)+/g, (block) => {
      const items = block.trim().split(/\n/).map(line =>
        `<li>${line.replace(/^[•\-] /, "")}</li>`
      ).join("");
      return `<ul>${items}</ul>`;
    });

    // Saltos de línea (fuera de listas/pre)
    html = html.replace(/\n/g, "<br>");
    // Limpiar <br> dentro de <ul>/<li>
    html = html.replace(/<br>(<\/?(?:ul|li)>)/g, "$1");
    html = html.replace(/(<\/?(?:ul|li)>)<br>/g, "$1");

    return html;
  }

  // ─── Parser de tag [BUTTONS: opt1 | opt2 | opt3] ─────────────────────────
  function parseButtonsTag(text) {
    const match = text.match(/\[BUTTONS:\s*(.+?)\]/i);
    if (!match) return { clean: text, buttons: [] };
    const raw = match[1];
    const buttons = raw.split('|').map(s => s.trim()).filter(Boolean);
    const clean = (text.slice(0, match.index) + text.slice(match.index + match[0].length)).trim();
    return { clean, buttons };
  }

  // ─── Inyectar CSS ─────────────────────────────────────────────────────────
  function injectCSS() {
    if (document.getElementById("cm-widget-css")) return;
    const link = document.createElement("link");
    link.id = "cm-widget-css";
    link.rel = "stylesheet";
    link.href = `${CONFIG.apiUrl}/static/chat.css`;
    document.head.appendChild(link);

    // Aplicar color primario custom si difiere del default
    if (CONFIG.color !== "#1a73e8") {
      const style = document.createElement("style");
      style.textContent = `:root { --cm-primary: ${CONFIG.color}; --cm-primary-dark: ${darkenColor(CONFIG.color, 20)}; }`;
      document.head.appendChild(style);
    }
  }

  function darkenColor(hex, amount) {
    const num = parseInt(hex.replace("#", ""), 16);
    const r = Math.max(0, (num >> 16) - amount);
    const g = Math.max(0, ((num >> 8) & 0xff) - amount);
    const b = Math.max(0, (num & 0xff) - amount);
    return "#" + ((r << 16) | (g << 8) | b).toString(16).padStart(6, "0");
  }

  // ─── HTML del widget ──────────────────────────────────────────────────────
  function buildHTML() {
    return `
<div id="cm-widget-container">
  <div id="cm-panel" role="dialog" aria-label="Chat de soporte">
    <div id="cm-header">
      <div id="cm-avatar">${CONFIG.avatar && (CONFIG.avatar.startsWith('http') || CONFIG.avatar.startsWith('/')) ? '<img src="'+CONFIG.avatar+'" style="width:100%;height:100%;object-fit:cover;border-radius:50%">' : (CONFIG.avatar || '🩺')}</div>
      <div id="cm-header-text">
        <div id="cm-header-name">${CONFIG.title}</div>
        <div id="cm-header-status">● En línea</div>
      </div>
      <button id="cm-close-btn" aria-label="Cerrar chat">
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5">
          <line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>
        </svg>
      </button>
    </div>
    <div id="cm-country-bar">
      <span>País:</span>
      <select id="cm-country-select">
        <option value="AR" ${CONFIG.country==="AR"?"selected":""}>🇦🇷 Argentina</option>
        <option value="MX" ${CONFIG.country==="MX"?"selected":""}>🇲🇽 México</option>
        <option value="CO" ${CONFIG.country==="CO"?"selected":""}>🇨🇴 Colombia</option>
        <option value="PE" ${CONFIG.country==="PE"?"selected":""}>🇵🇪 Perú</option>
        <option value="CL" ${CONFIG.country==="CL"?"selected":""}>🇨🇱 Chile</option>
        <option value="UY" ${CONFIG.country==="UY"?"selected":""}>🇺🇾 Uruguay</option>
      </select>
    </div>
    <div id="cm-messages" role="log" aria-live="polite"></div>
    <div id="cm-typing">
      <span class="cm-dot"></span><span class="cm-dot"></span><span class="cm-dot"></span>
    </div>
    <div id="cm-input-area">
      <textarea
        id="cm-input"
        rows="1"
        placeholder="Escribí tu consulta..."
        aria-label="Escribí tu mensaje"
        maxlength="1000"
      ></textarea>
      <button id="cm-send-btn" aria-label="Enviar mensaje">
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#fff" stroke-width="2.5">
          <line x1="22" y1="2" x2="11" y2="13"/>
          <polygon points="22 2 15 22 11 13 2 9 22 2"/>
        </svg>
      </button>
    </div>
  </div>
  <div id="cm-badge"></div>
  <button id="cm-fab" aria-label="Abrir chat de soporte">
    <svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="#fff" stroke-width="2">
      <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>
    </svg>
  </button>
</div>`;
  }

  // ─── Estado ───────────────────────────────────────────────────────────────
  let isOpen = false;
  let isLoading = false;
  let country = CONFIG.country;
  let unreadCount = 0;
  // Conversación aún no materializada en el backend:
  let pendingGreeting = null;          // saludo stateless ya mostrado (se persiste cuando el user mande el 1er msg)
  let conversationMaterialized = false; // true cuando ya hay al menos un mensaje de user
  let lastKnownEmail = CONFIG.email || "";  // para detectar cambios de login/logout
  let greetingBubbleEl = null;         // ref al <div> del saludo, para poder reemplazarlo si cambia el login

  // ─── DOM refs (asignados después de mount) ────────────────────────────────
  let panel, fab, messagesEl, typingEl, inputEl, sendBtn, badge, countrySelect;

  // ─── Render de mensajes ───────────────────────────────────────────────────
  function appendMessage(role, text, timestamp, mediaUrl, mediaType, mediaMime) {
    if (!messagesEl) return;
    if (!mediaUrl && (text === null || text === undefined || text === '')) return;
    const now = timestamp || new Date().toLocaleTimeString("es", { hour: "2-digit", minute: "2-digit" });
    const div = document.createElement("div");
    div.className = `cm-msg ${role}`;

    let displayText = String(text || "");
    let buttons = [];
    let mediaHtml = '';

    // Media rendering
    if (mediaUrl) {
      let url = mediaUrl;
      if (!url.startsWith('http') && !url.startsWith('/')) {
        const base = CONFIG.apiUrl ? CONFIG.apiUrl.replace('/api','') : '';
        url = base + '/' + url;
      }
      const mt = (mediaType || '').toLowerCase();
      if (mt === 'audio') {
        const audioMime = mediaMime || 'audio/webm';
        mediaHtml = `<div style="margin-bottom:4px;padding:6px;background:#f0f0f0;border-radius:12px;display:block !important;"><audio controls preload="auto" style="width:220px;height:40px;display:block !important;visibility:visible !important;opacity:1 !important;"><source src="${escapeHTML(url)}" type="${escapeHTML(audioMime)}"><source src="${escapeHTML(url)}">Audio no soportado.</audio></div>`;
        if (displayText.startsWith('[Audio')) displayText = '';
      } else if (mt === 'image') {
        mediaHtml = `<div style="margin-bottom:4px"><img src="${escapeHTML(url)}" style="max-width:200px;border-radius:8px;cursor:pointer" onclick="window.open('${escapeHTML(url)}','_blank')"></div>`;
        if (displayText.startsWith('[Imagen')) displayText = '';
      } else if (mt === 'video') {
        mediaHtml = `<div style="margin-bottom:4px"><video controls src="${escapeHTML(url)}" style="max-width:200px;border-radius:8px"></video></div>`;
        if (displayText.startsWith('[Video')) displayText = '';
      }
    }

    if (role === "bot") {
      const parsed = parseButtonsTag(displayText);
      displayText = parsed.clean;
      buttons = parsed.buttons;
    }

    if (!displayText.trim() && buttons.length === 0 && !mediaHtml) return;

    const bubbleContent = displayText
      ? (role === "bot" ? renderMarkdown(displayText) : escapeHTML(displayText))
      : '';

    div.innerHTML = `
      ${mediaHtml}
      ${bubbleContent ? `<div class="cm-bubble cm-markdown">${bubbleContent}</div>` : ''}
      <div class="cm-time">${now}</div>
    `;
    messagesEl.appendChild(div);

    if (buttons.length > 0) {
      const qrDiv = document.createElement("div");
      qrDiv.className = "cm-quick-replies";
      buttons.forEach(function (btnText) {
        const btn = document.createElement("button");
        btn.className = "cm-qr-btn";
        btn.textContent = btnText;
        btn.addEventListener("click", function () {
          qrDiv.style.display = "none";
          sendMessage(btnText);
        });
        qrDiv.appendChild(btn);
      });
      messagesEl.appendChild(qrDiv);
    }

    messagesEl.scrollTop = messagesEl.scrollHeight;

    if (role === "bot" && !isOpen) {
      unreadCount++;
      badge.textContent = unreadCount;
      badge.style.display = "flex";
    }
    return div;  // para poder reemplazar/quitar el bubble después (ej. refresh del saludo)
  }

  // ─── Mostrar botones de respuesta rápida inicial ──────────────────────────
  function showQuickReplies(btns) {
    if (!btns || btns.length === 0) return;
    const qrDiv = document.createElement("div");
    qrDiv.className = "cm-quick-replies";
    btns.forEach(function (btnText) {
      const btn = document.createElement("button");
      btn.className = "cm-qr-btn";
      btn.textContent = btnText;
      btn.addEventListener("click", function () {
        qrDiv.style.display = "none";
        sendMessage(btnText);
      });
      qrDiv.appendChild(btn);
    });
    messagesEl.appendChild(qrDiv);
    messagesEl.scrollTop = messagesEl.scrollHeight;
  }

  function escapeHTML(text) {
    return text
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/\n/g, "<br>");
  }

  function showTyping() {
    typingEl.classList.add("visible");
    messagesEl.scrollTop = messagesEl.scrollHeight;
  }

  function hideTyping() {
    typingEl.classList.remove("visible");
  }

  // ─── API call ─────────────────────────────────────────────────────────────
  async function sendMessage(text) {
    if (isLoading || !text.trim()) return;
    isLoading = true;
    sendBtn.disabled = true;

    appendMessage("user", text);
    lastMsgCount++; // contar mensaje de usuario inmediatamente (evita duplicados del poll)
    showTyping();

    // Si todavía no materializamos la conversación, mandamos el saludo
    // pendiente para que el backend lo persista como primer bot msg.
    const payload = {
      session_id: sessionId,
      message: text,
      country: country,
      user_email: CONFIG.email,
      user_name: CONFIG.userName,
      user_phone: CONFIG.userPhone,
      user_courses: CONFIG.userCourses,
      page_slug: CONFIG.pageSlug,
    };
    if (pendingGreeting) {
      payload.initial_greeting = pendingGreeting;
    }

    try {
      const res = await fetch(`${CONFIG.apiUrl}/widget/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });

      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();

      // Primer mensaje exitoso → conv materializada. No volvemos a mandar
      // initial_greeting ni a dejar que el identity watcher reemplace el saludo.
      conversationMaterialized = true;

      hideTyping();
      if (data.response) {
        appendMessage("bot", data.response);
        lastMsgCount++; // contar respuesta del bot
      }

      // El saludo (si estaba pendiente) se persistió en backend como primer
      // bot msg de la conv. Lo contamos en lastMsgCount para que el poll no
      // lo traiga como "mensaje nuevo" duplicado.
      if (pendingGreeting) {
        lastMsgCount++;
        pendingGreeting = null;
      }

      if (data.handoff_requested) {
        appendMessage("bot", "📞 Un asesor humano continuará esta conversación pronto.");
        startPolling();
      }

      // If bot_disabled (agent took over), start polling for agent replies
      if (data.bot_disabled) {
        startPolling();
      }

      // Guardar session_id si el backend lo asignó
      if (data.session_id) {
        sessionId = data.session_id;
        sessionStorage.setItem("cm_session_id", sessionId);
      }
    } catch (err) {
      hideTyping();
      appendMessage(
        "bot",
        "Lo siento, tuve un problema técnico. Por favor intentá de nuevo en unos segundos."
      );
      console.error("[CM Widget]", err);
    } finally {
      isLoading = false;
      sendBtn.disabled = false;
      inputEl.focus();
    }
  }

  // ─── Cargar historial previo ──────────────────────────────────────────────
  async function loadHistory() {
    try {
      const res = await fetch(
        `${CONFIG.apiUrl}/widget/history/${sessionId}`
      );
      if (!res.ok) return;
      const data = await res.json();
      if (data.messages && data.messages.length > 0) {
        data.messages.forEach((m) => {
          const role = m.role === "user" ? "user" : "bot";
          const time = new Date(m.timestamp).toLocaleTimeString("es", {
            hour: "2-digit",
            minute: "2-digit",
          });
          appendMessage(role, m.content, time, m.media_url, m.media_type, m.media_mime);
        });
        lastMsgCount = data.messages.length;
        return true;
      }
    } catch (e) {
      // Silencioso — si no hay historial, se muestra el saludo
    }
    return false;
  }

  // ─── Toggle panel ─────────────────────────────────────────────────────────
  function togglePanel() {
    isOpen = !isOpen;
    if (isOpen) {
      panel.classList.add("open");
      unreadCount = 0;
      badge.style.display = "none";
      inputEl.focus();
    } else {
      panel.classList.remove("open");
    }
  }

  // ─── Auto-resize textarea ─────────────────────────────────────────────────
  function autoResize() {
    inputEl.style.height = "auto";
    inputEl.style.height = Math.min(inputEl.scrollHeight, 100) + "px";
  }

  // ─── Load remote widget config from admin panel ────────────────────────────
  async function loadRemoteConfig() {
    try {
      const res = await fetch(`${CONFIG.apiUrl}/admin/flows/widget-config/public`);
      if (!res.ok) return;
      const cfg = await res.json();
      if (cfg.title) CONFIG.title = cfg.title;
      if (cfg.color && cfg.color !== '#1a73e8') CONFIG.color = cfg.color;
      if (cfg.greeting) CONFIG.greeting = cfg.greeting;
      if (cfg.avatar) CONFIG.avatar = cfg.avatar;
      if (cfg.quick_replies) CONFIG.quickReplies = cfg.quick_replies;
      if (cfg.position) CONFIG.position = cfg.position;
    } catch(e) { /* silent */ }
  }

  // ─── Mount ────────────────────────────────────────────────────────────────
  async function mount() {
    await loadRemoteConfig();
    injectCSS();

    const container = document.createElement("div");
    container.innerHTML = buildHTML();
    document.body.appendChild(container.firstElementChild);

    // Apply position
    if (CONFIG.position === 'left') {
      const el = document.getElementById('cm-widget-container');
      if (el) { el.style.right = 'auto'; el.style.left = '24px'; }
    }

    // DOM refs
    panel = document.getElementById("cm-panel");
    fab = document.getElementById("cm-fab");
    messagesEl = document.getElementById("cm-messages");
    typingEl = document.getElementById("cm-typing");
    inputEl = document.getElementById("cm-input");
    sendBtn = document.getElementById("cm-send-btn");
    badge = document.getElementById("cm-badge");
    countrySelect = document.getElementById("cm-country-select");

    // Event listeners
    fab.addEventListener("click", togglePanel);
    document.getElementById("cm-close-btn").addEventListener("click", togglePanel);

    countrySelect.addEventListener("change", function () {
      country = this.value;
    });

    sendBtn.addEventListener("click", function () {
      const text = inputEl.value.trim();
      if (text) {
        inputEl.value = "";
        autoResize();
        sendMessage(text);
      }
    });

    inputEl.addEventListener("keydown", function (e) {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        const text = this.value.trim();
        if (text) {
          this.value = "";
          autoResize();
          sendMessage(text);
        }
      }
    });

    inputEl.addEventListener("input", autoResize);

    // Cerrar con Escape
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape" && isOpen) togglePanel();
    });

    // ── Resolver estado inicial ──────────────────────────────────────────
    // 1) Si sessionStorage tiene session_id y YA hay historial real → cargarlo
    //    (usuario recargó la pestaña, misma conv)
    // 2) Esperar datos del user (login async)
    // 3) Si el user tiene email → tryResumeByEmail (trae conv previa de otro
    //    dispositivo / sesión expirada, si existe dentro de 30 días)
    // 4) Si nada anterior → fetchPersonalizedGreeting (STATELESS, no crea conv)

    const hadHistory = await loadHistory();
    if (hadHistory) {
      lastMsgCount = messagesEl.querySelectorAll('.cm-msg').length;
      conversationMaterialized = true;
      lastKnownEmail = CONFIG.email || "";
      startPolling();
    } else {
      await waitForUserData(2000);
      lastKnownEmail = CONFIG.email || "";
      let resumed = false;
      if (CONFIG.email) {
        resumed = await tryResumeByEmail();
        if (resumed) startPolling();
      }
      if (!resumed) {
        await fetchPersonalizedGreeting();
      }
    }

    // Detectar cambios de identity (login tardío, logout, cambio de cuenta)
    startIdentityWatcher();
  }

  // ─── Espera a que msk-front setee los datos del usuario ───────────────────
  // Revisa cada 200ms durante `timeoutMs` si apareció email en:
  //   1. window.CM_USER  = { email, name, phone, courses }
  //   2. scriptEl data-user-email / data-email (por si se setea async)
  //   3. meta tag <meta name="msk-user-email" content="...">
  async function waitForUserData(timeoutMs) {
    if (CONFIG.email) return; // ya lo tenemos, no esperar
    const start = Date.now();
    while (Date.now() - start < timeoutMs) {
      // 1. window.CM_USER
      if (window.CM_USER && window.CM_USER.email) {
        CONFIG.email = window.CM_USER.email;
        CONFIG.userName = window.CM_USER.name || CONFIG.userName;
        CONFIG.userPhone = window.CM_USER.phone || CONFIG.userPhone;
        CONFIG.userCourses = window.CM_USER.courses || CONFIG.userCourses;
        return;
      }
      // 2. data-* attribute se actualizó
      if (scriptEl) {
        const e2 = scriptEl.getAttribute("data-user-email") || scriptEl.getAttribute("data-email");
        if (e2) {
          CONFIG.email = e2;
          const n2 = scriptEl.getAttribute("data-user-name");
          if (n2) CONFIG.userName = n2;
          const c2 = scriptEl.getAttribute("data-user-courses");
          if (c2) CONFIG.userCourses = c2;
          return;
        }
      }
      // 3. meta tag
      const meta = document.querySelector('meta[name="msk-user-email"]');
      if (meta && meta.content) {
        CONFIG.email = meta.content;
        return;
      }
      // 4. localStorage (test.html guarda customer_user)
      const u = _readStoredUser();
      if (u && u.email) {
        CONFIG.email = u.email;
        if (u.name) CONFIG.userName = u.name;
        if (u.phone) CONFIG.userPhone = u.phone;
        if (Array.isArray(u.courses)) CONFIG.userCourses = u.courses.join(",");
        return;
      }
      await new Promise(r => setTimeout(r, 200));
    }
  }

  // ─── Polling para mensajes del agente humano ──────────────────────────────
  let pollTimer = null;
  let lastMsgCount = 0;

  function startPolling() {
    if (pollTimer) return;
    pollTimer = setInterval(pollNewMessages, 3000);
  }

  function stopPolling() {
    if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
  }

  async function pollNewMessages() {
    if (!sessionId || isLoading) return; // no pollear mientras se procesa un mensaje (evita duplicados)
    try {
      const res = await fetch(`${CONFIG.apiUrl}/widget/history/${sessionId}`);
      if (!res.ok) return;
      const data = await res.json();
      const msgs = data.messages || [];
      if (msgs.length > lastMsgCount) {
        // Render only new messages
        const newMsgs = msgs.slice(lastMsgCount);
        newMsgs.forEach(m => {
          // Skip user messages (already rendered locally)
          if (m.role === 'user') { lastMsgCount++; return; }
          const role = 'bot';
          const time = new Date(m.timestamp).toLocaleTimeString("es", { hour: "2-digit", minute: "2-digit" });
          appendMessage(role, m.content, time, m.media_url, m.media_type, m.media_mime);
          lastMsgCount++;
        });
        // Hide typing if bot/agent responded
        hideTyping();
        // Notify if panel closed
        if (!isOpen && newMsgs.some(m => m.role === 'assistant')) {
          unreadCount += newMsgs.filter(m => m.role === 'assistant').length;
          badge.textContent = unreadCount;
          badge.style.display = "flex";
        }
      } else {
        lastMsgCount = msgs.length;
      }
    } catch(e) { /* silent */ }
  }

  // ─── Saludo stateless — NO crea conversación en el backend ─────────────────
  // El backend solo genera el saludo (con perfil Supabase + Zoho si hay email)
  // y lo devuelve. Lo guardamos en `pendingGreeting` y lo renderizamos.
  // La conversación se materializa recién cuando el user manda el primer msg
  // real (ahí le pasamos `initial_greeting` para que el backend lo persista
  // como primer bot msg del historial).
  async function fetchPersonalizedGreeting() {
    showTyping();
    try {
      const res = await fetch(`${CONFIG.apiUrl}/widget/greeting`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          user_email: CONFIG.email,
          user_name: CONFIG.userName,
          user_courses: CONFIG.userCourses,
          page_slug: CONFIG.pageSlug,
          country: country,
        }),
      });
      if (!res.ok) throw new Error("HTTP " + res.status);
      const data = await res.json();
      hideTyping();
      const greetingText = data.greeting || CONFIG.greeting;
      pendingGreeting = greetingText;
      greetingBubbleEl = appendMessage("bot", greetingText);
    } catch (err) {
      hideTyping();
      const fallbackGreeting = CONFIG.greeting || "¡Hola! 😊 Soy tu asistente virtual de MSK. Estoy aquí para guiarte.";
      pendingGreeting = fallbackGreeting;
      greetingBubbleEl = appendMessage("bot", fallbackGreeting);
      const fallbackBtns = (CONFIG.quickReplies || "Explorar cursos 📖|Asistencia 📩 💻")
        .split('|').map(s => s.trim()).filter(Boolean);
      showQuickReplies(fallbackBtns);
      console.error("[CM Widget] greeting failed:", err);
    }
  }

  // ─── Resume-by-email: busca conv previa de este user (últimos 30 días) ────
  // Solo para usuarios logueados. Si existe, adopta ese session_id y carga
  // el historial. Devuelve true si retomó, false si no hay nada.
  async function tryResumeByEmail() {
    if (!CONFIG.email) return false;
    try {
      const res = await fetch(
        `${CONFIG.apiUrl}/widget/resume?email=${encodeURIComponent(CONFIG.email)}`
      );
      if (!res.ok) return false;
      const data = await res.json();
      if (!data.session_id || !data.messages || data.messages.length === 0) {
        return false;
      }
      // Adoptamos el session_id histórico y lo guardamos en sessionStorage,
      // pisando cualquier session_id anónimo anterior (C4/A5).
      sessionId = data.session_id;
      sessionStorage.setItem("cm_session_id", sessionId);
      data.messages.forEach((m) => {
        const role = m.role === "user" ? "user" : "bot";
        const time = new Date(m.timestamp).toLocaleTimeString("es", {
          hour: "2-digit",
          minute: "2-digit",
        });
        appendMessage(role, m.content, time, m.media_url, m.media_type, m.media_mime);
      });
      lastMsgCount = data.messages.length;
      conversationMaterialized = true;
      pendingGreeting = null;
      return true;
    } catch (e) {
      return false;
    }
  }

  // ─── Re-fetch del saludo cuando cambia el login (mid-session) ─────────────
  // Solo aplica si todavía no se materializó la conversación — si ya hubo
  // mensajes del user, el saludo viejo queda congelado en el historial.
  async function refreshGreetingForNewUser() {
    if (conversationMaterialized) return;
    try {
      const res = await fetch(`${CONFIG.apiUrl}/widget/greeting`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          user_email: CONFIG.email,
          user_name: CONFIG.userName,
          user_courses: CONFIG.userCourses,
          page_slug: CONFIG.pageSlug,
          country: country,
        }),
      });
      if (!res.ok) return;
      const data = await res.json();
      pendingGreeting = data.greeting;
      // Reemplazar el bubble del saludo (+ sus botones si tiene) en el DOM
      if (greetingBubbleEl && greetingBubbleEl.parentNode) {
        const next = greetingBubbleEl.nextElementSibling;
        if (next && next.classList && next.classList.contains("cm-quick-replies")) {
          next.remove();
        }
        greetingBubbleEl.remove();
      }
      greetingBubbleEl = appendMessage("bot", data.greeting);
    } catch (e) { /* silent */ }
  }

  // ─── Watcher: detecta cambios de identity (login / logout / cambio user) ──
  // Revisa window.CM_USER y localStorage.customer_user cada 1s. Si el email
  // cambia respecto de `lastKnownEmail`, dispara acciones:
  //   - logout (había email, ahora no) → re-fetch saludo genérico
  //   - login (no había, ahora sí)     → re-fetch saludo + intentar resume
  //   - cambio de cuenta (email distinto) → nuevo session_id + resume
  function startIdentityWatcher() {
    setInterval(async () => {
      const storedNow = _readStoredUser();
      const cmUser = window.CM_USER || {};
      const currentEmail =
        cmUser.email ||
        (scriptEl && scriptEl.getAttribute("data-user-email")) ||
        (storedNow && storedNow.email) ||
        "";
      if (currentEmail === lastKnownEmail) return;

      // Hubo cambio
      const prev = lastKnownEmail;
      lastKnownEmail = currentEmail;
      // Actualizar CONFIG
      CONFIG.email = currentEmail;
      CONFIG.userName =
        cmUser.name ||
        (scriptEl && scriptEl.getAttribute("data-user-name")) ||
        (storedNow && storedNow.name) ||
        CONFIG.userName;
      CONFIG.userCourses =
        cmUser.courses ||
        (scriptEl && scriptEl.getAttribute("data-user-courses")) ||
        (storedNow && Array.isArray(storedNow.courses) ? storedNow.courses.join(",") : "") ||
        CONFIG.userCourses;

      if (prev && currentEmail && prev !== currentEmail) {
        // B6: cambio de cuenta → arrancar limpio
        await resetConversationForAccountSwitch();
        return;
      }
      if (!prev && currentEmail) {
        // Login fresco
        if (!conversationMaterialized) {
          // Intentar retomar history del nuevo usuario
          const resumed = await tryResumeByEmail();
          if (!resumed) await refreshGreetingForNewUser();
        }
        // Si ya hay conv en curso (B4), el email se pegará automáticamente
        // al próximo POST /widget/chat (user_email ya va en el body).
        return;
      }
      if (prev && !currentEmail) {
        // Logout
        if (!conversationMaterialized) {
          await refreshGreetingForNewUser();
        }
      }
    }, 1000);
  }

  // ─── B6: cambio de cuenta → limpia UI y arranca nueva sesión ──────────────
  async function resetConversationForAccountSwitch() {
    // Stop polling si estaba activo
    stopPolling();
    // Limpiar DOM
    messagesEl.innerHTML = "";
    lastMsgCount = 0;
    conversationMaterialized = false;
    pendingGreeting = null;
    greetingBubbleEl = null;
    // Nuevo session_id
    sessionId = generateUUID();
    sessionStorage.setItem("cm_session_id", sessionId);
    // Intentar resume del nuevo email
    const resumed = await tryResumeByEmail();
    if (!resumed) {
      await fetchPersonalizedGreeting();
    } else {
      startPolling();
    }
  }

  // ─── Init ─────────────────────────────────────────────────────────────────
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", mount);
  } else {
    mount();
  }
})();
