/* ============================================================
   CYVATHON — shared frontend helpers
============================================================ */

/* Apply the saved theme as early as possible to avoid a flash. */
(function () {
  try { if (localStorage.getItem("cyv-theme") === "light")
    document.documentElement.setAttribute("data-theme", "light"); } catch (e) {}
})();

function toggleTheme() {
  const light = document.documentElement.getAttribute("data-theme") === "light";
  if (light) document.documentElement.removeAttribute("data-theme");
  else document.documentElement.setAttribute("data-theme", "light");
  try { localStorage.setItem("cyv-theme", light ? "dark" : "light"); } catch (e) {}
  syncThemeBtn();
}
function syncThemeBtn() {
  const light = document.documentElement.getAttribute("data-theme") === "light";
  const btn = document.getElementById("themeToggle");
  if (btn) {
    btn.innerHTML = `<i class="fas fa-${light ? "moon" : "sun"}"></i>`;
    btn.title = light ? "Switch to dark mode" : "Switch to light mode";
  }
}

/* ---- Global first-load loader ----
   Every page fetches through api(). We show a fun full-page loader while the
   initial data load is in flight, then fade it out. Only for the FIRST load of
   a page (subsequent api() calls — polls, actions — never re-trigger it), and
   only if the load is slow enough to notice (no flash on fast loads). */
let _inflight = 0, _initDone = false, _gEl = null, _gShowT = null, _gHideT = null, _gMaxT = null;
function _gShow() {
  if (_initDone || _gEl) return;
  _gEl = document.createElement("div");
  _gEl.id = "cyGlobalLoad";
  _gEl.style.cssText = "position:fixed;inset:0;z-index:5000;display:flex;align-items:center;"
    + "justify-content:center;background:var(--bg,#0a0d14);opacity:0;transition:opacity .25s;";
  _gEl.innerHTML = spinner();
  (document.body || document.documentElement).appendChild(_gEl);
  requestAnimationFrame(() => { if (_gEl) _gEl.style.opacity = "1"; });
}
function _gHide() {
  if (_gEl) { const el = _gEl; _gEl = null; el.style.opacity = "0"; setTimeout(() => el.remove(), 320); }
}
function _gSettle() {          // the initial data load has finished
  _initDone = true;
  if (_gShowT) { clearTimeout(_gShowT); _gShowT = null; }
  if (_gMaxT) { clearTimeout(_gMaxT); _gMaxT = null; }
  _gHide();
}
function _apiStart() {
  _inflight++;
  if (_initDone) return;
  if (_gHideT) { clearTimeout(_gHideT); _gHideT = null; }   // a new request — don't settle yet
  // Only actually show if something is still loading when the delay elapses
  // (this is what stops a flash on fast loads).
  if (!_gShowT && !_gEl) _gShowT = setTimeout(() => { _gShowT = null; if (_inflight > 0 && !_initDone) _gShow(); }, 180);
  if (!_gMaxT) _gMaxT = setTimeout(_gSettle, 12000);        // safety: never stay stuck
}
function _apiEnd() {
  _inflight = Math.max(0, _inflight - 1);
  if (_initDone || _inflight > 0) return;
  if (_gHideT) clearTimeout(_gHideT);
  _gHideT = setTimeout(_gSettle, 300);   // idle for 300ms => initial load done (coalesces auth->data)
}

async function api(path, options = {}) {
  _apiStart();
  try {
    const res = await fetch(path, {
      credentials: "include",
      ...options,
      headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    });
    const text = await res.text();
    let data;
    try { data = text ? JSON.parse(text) : {}; }
    catch (e) {
      throw new Error("Server error (HTTP " + res.status + "). The database may not be set up yet — run schema.sql in Supabase.");
    }
    if (!res.ok || data.success === false) throw new Error(data.error || ("HTTP " + res.status));
    return data;
  } finally {
    _apiEnd();
  }
}

/* Cache /me for a few seconds so a single page-load doesn't fetch it
   multiple times (requireAuth + nav + page all share one call). */
