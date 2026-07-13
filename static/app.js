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

async function api(path, options = {}) {
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
    { label: "Government",  items: [["/government","Cabinet"], ["/ministries","Ministries"], ["/legislature","Legislature"], ["/gazette","Gazette"], ["/court","Courts"], ["/fir","Report a Crime"], ["/voting","Elections"], ["/treasury","Treasury"], ["/admin","Admin Panel"]] },
    { label: "Community",   items: [["/citizens","Citizens"], ["/chat","Chat"], ["/ai","Cyvathon AI"], ["/invite","Invite Friends"], ["/news","News"], ["/rules","Rules"]] },
    { label: "ID Card", items: [["/profile","ID Card"], ["/passport","Passport & Citizenship"]] },
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
  if (user) { refreshNotifBadge(); revealAthena(); }
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
  return s;
}

/* A clickable link to a citizen's public profile. */
function userLink(name) {
  if (!name) return "—";
  const safe = (name + "").replace(/</g, "&lt;");
  return `<a class="link" href="/profile?user=${encodeURIComponent(name)}">${safe}</a>`;
}
