/* ============================================================
   CYVATHON DEBIT CARD — shared renderer
   Used by /card, /card/sheet and /bank so the card markup lives once.
   ============================================================ */

function cyEsc(s){
  return (s || "").replace(/&/g, "&amp;").replace(/</g, "&lt;")
    .replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}

function cySince(iso){
  if (!iso) return "--/--";
  const d = new Date(iso);
  return isNaN(d) ? "--/--"
    : d.toLocaleDateString(undefined, { month: "2-digit", year: "2-digit" });
}

/* Not a secret — just the last 3 digits, printed where a CVV would sit so the
   back of the card reads like a real one. Nothing authenticates against it. */
function cyTail(number){ return (number || "").slice(-3); }

function cyCardFront(c){
  return `<div class="cy-card front"><div class="inner">
    <div class="cy-top">
      <div>
        <div class="cy-brand">CYVA<span>THON</span></div>
        <div class="cy-type">National Debit</div>
      </div>
      <div class="cy-qr"><img src="/card/qr/${c.number}.svg"
           alt="Scan to pay ${cyEsc(c.holder)}" loading="lazy"></div>
    </div>
    <div class="cy-chip"></div>
    <div class="cy-num">${cyEsc(c.pretty)}</div>
    <div class="cy-bottom">
      <div class="cy-foot">
        <div>
          <div class="cy-lbl">Cardholder</div>
          <div class="cy-val">${cyEsc(c.holder)}</div>
        </div>
        <div style="text-align:right;">
          <div class="cy-lbl">Member since</div>
          <div class="cy-val">${cySince(c.since)}</div>
        </div>
      </div>
      <div class="cy-bars"><img src="/card/barcode/${c.number}.svg" alt="" loading="lazy"></div>
    </div>
  </div></div>`;
}

function cyCardBack(c){
  return `<div class="cy-card back"><div class="inner"><div class="cy-back-body">
    <div class="cy-mag"></div>
    <div class="cy-sign">
      <span class="sig">${cyEsc(c.holder)}</span>
      <span class="cvv">${cyTail(c.number)}</span>
    </div>
    <div class="cy-fine">
      Property of the <b>Republic of Cyvathon</b>. This card identifies its holder
      so other citizens may pay them — it cannot be used to withdraw funds.
      Honoured in <b>Aquilithia</b> at treaty par, 1 PB = 1 CB.
      Found it? Return it to the Treasury.
    </div>
  </div></div></div>`;
}

/* Static, non-interactive card — for the print sheet. */
function cyCardStatic(c){ return cyCardFront(c); }

/* Interactive card: tilts toward the pointer, click/tap to flip.
   Returns the stage element. */
function cyCardInteractive(container, c, opts){
  const o = opts || {};
  container.innerHTML =
    `<div class="cy-stage"><div class="cy-flip">
       ${cyCardFront(c)}${cyCardBack(c)}
       <div class="cy-glare"></div>
     </div></div>` +
    (o.hint === false ? "" :
      `<div class="cy-hint"><i class="fas fa-hand-pointer"></i> Tap the card to turn it over</div>`);

  const stage = container.querySelector(".cy-stage");
  const flip = container.querySelector(".cy-flip");
  const glare = container.querySelector(".cy-glare");
  const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  let flipped = false, raf = null;

  const MAX = 11;   // degrees

  function apply(px, py){
    // px/py are 0..1 across the card
    const ry = (px - 0.5) * 2 * MAX;
    const rx = (0.5 - py) * 2 * MAX;
    flip.style.transform =
      `rotateY(${flipped ? 180 + ry : ry}deg) rotateX(${rx}deg)`;
    glare.style.setProperty("--gx", (px * 100).toFixed(1) + "%");
    glare.style.setProperty("--gy", (py * 100).toFixed(1) + "%");
  }

  function fromEvent(e){
    const r = stage.getBoundingClientRect();
    const pt = e.touches && e.touches[0] ? e.touches[0] : e;
    const px = Math.min(1, Math.max(0, (pt.clientX - r.left) / r.width));
    const py = Math.min(1, Math.max(0, (pt.clientY - r.top) / r.height));
    if (raf) cancelAnimationFrame(raf);
    raf = requestAnimationFrame(() => apply(px, py));
  }

  function rest(){
    if (raf) cancelAnimationFrame(raf);
    flip.classList.remove("is-tilting");
    flip.style.transform = flipped ? "rotateY(180deg)" : "";
  }

  if (!reduce){
    stage.addEventListener("pointermove", (e) => {
      if (e.pointerType === "touch") return;      // touch drives it on touchmove
      flip.classList.add("is-tilting");
      fromEvent(e);
    });
    stage.addEventListener("pointerleave", rest);
    stage.addEventListener("touchmove", (e) => {
      flip.classList.add("is-tilting");
      fromEvent(e);
    }, { passive: true });
    stage.addEventListener("touchend", rest);
  }

  function toggle(){
    flipped = !flipped;
    flip.classList.toggle("is-flipped", flipped);
    rest();
  }
  flip.addEventListener("click", toggle);
  flip.setAttribute("tabindex", "0");
  flip.setAttribute("role", "button");
  flip.setAttribute("aria-label", "Cyvathon debit card — activate to turn over");
  flip.addEventListener("keydown", (e) => {
    if (e.key === "Enter" || e.key === " "){ e.preventDefault(); toggle(); }
  });

  return stage;
}