let _mePromise = null, _meAt = 0;
function fetchMe() {
  if (_mePromise && (Date.now() - _meAt) < 5000) return _mePromise;
  _meAt = Date.now();
  _mePromise = api("/me").catch(() => null);
  return _mePromise;
}

/* Returns the logged-in user, or null. */
async function currentUser() {
  const m = await fetchMe();
  if (m && m.user) window.__me = m.user.username;   // used to highlight @mentions of you
  return m ? m.user : null;
}

/* Guard a page: redirect to landing if not logged in. Returns user. */
async function requireAuth() {
  const user = await currentUser();
  if (!user) { window.location.href = "/login"; return null; }
  return user;
}

/* Build the shared top navigation with hover dropdowns.
   Guests see ONLY the logo + Login. The full nav appears once logged in. */
function renderNav(active, user) {
  const GROUPS = [
    { label: "Home", href: "/" },
    { label: "Money",      items: [["/bank","Bank"], ["/exchange","Stock Exchange"], ["/loans","Loans"], ["/casino","Casino"]] },
    { label: "Business",   items: [["/company","Companies"], ["/jobs","Jobs Board"], ["/states","States"], ["/marketplace","Import & Export"]] },
    { label: "Government",  items: [["/government","Cabinet"], ["/ministries","Ministries"], ["/legislature","Legislature"], ["/gazette","Gazette"], ["/court","Courts"], ["/fir","Report a Crime"], ["/voting","Elections"], ["/foreign","Foreign Affairs"], ["/treasury","Treasury"], ["/admin","Admin Panel"]] },
    { label: "Community",   items: [["/citizens","Citizens"], ["/chat","Chat"], ["/videos","Videos"], ["/blogs","Blogs"], ["/ai","Cyvathon AI"], ["/invite","Invite Friends"], ["/news","News"], ["/rules","Rules"]] },
    { label: "ID Card", items: [["/profile","ID Card"], ["/passport","Passport & Citizenship"], ["/portfolio","Portfolio"], ["/leaderboard","Leaderboards"]] },
  ];

  let navLinks;
  if (!user) {
    navLinks = `<a href="/login" style="color:var(--accent)"><i class="fas fa-right-to-bracket"></i> Login</a>`;
  } else {
    navLinks = GROUPS.map(g => {
      if (g.href) {
        const act = g.href === active ? 'style="color:var(--accent)"' : "";
        return `<a href="${g.href}" ${act}>${g.label}</a>`;
      }
      const onActive = g.items.some(([h]) => h === active);
      const items = g.items.map(([h, t]) =>
        `<a href="${h}" ${h === active ? 'style="color:var(--accent)"' : ""}>${t}</a>`).join("");
      return `<div class="nav-group" onclick="this.classList.toggle('open')">
        <span class="nav-top" ${onActive ? 'style="color:var(--accent)"' : ""}>${g.label} <i class="fas fa-chevron-down"></i></span>
        <div class="nav-menu">${items}</div>
      </div>`;
    }).join("");
    navLinks += `<a href="/notifications" id="navBell" title="Notifications"><i class="fas fa-bell"></i><span id="notifDot" class="notif-dot" style="display:none"></span></a>
       <span id="navUser"><span class="nav-av" ${avatarStyle(user.avatar)}>${user.avatar ? "" : (user.username[0] || "?").toUpperCase()}</span>${user.username}</span>
       <a href="#" onclick="doLogout();return false;">Logout</a>`;
  }

  navLinks += `<a href="#" id="themeToggle" class="theme-toggle" onclick="toggleTheme();return false;" title="Toggle theme"><i class="fas fa-sun"></i></a>`;

  const html = `
    <header><nav>
      <a href="/" class="logo" style="text-decoration:none;"><i class="fas fa-code logo-icon"></i><h1>CYVATHON</h1></a>
      <div class="nav-links">${navLinks}</div>
    </nav></header>`;
  const host = document.getElementById("nav");
  if (host) host.outerHTML = html;
  syncThemeBtn();
  if (user) { refreshNotifBadge(); revealAthena(); startToasts(); }
}

