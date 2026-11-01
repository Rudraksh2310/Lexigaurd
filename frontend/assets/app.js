/* LexiGuard AI — shared browser helpers (no framework).
   Everything that shows server or AI text builds DOM nodes with textContent,
   never innerHTML, so document text can't inject markup. */
(function () {
  "use strict";
  const API = (window.LEXIGUARD_API_BASE || "").replace(/\/$/, "");
  const LG = (window.LG = {});

  // ------------------------------------------------------------ DOM helper
  LG.el = function el(tag, attrs, ...kids) {
    const svgTags = ["svg", "path", "circle", "rect", "g", "text", "line", "polyline"];
    const n = svgTags.includes(tag) ? document.createElementNS("http://www.w3.org/2000/svg", tag) : document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (v == null || v === false) continue;
      if (k === "class") n.setAttribute("class", v);
      else if (k === "text") n.textContent = v;
      else if (k.startsWith("on") && typeof v === "function") n.addEventListener(k.slice(2), v);
      else n.setAttribute(k, v === true ? "" : v);
    }
    for (const kid of kids.flat(Infinity)) {
      if (kid == null || kid === false) continue;
      n.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
    }
    return n;
  };
  const el = LG.el;

  LG.icon = function (name) {
    const P = {
      download: "M12 4v11m0 0l-4.5-4.5M12 15l4.5-4.5M5 19h14",
      play: "M8 5.5v13l11-6.5z",
      stop: "M7 7h10v10H7z",
      send: "M4 12l16-8-6 16-2.5-6.5z",
      trash: "M5 7h14M10 7V5h4v2m-7 0l1 12h8l1-12",
      chat: "M5 5h14v10H9l-4 4z",
      back: "M15 5l-7 7 7 7",
      upload: "M12 19V8m0 0l-4.5 4.5M12 8l4.5 4.5M5 5h14",
      file: "M7 3h7l5 5v13H7zM14 3v5h5",
      menu: "M4 7h16M4 12h16M4 17h16",
      x: "M6 6l12 12M18 6L6 18",
      search: "M11 18a7 7 0 100-14 7 7 0 000 14zm9 2l-4-4",
      sparkle: "M12 3v6m0 6v6M3 12h6m6 0h6",
      check: "M5 12.5l4.5 4.5L19 7",
      mic: "M12 4a3 3 0 00-3 3v5a3 3 0 006 0V7a3 3 0 00-3-3zM6 11.5a6 6 0 0012 0M12 17.5V20",
      down: "M12 5v14m0 0l-6-6m6 6l6-6",
      edit: "M5 19h4L19 9l-4-4L5 15zM13.5 6.5l4 4",
      eye: "M2.5 12S6 5.5 12 5.5 21.5 12 21.5 12 18 18.5 12 18.5 2.5 12 2.5 12zM12 15a3 3 0 100-6 3 3 0 000 6z",
      copy: "M8 8h11v11H8zM5 16V5h11",
      print: "M7 9V4h10v5M7 17H5v-6h14v6h-2M8 14h8v6H8z",
      refresh: "M19 8a7.5 7.5 0 00-13.5 1M5 16a7.5 7.5 0 0013.5-1M19 4v4h-4M5 20v-4h4",
    };
    const svg = el("svg", { viewBox: "0 0 24 24", fill: name === "play" || name === "stop" ? "currentColor" : "none", stroke: "currentColor", "stroke-width": "1.9", "stroke-linecap": "round", "stroke-linejoin": "round", "aria-hidden": "true" });
    svg.append(el("path", { d: P[name] || "" }));
    return svg;
  };

  // The brand mark: a round notary-style seal
  LG.seal = function (size = 32) {
    const s = el("svg", { viewBox: "0 0 40 40", width: size, height: size, "aria-hidden": "true" });
    s.append(
      el("circle", { cx: 20, cy: 20, r: 18.5, fill: "#2F6B4F" }),
      el("circle", { cx: 20, cy: 20, r: 15.2, fill: "none", stroke: "#E1ECE4", "stroke-width": 1, "stroke-dasharray": "1.6 1.4" }),
      el("path", { d: "M14 12.5v14h7.5", fill: "none", stroke: "#fff", "stroke-width": 2.6, "stroke-linecap": "round", "stroke-linejoin": "round" }),
      el("path", { d: "M18.5 21.5l3 3 6-7.5", fill: "none", stroke: "#F7E681", "stroke-width": 2.6, "stroke-linecap": "round", "stroke-linejoin": "round" })
    );
    return s;
  };

  // ---------------------------------------------------------------- API
  class ApiError extends Error {
    constructor(status, detail) {
      super(typeof detail === "string" ? detail : (detail && (detail.message || detail.detail)) || `Request failed (${status})`);
      this.status = status; this.detail = detail;
    }
  }
  LG.ApiError = ApiError;

  LG.api = async function (path, { method = "GET", json, form, raw = false, signal } = {}) {
    const headers = { "X-Requested-With": "LexiGuard" };
    let body;
    if (json !== undefined) { headers["Content-Type"] = "application/json"; body = JSON.stringify(json); }
    if (form) body = form;
    let res;
    try {
      res = await fetch(API + path, { method, headers, body, credentials: "same-origin", signal });
    } catch (e) {
      if (e.name === "AbortError") throw e;
      throw new ApiError(0, "Can't reach the LexiGuard server. Check that it's running.");
    }
    if (raw) {
      if (!res.ok) {
        let d; try { d = await res.json(); } catch (_) {}
        throw new ApiError(res.status, d && (d.fallback_text ? d : d.detail));
      }
      return res;
    }
    let data = null;
    try { data = await res.json(); } catch (_) {}
    if (!res.ok) {
      if (res.status === 401 && LG._guarded) LG.toLogin();
      throw new ApiError(res.status, data && data.detail);
    }
    return data;
  };

  // ---------------------------------------------------------------- auth
  let mePromise = null;
  LG.me = function (fresh) {
    if (!mePromise || fresh) mePromise = LG.api("/api/auth/me").catch(() => null);
    return mePromise;
  };
  LG.toLogin = function () {
    const next = encodeURIComponent(location.pathname.replace(/^\//, "") + location.search + location.hash);
    location.replace(`login.html?next=${next}`);
  };
  LG.requireUser = async function () {
    LG._guarded = true;
    const u = await LG.me();
    if (!u) { LG.toLogin(); return new Promise(() => {}); }
    return u;
  };
  LG.safeNext = function (fallback = "dashboard.html") {
    const n = new URLSearchParams(location.search).get("next") || "";
    return /^[a-z0-9_-]+\.html([?#].*)?$/i.test(n) ? n : fallback;
  };
  LG.logout = async function () {
    try { await LG.api("/api/auth/logout", { method: "POST" }); } catch (_) {}
    location.href = "index.html";
  };

  // ------------------------------------------------------------- top bar
  LG.topbar = function (user, active) {
    const host = document.getElementById("topbar");
    if (!host) return;
    const links = user
      ? [["dashboard.html", "Your documents", "documents"], ["analyze.html", "Check a document", "analyze"], ["draft.html", "Draft a document", "draft"]]
      : [["index.html#how", "How it works", "how"], ["index.html#checks", "What it checks", "checks"], ["index.html#privacy", "Privacy", "privacy"]];
    const nav = el("nav", { class: "nav", id: "nav", "aria-label": "Main" },
      links.map(([href, label, key]) => el("a", { href, "aria-current": key === active ? "page" : null }, label)));
    let end;
    if (user) {
      const initials = (user.name || "?").split(/\s+/).map((w) => w[0]).slice(0, 2).join("").toUpperCase();
      const menu = el("div", { class: "menu", id: "user-menu", role: "menu" },
        el("div", { class: "menu-head" }, el("strong", {}, user.name), el("span", { class: "tiny faint" }, user.email)),
        el("a", { href: "dashboard.html", role: "menuitem" }, "Your documents"),
        el("a", { href: "analyze.html", role: "menuitem" }, "Check a document"),
        el("a", { href: "draft.html", role: "menuitem" }, "Draft a document"),
        el("button", { type: "button", role: "menuitem", onclick: LG.logout }, "Sign out"));
      const btn = el("button", { class: "avatar", type: "button", "aria-haspopup": "menu", "aria-expanded": "false", "aria-controls": "user-menu", title: user.name },
        initials);
      btn.addEventListener("click", (e) => {
        e.stopPropagation();
        const open = menu.classList.toggle("is-open");
        btn.setAttribute("aria-expanded", String(open));
      });
      document.addEventListener("click", () => { menu.classList.remove("is-open"); btn.setAttribute("aria-expanded", "false"); });
      end = el("div", { class: "topbar-end" },
        active !== "analyze" && active !== "draft" ? el("a", { class: "btn btn-primary btn-sm hide-sm", href: "analyze.html" }, "Check a document") : null,
        el("div", { class: "user-menu" }, btn, menu));
    } else {
      end = el("div", { class: "topbar-end" },
        el("a", { class: "btn btn-quiet btn-sm", href: "login.html" }, "Sign in"),
        el("a", { class: "btn btn-primary btn-sm hide-sm", href: "signup.html" }, "Create account"));
    }
    const toggle = el("button", { class: "btn btn-quiet btn-icon nav-toggle", type: "button", "aria-label": "Menu", "aria-controls": "nav", "aria-expanded": "false",
      onclick: (e) => { const open = nav.classList.toggle("is-open"); e.currentTarget.setAttribute("aria-expanded", String(open)); } }, LG.icon("menu"));
    host.className = "topbar";
    host.replaceChildren(el("div", { class: "wrap topbar-in" },
      el("a", { class: "brand", href: user ? "dashboard.html" : "index.html" }, LG.seal(32), "LexiGuard"),
      nav, end, toggle));
    const onScroll = () => host.classList.toggle("is-scrolled", window.scrollY > 4);
    window.addEventListener("scroll", onScroll, { passive: true }); onScroll();
  };

  // ---------------------------------------------------------- bits & pieces
  LG.LEVELS = ["High", "Medium", "Low"];
  LG.RANK = { High: 3, Medium: 2, Low: 1 };
  LG.lvl = (l) => (LG.RANK[l] ? l : "Medium");

  LG.stamp = function (level, { size = "", sub = "", landing = false } = {}) {
    const L = level === "Void" ? "Void" : LG.lvl(level);
    const main = L === "Void" ? "Not a legal document" : `${L} risk`;
    return el("span", { class: `stamp ${L} ${size ? "stamp-" + size : ""} ${landing ? "is-landing" : ""}`, role: "img", "aria-label": main + (sub ? `, ${sub}` : "") },
      el("span", { class: "stamp-main" }, main), sub ? el("span", { class: "stamp-sub" }, sub) : null);
  };

  LG.fmtDate = function (iso, withTime) {
    if (!iso) return "";
    const d = new Date(iso);
    const opts = withTime ? { day: "numeric", month: "short", year: "numeric", hour: "numeric", minute: "2-digit" } : { day: "numeric", month: "short", year: "numeric" };
    return d.toLocaleString("en-IN", opts);
  };
  LG.cap = (s) => (s ? s[0].toUpperCase() + s.slice(1) : "");

  LG.toast = function (msg, { error = false, action, onAction, ms = 4200 } = {}) {
    let host = document.querySelector(".toasts");
    if (!host) { host = el("div", { class: "toasts", role: "status", "aria-live": "polite" }); document.body.append(host); }
    const t = el("div", { class: `toast ${error ? "is-error" : ""}` }, el("span", {}, msg),
      action ? el("button", { type: "button", onclick: () => { onAction && onAction(); t.remove(); } }, action) : null);
    host.append(t);
    setTimeout(() => t.remove(), ms);
  };

  LG.confirm = function (title, message, okLabel = "Delete") {
    return new Promise((resolve) => {
      const d = el("dialog", { class: "dialog" },
        el("div", { class: "dialog-body" }, el("h2", { class: "h3" }, title), el("p", { class: "muted small" }, message)),
        el("div", { class: "dialog-actions" },
          el("button", { class: "btn", type: "button", onclick: () => { d.close(); resolve(false); } }, "Cancel"),
          el("button", { class: "btn btn-primary", type: "button", style: "background:var(--high);border-color:var(--high)", onclick: () => { d.close(); resolve(true); } }, okLabel)));
      d.addEventListener("close", () => setTimeout(() => d.remove(), 50));
      d.addEventListener("cancel", () => resolve(false));
      document.body.append(d); d.showModal();
    });
  };

  // ----------------------------------------------------------- voice
  // One player for the whole page: starting a clip stops the previous one.
  const voice = (LG.voice = { audio: null, button: null, lang: "en" });
  try { voice.lang = localStorage.getItem("lg_voice_lang") === "hi" ? "hi" : "en"; } catch (_) {}
  voice.setLang = (l) => { voice.lang = l === "hi" ? "hi" : "en"; try { localStorage.setItem("lg_voice_lang", voice.lang); } catch (_) {} };

  function setBtn(btn, playing, loading) {
    if (!btn) return;
    btn.classList.toggle("is-busy", !!loading);
    btn.setAttribute("aria-pressed", String(!!playing));
    const label = btn.querySelector(".v-label");
    btn.replaceChildren(LG.icon(playing ? "stop" : "play"));
    if (label) btn.append(el("span", { class: "v-label" }, playing ? "Stop" : btn.dataset.label || "Listen"));
  }
  voice.stop = function () {
    if (voice.audio) { voice.audio.pause(); voice.audio = null; }
    if (window.speechSynthesis) window.speechSynthesis.cancel();
    setBtn(voice.button, false, false); voice.button = null;
  };
  function browserSpeak(text, lang, btn) {
    if (!("speechSynthesis" in window)) { LG.toast("This browser can't read text aloud.", { error: true }); setBtn(btn, false); return; }
    if (lang === "hi" && !speechSynthesis.getVoices().some((x) => (x.lang || "").toLowerCase().startsWith("hi"))) {
      setBtn(btn, false); voice.button = null;
      LG.toast("The online Hindi voice isn't reachable, and this computer has no Hindi voice installed. On Windows: Settings > Time & language > Speech > Add voices > Hindi. Or run check_setup.py to fix the online voice.", { error: true, ms: 9000 });
      return;
    }
    const u = new SpeechSynthesisUtterance(text);
    u.lang = lang === "hi" ? "hi-IN" : "en-IN";
    const v = speechSynthesis.getVoices().find((x) => x.lang && x.lang.toLowerCase().startsWith(lang === "hi" ? "hi" : "en-in"));
    if (v) u.voice = v;
    u.rate = 0.98;
    u.onend = u.onerror = () => { if (voice.button === btn) { setBtn(btn, false); voice.button = null; } };
    speechSynthesis.speak(u);
    setBtn(btn, true, false);
  }
  voice.play = async function (text, btn, lang = voice.lang) {
    if (voice.button === btn && btn) { voice.stop(); return; }
    voice.stop();
    voice.button = btn; setBtn(btn, false, true);
    try {
      const res = await LG.api("/api/tts", { method: "POST", json: { text, lang }, raw: true });
      const url = URL.createObjectURL(await res.blob());
      if (voice.button !== btn) return;
      const a = new Audio(url);
      voice.audio = a;
      a.onended = () => { if (voice.button === btn) { setBtn(btn, false); voice.button = null; } URL.revokeObjectURL(url); };
      await a.play();
      setBtn(btn, true, false);
    } catch (err) {
      if (voice.button !== btn) return;
      const d = err.detail && typeof err.detail === "object" ? err.detail : null;
      if (err.status === 503 && d && d.fallback_text) {
        browserSpeak(d.fallback_text, d.lang || lang, btn);
        if (d.detail && /English instead/.test(d.detail)) LG.toast(d.detail, { ms: 6000 });
      } else if (err.status === 503) {
        browserSpeak(text, lang, btn);
      } else {
        setBtn(btn, false, false); voice.button = null;
        LG.toast(err.message || "Couldn't play audio.", { error: true });
      }
    }
  };
  LG.voiceButton = function (getText, { label = "", small = true } = {}) {
    const b = el("button", { class: `btn ${small ? "btn-sm" : ""} btn-quiet v-btn`, type: "button", "aria-pressed": "false", "data-label": label || "Listen",
      title: "Read aloud", "aria-label": label || "Read aloud" }, LG.icon("play"), label ? el("span", { class: "v-label" }, label) : null);
    b.addEventListener("click", (e) => { e.stopPropagation(); voice.play(typeof getText === "function" ? getText() : getText, b); });
    return b;
  };

  // ------------------------------------------------- chat text renderer
  // A small, safe subset of Markdown: paragraphs, - / 1. lists, **bold**,
  // `code`, [#n] flag references, and "Suggested wording:" blocks.
  function inline(text, onFlag) {
    const out = [];
    const re = /(\*\*[^*]+\*\*|\[#\d{1,2}\]|`[^`]+`)/g;
    let last = 0, m;
    while ((m = re.exec(text))) {
      if (m.index > last) out.push(text.slice(last, m.index));
      const t = m[0];
      if (t.startsWith("**")) out.push(el("strong", {}, t.slice(2, -2)));
      else if (t.startsWith("`")) out.push(el("code", {}, t.slice(1, -1)));
      else {
        const n = +t.slice(2, -1);
        out.push(el("button", { class: "ref", type: "button", title: `Show flag #${n} in the document`, onclick: () => onFlag && onFlag(n) }, `#${n}`));
      }
      last = re.lastIndex;
    }
    if (last < text.length) out.push(text.slice(last));
    return out;
  }
  LG.renderRichText = function (text, onFlag) {
    const frag = document.createDocumentFragment();
    const lines = String(text || "").replace(/\r/g, "").split("\n");
    let list = null, listType = null, suggest = null;
    const flushList = () => { if (list) { frag.append(list); list = null; listType = null; } };
    for (let raw of lines) {
      const line = raw.trimEnd();
      if (suggest && line.trim() && !/^\s*[-*]\s|^\s*\d+[.)]\s|^#+\s/.test(line) && !/^\*\*/.test(line.trim())) {
        suggest.querySelector(".redline-text").append(document.createTextNode((suggest.dataset.has ? " " : "") + line.trim().replace(/^["“]|["”]$/g, "")));
        suggest.dataset.has = "1";
        continue;
      }
      if (!line.trim()) { flushList(); suggest = null; continue; }
      const sw = line.match(/^\s*(?:\*\*)?suggested wording:?(?:\*\*)?:?\s*(.*)$/i);
      if (sw) {
        flushList();
        suggest = el("figure", { class: "redline" }, el("figcaption", {}, "Suggested wording"), el("p", { class: "redline-text" }));
        const rest = sw[1].replace(/^["“]|["”]$/g, "").trim();
        if (rest) { suggest.querySelector(".redline-text").append(rest); suggest.dataset.has = "1"; }
        frag.append(suggest);
        continue;
      }
      const h = line.match(/^#{1,4}\s+(.*)$/);
      if (h) { flushList(); frag.append(el("p", { class: "rt-h" }, inline(h[1], onFlag))); continue; }
      const ul = line.match(/^\s*[-*•]\s+(.*)$/);
      const ol = line.match(/^\s*\d+[.)]\s+(.*)$/);
      if (ul || ol) {
        const type = ul ? "ul" : "ol";
        if (!list || listType !== type) { flushList(); list = el(type); listType = type; }
        list.append(el("li", {}, inline((ul || ol)[1], onFlag)));
        continue;
      }
      flushList();
      frag.append(el("p", {}, inline(line, onFlag)));
    }
    flushList();
    return frag;
  };

  // ------------------------------------------------------- display language
  // One setting for the whole app: "en" or "hi". Hindi shows translated
  // explanations, reads aloud in Hindi and asks the chat to answer in Hindi.
  LG.lang = () => voice.lang;
  LG.setLang = (l) => { voice.setLang(l); document.dispatchEvent(new CustomEvent("lg:lang", { detail: voice.lang })); };

  // ------------------------------------------------------- streaming fetch
  // POSTs JSON and calls onText(fullTextSoFar) as the answer arrives.
  LG.streamPost = async function (path, json, onText, signal) {
    const res = await LG.api(path, { method: "POST", json, raw: true, signal });
    const reader = res.body.getReader(), dec = new TextDecoder();
    let full = "", queued = false;
    const flush = () => { queued = false; onText(full, false); };
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      full += dec.decode(value, { stream: true });
      if (!queued) { queued = true; requestAnimationFrame(flush); }
    }
    full += dec.decode();
    onText(full, true);
    return { text: full, headers: res.headers };
  };

  // ------------------------------------------------------- voice typing
  // Records with the microphone and sends the audio to the server
  // (Whisper via Groq). If that isn't possible, uses the browser's own
  // speech recognition (Chrome / Edge). States: idle -> recording -> working.
  LG.recorder = function ({ onText, onState, language = () => "auto", maxSeconds = 600 }) {
    let rec = null, chunks = [], stream = null, timer = null, started = 0, mode = null, sr = null;
    const state = (s, extra) => onState && onState(s, extra || {});
    const pickType = () => ["audio/webm;codecs=opus", "audio/webm", "audio/ogg;codecs=opus", "audio/mp4"].find((t) => window.MediaRecorder && MediaRecorder.isTypeSupported && MediaRecorder.isTypeSupported(t)) || "";
    function cleanup() { clearInterval(timer); if (stream) stream.getTracks().forEach((t) => t.stop()); stream = null; }

    async function viaServer(blob) {
      state("working");
      const fd = new FormData();
      const ext = (blob.type.includes("mp4") ? "m4a" : blob.type.includes("ogg") ? "ogg" : "webm");
      fd.append("file", blob, `speech.${ext}`);
      fd.append("language", language());
      try {
        const r = await LG.api("/api/transcribe", { method: "POST", form: fd });
        if (r.text) onText(r.text); else LG.toast("No speech was picked up. Try again, a little closer to the microphone.");
        state("idle");
      } catch (err) {
        state("idle");
        LG.toast(err.message + (browserSR() ? " You can also try again: the browser's own speech typing will be used." : ""), { error: true, ms: 7000 });
        if (err.status === 503) useBrowser = true;
      }
    }
    let useBrowser = false;
    const browserSR = () => window.SpeechRecognition || window.webkitSpeechRecognition;

    function startBrowser() {
      const SR = browserSR();
      if (!SR) { LG.toast("Voice typing isn't available in this browser. Use Chrome or Edge, or type instead.", { error: true }); return; }
      sr = new SR();
      const l = language();
      sr.lang = l === "hi" ? "hi-IN" : "en-IN";
      sr.continuous = true; sr.interimResults = true;
      let finalText = "";
      sr.onresult = (e) => {
        let interim = "";
        for (let i = e.resultIndex; i < e.results.length; i++) {
          if (e.results[i].isFinal) finalText += e.results[i][0].transcript + " ";
          else interim += e.results[i][0].transcript;
        }
        state("recording", { seconds: Math.round((Date.now() - started) / 1000), interim: finalText + interim });
      };
      sr.onerror = (e) => { if (e.error !== "aborted") LG.toast(e.error === "not-allowed" ? "Allow microphone access in the browser to use voice typing." : "Voice typing stopped: " + e.error, { error: true }); };
      sr.onend = () => { clearInterval(timer); if (finalText.trim()) onText(finalText.trim()); state("idle"); mode = null; };
      mode = "browser"; started = Date.now();
      timer = setInterval(() => state("recording", { seconds: Math.round((Date.now() - started) / 1000) }), 500);
      sr.start(); state("recording", { seconds: 0 });
    }

    return {
      get active() { return !!mode; },
      async start() {
        if (mode) return;
        if (useBrowser || !window.MediaRecorder || !navigator.mediaDevices) { startBrowser(); return; }
        try { stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true } }); }
        catch (err) {
          LG.toast(err.name === "NotAllowedError" ? "Allow microphone access in the browser (the icon in the address bar) to speak." : "No microphone was found.", { error: true, ms: 6000 });
          return;
        }
        const type = pickType();
        rec = new MediaRecorder(stream, type ? { mimeType: type } : undefined);
        chunks = [];
        rec.ondataavailable = (e) => { if (e.data && e.data.size) chunks.push(e.data); };
        rec.onstop = () => {
          cleanup(); mode = null;
          const blob = new Blob(chunks, { type: rec.mimeType || "audio/webm" });
          if (blob.size < 1500) { state("idle"); LG.toast("That recording was too short. Hold on a moment longer before stopping."); return; }
          viaServer(blob);
        };
        // live level meter
        let level = () => 0;
        try {
          const ctx = new (window.AudioContext || window.webkitAudioContext)();
          const an = ctx.createAnalyser(); an.fftSize = 512;
          ctx.createMediaStreamSource(stream).connect(an);
          const data = new Uint8Array(an.fftSize);
          level = () => { an.getByteTimeDomainData(data); let m = 0; for (const v of data) m = Math.max(m, Math.abs(v - 128)); return Math.min(1, m / 60); };
          rec.addEventListener("stop", () => ctx.close());
        } catch (_) {}
        mode = "server"; started = Date.now();
        timer = setInterval(() => {
          const sec = Math.round((Date.now() - started) / 1000);
          state("recording", { seconds: sec, level: level() });
          if (sec >= maxSeconds) this.stop();
        }, 120);
        rec.start(1000);
        state("recording", { seconds: 0, level: 0 });
      },
      stop() {
        if (mode === "server" && rec && rec.state !== "inactive") rec.stop();
        else if (mode === "browser" && sr) sr.stop();
      },
      cancel() { if (rec && rec.state !== "inactive") { rec.onstop = () => { cleanup(); mode = null; state("idle"); }; rec.stop(); } if (sr) { sr.onend = () => { mode = null; state("idle"); }; sr.abort(); } },
    };
  };
  LG.fmtSec = (s) => `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;

  // Highlighting: split text into runs; overlapping flags share a run
  LG.segments = function (text, flags) {
    const pts = new Set([0, text.length]);
    flags.forEach((f) => { if (Number.isInteger(f.start) && Number.isInteger(f.end)) { pts.add(f.start); pts.add(f.end); } });
    const sorted = [...pts].filter((p) => p >= 0 && p <= text.length).sort((a, b) => a - b);
    const runs = [];
    for (let i = 0; i < sorted.length - 1; i++) {
      const a = sorted[i], b = sorted[i + 1];
      if (a === b) continue;
      const cover = [];
      flags.forEach((f, idx) => { if (Number.isInteger(f.start) && f.start <= a && f.end >= b) cover.push(idx); });
      runs.push({ a, b, cover });
    }
    return runs;
  };
})();
