/* ============================================================
   CYVATHON — THE APP SHELL
   What makes Cyvathon feel like an app on a phone:
   - a bottom tab bar: Home, Bank, Chat, Alerts and More
   - "More" opens a sheet holding every page, grouped and coloured
   - a compact header, instead of the menu stacked down the screen
   - the offer to install Cyvathon on the home screen
   Screens wider than a phone keep the normal navigation.
============================================================ */
(function () {
  "use strict";
  if (window.CyvShell) return;

  const PHONE = "(max-width: 768px)";
  const DISMISS_KEY = "cyv-install-dismissed";
  const DISMISS_DAYS = 21;
  const $ = (s, r) => (r || document).querySelector(s);
  const esc = s => String(s ?? "").replace(/[&<>"']/g, m => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[m]));
  const user = () => window.CYV_SHELL_USER || null;
  const phone = () => matchMedia(PHONE).matches;
  const standalone = () => matchMedia("(display-mode: standalone)").matches || navigator.standalone === true;
  const isiOS = /iphone|ipad|ipod/i.test(navigator.userAgent)
    || (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);
  const here = (location.pathname.replace(/\/+$/, "") || "/");

  const TABS = [
    { href: "/", icon: "fa-house", label: "Home" },
    { href: "/bank", icon: "fa-building-columns", label: "Bank" },
    { href: "/chat", icon: "fa-comments", label: "Chat" },
    { href: "/notifications", icon: "fa-bell", label: "Alerts", badge: true },
  ];
  // The same colours the dashboard gives each part of the Republic.
  const GROUP = {
    "Money":      { color: "#58c4ff", icon: "fa-coins" },
    "Business":   { color: "#1fd6a6", icon: "fa-briefcase" },
    "Government": { color: "#ffce56", icon: "fa-landmark" },
    "Community":  { color: "#a78bfa", icon: "fa-users" },
    "ID Card":    { color: "#22d3ee", icon: "fa-id-card" },
    "More":       { color: "#ff8fb0", icon: "fa-star" },
  };
  const ICON = {
    "/bank": "fa-building-columns", "/card": "fa-credit-card", "/pay": "fa-qrcode",
    "/cyvapay": "fa-money-bill-transfer", "/loans": "fa-hand-holding-dollar",
    "/company": "fa-briefcase", "/exchange": "fa-chart-line", "/jobs": "fa-clipboard-list",
    "/states": "fa-map-location-dot", "/marketplace": "fa-store", "/cyvazon": "fa-truck-fast",
    "/shield": "fa-shield-halved", "/government": "fa-landmark", "/ministries": "fa-landmark-dome",
    "/cabinet": "fa-user-tie", "/legislature": "fa-scale-balanced", "/gazette": "fa-stamp",
    "/constitution": "fa-scroll", "/timeline": "fa-timeline", "/pens": "fa-pen", "/court": "fa-gavel",
    "/fir": "fa-file-shield", "/voting": "fa-check-to-slot", "/foreign": "fa-earth-americas",
    "/treasury": "fa-vault", "/admin": "fa-sliders", "/search": "fa-magnifying-glass",
    "/citizens": "fa-users", "/chat": "fa-comments", "/videos": "fa-video", "/blogs": "fa-feather-pointed",
    "/flightsim": "fa-plane-up", "/casino": "fa-dice", "/packet": "fa-futbol",
    "/cyvalend": "fa-hand-holding-heart", "/sites": "fa-globe", "/ai": "fa-robot",
    "/invite": "fa-user-plus", "/news": "fa-newspaper", "/rules": "fa-book",
    "/profile": "fa-id-card", "/passport": "fa-passport", "/portfolio": "fa-chart-pie",
    "/leaderboard": "fa-ranking-star", "/wrapped": "fa-gift", "/athena": "fa-eye",
    "/registry": "fa-folder-open", "/warroom": "fa-chess-knight", "/mail": "fa-envelope",
  };

  const CSS = `
  .cyv-tabbar, .m-av, .m-music{ display:none; }
  .m-music{ width:36px !important; height:36px; flex:none; margin:0 .55rem 0 auto; padding:0; border:none; border-radius:50%;
    place-items:center; cursor:pointer; font-size:.95rem; color:#9db0c6; box-shadow:none;
    background:rgba(120,170,255,.1); transition:background .2s, color .2s, box-shadow .2s; }
  .m-music:hover{ transform:none; filter:none; }
  .m-music.on{ color:#fff; background:linear-gradient(135deg,#1769c9,#22d3ee); box-shadow:0 0 0 3px rgba(34,211,238,.25), 0 6px 16px rgba(34,211,238,.4); }
  .m-music.blocked{ color:#231500; background:linear-gradient(135deg,#c98a06,#ffce56); }
  .m-music:focus-visible{ outline:2px solid #22d3ee; outline-offset:2px; }
  html[data-theme="light"] .m-music{ color:#566a83; background:rgba(15,50,110,.08); }
  @media ${PHONE}{
    /* A safety net: nothing on any page may widen the screen, or the phone
       zooms out and the right end of the tab bar — More — slides off the edge.
       Mobile Chrome ignores overflow-x:hidden on the page root for this, so
       each top-level section clips its own sideways overflow instead. "clip",
       unlike "hidden", makes no scrolling box, so the sticky header still sticks. */
    html.has-tabbar body > :not(header):not(.cyv-tabbar):not(.cm):not(.m-sheet):not(.m-install){ overflow-x:clip; }
    header nav{ flex-direction:row !important; padding:0 1rem; }
    body.has-tabbar header{ padding:.55rem 0; }
    body.has-tabbar .nav-links{ display:none !important; }
    body.has-tabbar .logo h1{ font-size:1.2rem; }
    body.has-tabbar .logo-icon{ font-size:1.4rem; }
    body.has-tabbar{ padding-bottom:calc(66px + env(safe-area-inset-bottom)); }
    body.has-tabbar .m-av, body.has-tabbar .m-music{ display:grid; }
    /* On a phone the floating music button would sit on the chat box and on
       buttons; the music lives in the header instead. The player itself stays. */
    body.has-tabbar .cm-fab, body.has-tabbar .cm-hint{ display:none !important; }
    .cyv-tabbar{ display:flex; position:fixed; left:0; right:0; bottom:0; z-index:60;
      height:calc(66px + env(safe-area-inset-bottom)); padding:6px 6px env(safe-area-inset-bottom);
      background:rgba(9,15,30,.9); backdrop-filter:blur(18px) saturate(160%); -webkit-backdrop-filter:blur(18px) saturate(160%);
      border-top:1px solid rgba(140,180,255,.14); box-shadow:0 -10px 30px rgba(0,0,0,.28); }
    body.has-tabbar .cm{ bottom:calc(66px + env(safe-area-inset-bottom) + 12px) !important; }
    body.has-tabbar .toast-wrap, body.has-tabbar .toast2{ bottom:calc(66px + env(safe-area-inset-bottom) + 14px) !important; }
  }
  @media (max-width:480px){
    body.has-tabbar .cm-panel{ bottom:calc(66px + env(safe-area-inset-bottom) + 80px) !important; }
  }
  body.playing .cyv-tabbar, body.playing .m-install{ display:none !important; }
  .m-av{ width:36px; height:36px; flex:none; border-radius:50%; place-items:center; text-decoration:none;
    font-weight:800; color:#06223a; background:linear-gradient(135deg,#58c4ff,#22d3ee); background-size:cover;
    background-position:center; box-shadow:0 0 0 2px rgba(88,196,255,.35); }
  .cyv-tab{ flex:1; min-width:0; display:flex; flex-direction:column; align-items:center; justify-content:center; gap:3px;
    position:relative; width:auto; margin:0; padding:0; border:none; background:none; box-shadow:none; cursor:pointer;
    text-decoration:none; color:#9db0c6; font:700 .68rem/1 inherit; letter-spacing:.2px;
    -webkit-tap-highlight-color:transparent; }
  .cyv-tab:hover{ transform:none; box-shadow:none; filter:none; background:none; }
  .cyv-tab i{ display:grid; place-items:center; width:46px; height:30px; border-radius:15px; font-size:1.12rem;
    transition:background .2s, color .2s, transform .15s, box-shadow .2s; }
  .cyv-tab.on{ color:#eaf6ff; }
  .cyv-tab.on i{ color:#041b33; background:linear-gradient(135deg,#58c4ff,#22d3ee); box-shadow:0 6px 16px rgba(34,211,238,.4); }
  .cyv-tab:active i{ transform:scale(.88); }
  .cyv-tab:focus-visible{ outline:2px solid #22d3ee; outline-offset:-2px; border-radius:14px; }
  .cyv-badge{ position:absolute; top:1px; left:calc(50% + 7px); min-width:18px; height:18px; padding:0 5px;
    border-radius:9px; font-style:normal; font-size:.62rem; font-weight:800; line-height:18px; text-align:center;
    color:#fff; background:#ff5d6c; box-shadow:0 2px 8px rgba(255,93,108,.55); }
  .cyv-badge[hidden]{ display:none; }
  html[data-theme="light"] .cyv-tabbar{ background:rgba(255,255,255,.92); border-top-color:rgba(15,50,110,.14);
    box-shadow:0 -8px 24px rgba(20,45,90,.1); }
  html[data-theme="light"] .cyv-tab{ color:#566a83; }
  html[data-theme="light"] .cyv-tab.on{ color:#10243f; }

  /* ---- the More sheet ---- */
  .m-sheet{ position:fixed; inset:0; z-index:950; display:flex; align-items:flex-end; justify-content:center;
    background:rgba(2,6,16,.55); backdrop-filter:blur(3px); animation:mFade .2s ease both; }
  .m-sheet[hidden]{ display:none; }
  .m-card{ width:100%; max-width:560px; max-height:88vh; overflow:auto; overscroll-behavior:contain;
    padding:.5rem 1rem calc(1.2rem + env(safe-area-inset-bottom)); border-radius:26px 26px 0 0;
    background:var(--surface,#111a2e); color:var(--text,#e8eef6); box-shadow:0 -20px 50px rgba(0,0,0,.45);
    animation:mUp .3s cubic-bezier(.2,.9,.3,1.08) both; }
  @keyframes mFade{ from{ opacity:0 } to{ opacity:1 } }
  @keyframes mUp{ from{ transform:translateY(40px); opacity:0 } to{ transform:none; opacity:1 } }
  .m-top{ display:flex; align-items:center; justify-content:space-between; margin:.2rem 0 .7rem; }
  .m-grab{ width:44px; height:5px; border-radius:3px; background:var(--panel-border-2,rgba(140,180,255,.24)); margin:.3rem auto .6rem; }
  .m-top b{ font-size:1.1rem; }
  .m-done{ width:auto !important; margin:0; padding:.45rem .9rem; border-radius:20px; border:none; cursor:pointer;
    font-weight:800; font-size:.85rem; color:var(--accent,#58c4ff); background:var(--panel-2,rgba(120,170,255,.09)); box-shadow:none; }
  .m-me{ display:flex; align-items:center; gap:.85rem; padding:.9rem 1rem; margin-bottom:1.1rem; border-radius:20px;
    text-decoration:none; color:#fff; background:linear-gradient(135deg,#1769c9,#22d3ee);
    box-shadow:0 12px 28px rgba(34,211,238,.28); }
  .m-me .av{ width:50px; height:50px; flex:none; border-radius:50%; display:grid; place-items:center;
    font-size:1.25rem; font-weight:900; color:#0b2a4a; background:#fff; background-size:cover; background-position:center; }
  .m-me b{ display:block; font-size:1.1rem; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
  .m-me small{ font-size:.8rem; opacity:.9; }
  .m-me > i{ margin-left:auto; opacity:.8; }
  .m-sec{ margin:0 0 1.15rem; }
  .m-sec h4{ display:flex; align-items:center; gap:.55rem; margin:0 0 .55rem .1rem; font-size:.74rem; font-weight:800;
    letter-spacing:.12em; text-transform:uppercase; color:var(--muted,#9db0c6); }
  .m-sec h4 i{ display:grid; place-items:center; width:28px; height:28px; border-radius:9px; font-size:.78rem;
    color:#041b33; background:var(--gc); box-shadow:0 4px 12px rgba(0,0,0,.2); }
  .m-grid{ display:grid; grid-template-columns:1fr 1fr; gap:.45rem; }
  .m-link{ display:flex; align-items:center; gap:.6rem; min-height:50px; padding:.65rem .75rem; border-radius:14px;
    text-decoration:none; color:var(--text,#e8eef6); font-weight:650; font-size:.88rem; line-height:1.2;
    background:var(--panel-2,rgba(120,170,255,.09)); border:1px solid var(--panel-border,rgba(140,180,255,.14));
    -webkit-tap-highlight-color:transparent; }
  .m-link i{ width:1.2em; text-align:center; color:var(--gc); font-size:1rem; flex:none; }
  .m-link:active{ transform:scale(.98); }
  .m-link.on{ border-color:var(--gc); box-shadow:inset 0 0 0 1px var(--gc); }
  .m-acts{ display:grid; gap:.45rem; margin-top:.3rem; }
  .m-act{ display:flex; align-items:center; gap:.7rem; width:100% !important; margin:0; min-height:50px; padding:.7rem .9rem;
    border-radius:14px; cursor:pointer; text-align:left; font-weight:700; font-size:.92rem; box-shadow:none;
    color:var(--text,#e8eef6); background:var(--panel-2,rgba(120,170,255,.09)); border:1px solid var(--panel-border,rgba(140,180,255,.14)); }
  .m-act i{ width:1.2em; text-align:center; color:var(--accent,#58c4ff); }
  .m-act.go{ color:#fff; border:none; background:linear-gradient(135deg,#1769c9,#22d3ee); }
  .m-act.go i{ color:#fff; }
  .m-act.out{ color:var(--red,#ff5d6c); } .m-act.out i{ color:var(--red,#ff5d6c); }
  .m-act:hover{ transform:none; filter:brightness(1.06); }
  .m-how{ margin:.2rem 0 0; padding:.75rem .9rem; border-radius:14px; font-size:.86rem; line-height:1.5;
    background:rgba(88,196,255,.1); border:1px solid rgba(88,196,255,.3); }
  .m-how[hidden]{ display:none; }

  /* ---- the install offer ---- */
  .m-install{ position:fixed; left:12px; right:12px; z-index:910; display:flex; align-items:center; gap:.8rem;
    bottom:calc(66px + env(safe-area-inset-bottom) + 12px); padding:.8rem .85rem; border-radius:20px; color:#fff;
    background:linear-gradient(135deg,#0b3d91,#1769c9 55%,#22d3ee); box-shadow:0 18px 40px rgba(0,0,0,.4);
    animation:mUp .35s cubic-bezier(.2,.9,.3,1.1) both; }
  .m-install img{ width:46px; height:46px; border-radius:12px; flex:none; }
  .m-install .t{ flex:1; min-width:0; }
  .m-install b{ display:block; font-size:.98rem; }
  .m-install span{ display:block; font-size:.8rem; line-height:1.4; opacity:.92; }
  .m-install .go{ width:auto !important; margin:0; padding:.6rem 1rem; border:none; border-radius:30px; cursor:pointer;
    font-weight:800; font-size:.85rem; color:#0b2a4a; background:#fff; box-shadow:none; white-space:nowrap; }
  .m-install .x{ width:32px !important; height:32px; margin:0; padding:0; border:none; border-radius:50%; cursor:pointer;
    color:#fff; background:rgba(255,255,255,.18); box-shadow:none; flex:none; }
  #navInstall{ color:var(--accent,#58c4ff); font-weight:700; }
  @media (prefers-reduced-motion: reduce){ .m-sheet, .m-card, .m-install{ animation:none !important; } }`;

  // ---------------- the tab bar and compact header ----------------
  function mountTabs() {
    const u = user();
    if (!u || $("#cyvTabbar")) return;
    document.body.classList.add("has-tabbar");
    document.documentElement.classList.add("has-tabbar");
    const bar = document.createElement("div");
    bar.id = "cyvTabbar";
    bar.className = "cyv-tabbar";
    bar.setAttribute("role", "navigation");
    bar.setAttribute("aria-label", "App");
    bar.innerHTML = TABS.map(t => {
      const on = here === t.href;
      return `<a class="cyv-tab ${on ? "on" : ""}" href="${t.href}"${on ? ' aria-current="page"' : ""}>
          <i class="fas ${t.icon}" aria-hidden="true"></i><span>${t.label}</span>
          ${t.badge ? '<em class="cyv-badge" id="tabBadge" hidden></em>' : ""}</a>`;
    }).join("") + `<button type="button" class="cyv-tab" id="tabMore" aria-haspopup="dialog" aria-expanded="false">
          <i class="fas fa-grip" aria-hidden="true"></i><span>More</span></button>`;
    document.body.appendChild(bar);
    $("#tabMore").addEventListener("click", openSheet);

    // Theme music gets a small button in the header on phones. It opens the
    // same player; it glows while music plays and turns gold if it needs a tap.
    const nav = $("header nav");
    if (nav && !$(".m-music", nav)) {
      const mb = document.createElement("button");
      mb.type = "button";
      mb.className = "m-music";
      mb.setAttribute("aria-label", "Theme music");
      mb.innerHTML = '<i class="fas fa-music" aria-hidden="true"></i>';
      mb.addEventListener("click", e => {
        e.stopPropagation();           // or the player's "click outside closes me" shuts it again at once
        const fab = document.getElementById("cmFab");
        if (fab) fab.click();
      });
      nav.appendChild(mb);
      const hook = () => {
        const m = document.getElementById("cyvMusic");
        if (!m) return false;
        const sync = () => {
          mb.classList.toggle("on", m.classList.contains("is-playing"));
          mb.classList.toggle("blocked", m.classList.contains("is-blocked"));
          mb.setAttribute("aria-label", m.classList.contains("is-playing") ? "Theme music — playing"
            : m.classList.contains("is-blocked") ? "Theme music — tap to resume" : "Theme music");
        };
        new MutationObserver(sync).observe(m, { attributes: true, attributeFilter: ["class"] });
        sync();
        return true;
      };
      if (!hook()) {                   // the player loads a moment after the nav
        const mo = new MutationObserver(() => { if (hook()) mo.disconnect(); });
        mo.observe(document.body, { childList: true });
      }
    }

    // The profile picture in the header goes to the ID card.
    if (nav && !$(".m-av", nav)) {
      const a = document.createElement("a");
      a.className = "m-av";
      a.href = "/profile";
      a.setAttribute("aria-label", "Your ID card");
      if (u.avatar && typeof avatarStyle === "function") {
        const st = avatarStyle(u.avatar).match(/style="([^"]*)"/);
        if (st) a.setAttribute("style", st[1].replace(/&quot;/g, '"').replace(/&#39;/g, "'").replace(/&amp;/g, "&"));
      } else {
        a.textContent = (u.username[0] || "?").toUpperCase();
      }
      nav.appendChild(a);
    }

    // Alerts carries the same unread count as the bell.
    const dot = document.getElementById("notifDot");
    const sync = () => {
      const b = $("#tabBadge");
      if (!b || !dot) return;
      const n = dot.textContent.trim();
      b.hidden = dot.style.display === "none" || !n;
      b.textContent = n;
    };
    if (dot) new MutationObserver(sync).observe(dot, { attributes: true, childList: true, characterData: true, subtree: true });
    sync();
  }

  // ---------------- the More sheet ----------------
  // Built from the nav itself each time it opens, so it always holds exactly
  // what the menu does — including anything revealed after the page loaded.
  function sections() {
    const out = [];
    document.querySelectorAll("header .nav-links .nav-group").forEach(g => {
      const label = ($(".nav-top", g) || {}).textContent || "";
      const links = [...g.querySelectorAll(".nav-menu a")].map(a => [a.getAttribute("href"), a.textContent.trim()]);
      if (label.trim() && links.length) out.push([label.trim(), links]);
    });
    const loose = [...document.querySelectorAll("header .nav-links > a")]
      .map(a => [a.getAttribute("href"), a.textContent.trim()])
      .filter(([h, t]) => h && h.startsWith("/") && h !== "/" && h !== "/notifications" && t && !TABS.some(x => x.href === h));
    if (loose.length) out.push(["More", loose]);
    return out;
  }

  function installRow() {
    if (standalone() || (!window.__cyvInstall && !isiOS)) return "";
    return `<button type="button" class="m-act go" data-act="install"><i class="fas fa-mobile-screen-button"></i> Install the Cyvathon app</button>
      <p class="m-how" id="mHow" hidden>On iPhone and iPad: tap <b>Share</b> <i class="fas fa-arrow-up-from-bracket"></i>
        at the bottom of Safari, then <b>Add to Home Screen</b>.</p>`;
  }

  function openSheet() {
    const u = user();
    if (!u) return;
    let sheet = $("#mSheet");
    if (!sheet) {
      sheet = document.createElement("div");
      sheet.id = "mSheet";
      sheet.className = "m-sheet";
      sheet.setAttribute("role", "dialog");
      sheet.setAttribute("aria-modal", "true");
      sheet.setAttribute("aria-label", "Menu");
      document.body.appendChild(sheet);
      sheet.addEventListener("click", e => {
        if (e.target === sheet) return closeSheet();
        const b = e.target.closest("[data-act]");
        if (!b) return;
        if (b.dataset.act === "close") closeSheet();
        else if (b.dataset.act === "theme") { if (typeof toggleTheme === "function") toggleTheme(); themeLabel(); }
        else if (b.dataset.act === "install") install($("#mHow"));
        else if (b.dataset.act === "logout" && typeof doLogout === "function") doLogout();
      });
    }
    let av = "";
    if (u.avatar && typeof avatarStyle === "function") av = avatarStyle(u.avatar);
    sheet.innerHTML = `<div class="m-card">
      <div class="m-grab" aria-hidden="true"></div>
      <div class="m-top"><b>Everything in Cyvathon</b><button type="button" class="m-done" data-act="close">Done</button></div>
      <a class="m-me" href="/profile"><span class="av" ${av}>${u.avatar ? "" : esc((u.username[0] || "?").toUpperCase())}</span>
        <span style="min-width:0"><b>${esc(u.username)}</b><small>Your ID card, balances and security</small></span>
        <i class="fas fa-chevron-right" aria-hidden="true"></i></a>
      ${sections().map(([label, links]) => {
        const g = GROUP[label] || GROUP.More;
        return `<section class="m-sec" style="--gc:${g.color}">
          <h4><i class="fas ${g.icon}" aria-hidden="true"></i> ${esc(label)}</h4>
          <div class="m-grid">${links.map(([h, t]) => `<a class="m-link ${h === here ? "on" : ""}" href="${esc(h)}">
            <i class="fas ${ICON[h] || "fa-circle-dot"}" aria-hidden="true"></i><span>${esc(t)}</span></a>`).join("")}</div>
        </section>`;
      }).join("")}
      <div class="m-acts">
        ${installRow()}
        <button type="button" class="m-act" data-act="theme" id="mTheme"></button>
        <button type="button" class="m-act out" data-act="logout"><i class="fas fa-right-from-bracket"></i> Log out</button>
      </div>
    </div>`;
    themeLabel();
    sheet.hidden = false;
    document.body.style.overflow = "hidden";
    $("#tabMore").setAttribute("aria-expanded", "true");
    const done = $(".m-done", sheet);
    if (done) done.focus();
  }

  function closeSheet() {
    const sheet = $("#mSheet");
    if (!sheet || sheet.hidden) return;
    sheet.hidden = true;
    document.body.style.overflow = "";
    const more = $("#tabMore");
    if (more) { more.setAttribute("aria-expanded", "false"); more.focus(); }
  }

  function themeLabel() {
    const b = $("#mTheme");
    if (!b) return;
    const light = document.documentElement.getAttribute("data-theme") === "light";
    b.innerHTML = `<i class="fas fa-${light ? "moon" : "sun"}"></i> ${light ? "Dark mode" : "Light mode"}`;
  }

  // ---------------- installing ----------------
  async function install(howEl) {
    const ev = window.__cyvInstall;
    if (ev) {
      ev.prompt();
      try { await ev.userChoice; } catch (e) {}
      window.__cyvInstall = null;
      hideBanner();
      const d = $("#navInstall"); if (d) d.remove();
      return;
    }
    if (howEl) howEl.hidden = false;           // iPhone: Safari has no button, only Share → Add to Home Screen
  }

  function dismissed() {
    try { return Date.now() - (+localStorage.getItem(DISMISS_KEY) || 0) < DISMISS_DAYS * 864e5; }
    catch (e) { return false; }
  }

  function offerBanner() {
    if (!user() || standalone() || !phone() || dismissed() || $("#mInstall")) return;
    if (!window.__cyvInstall && !isiOS) return;
    const ios = !window.__cyvInstall;
    const el = document.createElement("div");
    el.id = "mInstall";
    el.className = "m-install";
    el.setAttribute("role", "dialog");
    el.setAttribute("aria-label", "Install the Cyvathon app");
    el.innerHTML = `<img src="/static/icons/icon-192.png" alt="">
      <div class="t"><b>Get the Cyvathon app</b>
        <span>${ios ? 'Tap <b>Share</b> <i class="fas fa-arrow-up-from-bracket"></i>, then <b>Add to Home Screen</b>.'
                    : "On your home screen, full-screen, one tap away."}</span></div>
      ${ios ? "" : '<button type="button" class="go">Install</button>'}
      <button type="button" class="x" aria-label="Not now"><i class="fas fa-xmark"></i></button>`;
    document.body.appendChild(el);
    const go = $(".go", el);
    if (go) go.addEventListener("click", () => install());
    $(".x", el).addEventListener("click", () => {
      try { localStorage.setItem(DISMISS_KEY, String(Date.now())); } catch (e) {}
      hideBanner();
    });
  }

  function hideBanner() { const b = $("#mInstall"); if (b) b.remove(); }

  // A computer gets a quiet "Get the app" link in the menu instead.
  function offerDesktop() {
    if (phone() || standalone() || !window.__cyvInstall || $("#navInstall")) return;
    const links = $("header .nav-links"), theme = $("#themeToggle");
    if (!links) return;
    const a = document.createElement("a");
    a.href = "#";
    a.id = "navInstall";
    a.innerHTML = '<i class="fas fa-download"></i> Get the app';
    a.addEventListener("click", e => { e.preventDefault(); install(); });
    links.insertBefore(a, theme || null);
  }

  // Inside the installed app, the phone's status bar follows the theme.
  function statusBar() {
    if (!standalone()) return;
    let m = document.querySelector('meta[name="theme-color"]');
    if (!m) { m = document.createElement("meta"); m.name = "theme-color"; document.head.appendChild(m); }
    m.content = document.documentElement.getAttribute("data-theme") === "light" ? "#ffffff" : "#090f1e";
  }

  // ---------------- start ----------------
  function refresh() {
    mountTabs();
    offerDesktop();
    statusBar();
  }

  const style = document.createElement("style");
  style.id = "cyvShellCss";
  style.textContent = CSS;
  document.head.appendChild(style);
  document.documentElement.classList.toggle("is-app", standalone());
  refresh();
  document.addEventListener("cyv:installable", () => { offerDesktop(); setTimeout(offerBanner, 1500); });
  addEventListener("appinstalled", () => {
    window.__cyvInstall = null;
    hideBanner();
    const d = $("#navInstall"); if (d) d.remove();
  });
  document.addEventListener("keydown", e => { if (e.key === "Escape") closeSheet(); });
  new MutationObserver(statusBar).observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
  setTimeout(offerBanner, 3000);

  window.CyvShell = { refresh, openSheet, closeSheet, sections, install };
})();