/* Pop a toast when a new notification (DM, mention, approval, referral…) arrives. */
let _toastMax = null, _toastTimer = null;
function startToasts() {
  if (_toastTimer) return;
  pollToasts();
  _toastTimer = setInterval(pollToasts, 15000);
}
async function pollToasts() {
  try {
    const d = await api("/notifications/list");
    const rows = d.notifications || [];
    if (!rows.length) return;
    const maxId = Math.max(...rows.map(r => r.id));
    if (_toastMax === null) { _toastMax = maxId; return; }   // baseline — don't toast old ones
    const fresh = rows.filter(r => r.id > _toastMax && !r.read).sort((a, b) => a.id - b.id);
    fresh.forEach(showToast);
    _toastMax = Math.max(_toastMax, maxId);
    if (fresh.length) refreshNotifBadge();
  } catch {}
}
function showToast(n) {
  let wrap = document.getElementById("toastWrap");
  if (!wrap) { wrap = document.createElement("div"); wrap.id = "toastWrap"; wrap.className = "toast-wrap"; document.body.appendChild(wrap); }
  const t = document.createElement("div");
  t.className = "toast";
  t.innerHTML = `<i class="fas fa-bell"></i><div class="tmsg">${(n.message || "").replace(/</g, "&lt;")}</div><span class="tx">&times;</span>`;
  t.onclick = () => { if (n.link) location.href = n.link; };
  t.querySelector(".tx").onclick = (e) => { e.stopPropagation(); t.classList.remove("show"); setTimeout(() => t.remove(), 300); };
  wrap.appendChild(t);
  requestAnimationFrame(() => t.classList.add("show"));
  setTimeout(() => { t.classList.remove("show"); setTimeout(() => t.remove(), 300); }, 6500);
}

/* Reveal the classified Athena link only to agency members. */
async function revealAthena() {
  try {
    const me = await fetchMe();
    if (!me || !me.cia || document.getElementById("athenaNav")) return;
    const links = document.querySelector(".nav-links");
    if (!links) return;
    const a = document.createElement("a");
    a.id = "athenaNav"; a.href = "/athena"; a.title = "Athena (classified)";
    a.style.color = "var(--gold)"; a.innerHTML = "🦉 Athena";
    links.insertBefore(a, document.getElementById("navUser") || null);
  } catch {}
}

async function refreshNotifBadge() {
  try {
    const d = await api("/notifications/count");
    const dot = document.getElementById("notifDot");
    if (!dot) return;
    if (d.unread > 0) { dot.textContent = d.unread > 9 ? "9+" : d.unread; dot.style.display = "inline-block"; }
    else dot.style.display = "none";
  } catch {}
}

async function doLogout() {
  try { await api("/logout", { method: "POST", body: "{}" }); } catch {}
  window.location.href = "/";
}

const fmt = (n) => (Math.round((Number(n) || 0) * 100) / 100).toLocaleString();

/* Inline style that turns any avatar circle into a photo when a url is set. */
function avatarStyle(url) {
  return url ? `style="background-image:url('${(url + "").replace(/'/g, "%27")}');background-size:cover;background-position:center;"` : "";
}

/* Render chat text safely with **bold**, *italic*, __underline__, emojis,
   inline image/GIF URLs, and links. Escapes HTML first. */
