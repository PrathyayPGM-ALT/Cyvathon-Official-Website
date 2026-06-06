/* ============================================================
   CYVATHON — shared frontend helpers
============================================================ */

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

/* Returns the logged-in user, or null. */
async function currentUser() {
  try { return (await api("/me")).user; }
  catch { return null; }
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
    { label: "Money",      items: [["/bank","Bank"], ["/exchange","Stock Exchange"], ["/loans","Loans"]] },
    { label: "Business",   items: [["/company","Companies"], ["/marketplace","Marketplace"]] },
    { label: "Government",  items: [["/government","Cabinet"], ["/ministries","Ministries"], ["/legislature","Legislature"], ["/gazette","Gazette"], ["/court","Courts"], ["/voting","Elections"], ["/treasury","Treasury"], ["/admin","Admin Panel"]] },
    { label: "Community",   items: [["/citizens","Citizens"], ["/chat","Chat"], ["/news","News"], ["/rules","Rules"]] },
    { label: "ID Card", href: "/profile" },
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
       <span id="navUser"><i class="fas fa-user-shield"></i> ${user.username}</span>
       <a href="#" onclick="doLogout();return false;">Logout</a>`;
  }

  const html = `
    <header><nav>
      <a href="/" class="logo" style="text-decoration:none;"><i class="fas fa-code logo-icon"></i><h1>CYVATHON</h1></a>
      <div class="nav-links">${navLinks}</div>
    </nav></header>`;
  const host = document.getElementById("nav");
  if (host) host.outerHTML = html;
  if (user) refreshNotifBadge();
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

/* A clickable link to a citizen's public profile. */
function userLink(name) {
  if (!name) return "—";
  const safe = (name + "").replace(/</g, "&lt;");
  return `<a class="link" href="/profile?user=${encodeURIComponent(name)}">${safe}</a>`;
}