function renderRich(text) {
  let s = (text || "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  const imgs = [];
  s = s.replace(/(https?:\/\/[^\s]+?\.(?:gif|png|jpe?g|webp))/gi, function (m) { imgs.push(m); return "@@IMG" + (imgs.length - 1) + "@@"; });
  const auds = [];
  s = s.replace(/(https?:\/\/[^\s]+?\.(?:webm|ogg|mp3|m4a|wav))/gi, function (m) { auds.push(m); return "@@AUD" + (auds.length - 1) + "@@"; });
  s = s.replace(/(https?:\/\/[^\s]+)/g, '<a href="$1" target="_blank" rel="noopener" class="link">$1</a>');
  s = s.replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>")
       .replace(/__([^_]+)__/g, "<u>$1</u>")
       .replace(/\*([^*]+)\*/g, "<i>$1</i>");
  // @mentions — highlight, and flag it specially when it's you
  s = s.replace(/(?<![\w@])@([A-Za-z0-9_.\-]{2,32})/g, function (m, name) {
    const me = window.__me && name.toLowerCase() === String(window.__me).toLowerCase();
    return '<span class="mention' + (me ? ' me' : '') + '">@' + name + '</span>';
  });
  s = s.replace(/@@IMG(\d+)@@/g, function (m, i) { return '<br><img src="' + imgs[+i] + '" class="chat-img" loading="lazy" decoding="async"><br>'; });
  s = s.replace(/@@AUD(\d+)@@/g, function (m, i) { return '<audio class="voice-msg" controls preload="metadata" src="' + auds[+i] + '"></audio>'; });
  return s;
}

/* A fun on-brand loading animation — bouncing brand dots + a witty message.
   Usage: box.innerHTML = spinner();  or  spinner("Sorting the mail…", "sm") */
const LOAD_MSGS = [
  "Minting Cybucks…", "Consulting the Chair…", "Waking the Treasury…",
  "Summoning citizens…", "Counting the Aquilines…", "Booting the micronation…",
  "Polishing the passports…", "Herding the bots…", "Bribing the servers…",
  "Rallying the Republic…", "Dusting off the ledger…", "Warming up the exchange…",
];
/* Pick a themed loader animation based on the page you're on. */
function _pageVariant() {
  const p = (typeof location !== "undefined" ? location.pathname : "/") || "/";
  if (p.indexOf("/casino") === 0) return "dice";
  if (p.indexOf("/exchange") === 0 || p.indexOf("/portfolio") === 0) return "bars";
  if (p.indexOf("/videos") === 0) return "play";
  if (p.indexOf("/blogs") === 0) return "pen";
  if (p.indexOf("/passport") === 0 || p.indexOf("/profile") === 0) return "stamp";
  if (p.indexOf("/chat") === 0 || p.indexOf("/mail") === 0 || p.indexOf("/citizens") === 0 || p.indexOf("/ai") === 0) return "bubbles";
  if (/^\/(government|voting|legislature|court|gazette|ministries|foreign|fir|treasury|admin|athena|warroom)/.test(p)) return "seal";
  return "coin";   // bank, loans, company, jobs, states, marketplace, leaderboard, invite, home…
}
function _animMarkup(v) {
  let inner;
  switch (v) {
    case "bars":    inner = '<div class="cy-bars"><span></span><span></span><span></span><span></span></div>'; break;
    case "dice":    inner = '<div class="cy-die"><span class="pip"></span></div>'; break;
    case "bubbles": inner = '<div class="cy-bubble"><span></span><span></span><span></span></div>'; break;
    case "seal":    inner = '<div class="cy-seal"><span class="tri">&#9650;</span></div>'; break;
    case "play":    inner = '<div class="cy-play"></div>'; break;
    case "pen":     inner = '<div class="cy-pen"><i class="fas fa-pen-nib"></i><span class="line"></span></div>'; break;
    case "stamp":   inner = '<div class="cy-stamp"><i class="fas fa-stamp"></i></div>'; break;
    default:        inner = '<div class="cy-coinwrap"><div class="cy-coin"><div class="f1"><i class="fas fa-code"></i></div><div class="f2">&#9650;</div></div></div>';
  }
  return '<div class="cy-anim">' + inner + '</div>';
}
function spinner(msg, size, variant) {
  const m = msg || LOAD_MSGS[Math.floor(Math.random() * LOAD_MSGS.length)];
  const cls = "cyload" + (size === "sm" ? " sm" : "");
  return `<div class="${cls}">${_animMarkup(variant || _pageVariant())}`
       + `<div class="cyload-msg">${(m + "").replace(/</g, "&lt;")}</div></div>`;
}

/* A clickable link to a citizen's public profile. */
function userLink(name) {
  if (!name) return "—";
  const safe = (name + "").replace(/</g, "&lt;");
  return `<a class="link" href="/profile?user=${encodeURIComponent(name)}">${safe}</a>`;
}
