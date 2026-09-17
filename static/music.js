/* ============================================================
   CYVATHON THEME MUSIC
   A floating player on every page that has the nav.

   Four soothing themes are composed live in the browser with the Web
   Audio API, so there is nothing to download and nothing to license.
   Citizens can also add their own songs. Those are kept in this browser
   (IndexedDB), filed under the citizen who added them, and never sent to
   Cyvathon.

   The site is separate pages, so sound can't literally carry through a
   click. Instead the player remembers what was playing and where, and
   picks it back up on the next page with a short fade. Browsers only let
   sound start after a tap or key press, so when a page isn't allowed to
   start it yet, the button turns gold and the first tap anywhere does.
============================================================ */
(function () {
  "use strict";
  if (window.CyvMusic) return;

  const ME = window.CYV_MUSIC_USER || "guest";
  const KEY = "cyv-music:" + ME;
  const DB_NAME = "cyvathon-music", STORE = "songs";
  const MAX_SONGS = 12, MAX_MB = 40;
  const AUDIO_EXT = /\.(mp3|m4a|aac|ogg|oga|opus|wav|flac|webm)$/i;
  const TAB = Math.random().toString(36).slice(2);

  const $ = id => document.getElementById(id);
  const esc = s => String(s ?? "").replace(/[&<>"']/g, m => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[m]));
  const mb = n => (n / 1048576).toFixed(n < 10485760 ? 1 : 0) + " MB";

  // ---------------- what the citizen chose ----------------
  const DEFAULTS = { on: false, track: "still-water", vol: 0.5, t: 0, savedAt: 0 };
  let S = (() => {
    try { return Object.assign({}, DEFAULTS, JSON.parse(localStorage.getItem(KEY) || "{}")); }
    catch (e) { return Object.assign({}, DEFAULTS); }
  })();
  const save = () => { try { localStorage.setItem(KEY, JSON.stringify(S)); } catch (e) {} };

  // ============================================================
  //  THE ENGINE — one audio graph shared by every theme
  // ============================================================
  const midi = m => 440 * Math.pow(2, (m - 69) / 12);
  const rand = (a, b) => a + Math.random() * (b - a);
  const pick = a => a[Math.floor(Math.random() * a.length)];

  let ctx = null, master = null, wet = null, analyser = null;

  function engine() {
    if (ctx) return ctx;
    const AC = window.AudioContext || window.webkitAudioContext;
    if (!AC) return null;
    ctx = new AC();
    master = ctx.createGain();
    master.gain.value = 0;
    // A gentle limiter, so no combination of notes can ever get harsh.
    const comp = ctx.createDynamicsCompressor();
    comp.threshold.value = -16; comp.knee.value = 10; comp.ratio.value = 3.5;
    comp.attack.value = 0.02; comp.release.value = 0.5;
    analyser = ctx.createAnalyser();
    analyser.fftSize = 1024;
    master.connect(comp); comp.connect(analyser); analyser.connect(ctx.destination);
    // One shared hall. A burst of noise that dies away slowly is all a
    // soft, wide reverb needs.
    const conv = ctx.createConvolver();
    const len = Math.floor(ctx.sampleRate * 3.2);
    const ir = ctx.createBuffer(2, len, ctx.sampleRate);
    for (let ch = 0; ch < 2; ch++) {
      const d = ir.getChannelData(ch);
      for (let i = 0; i < len; i++) d[i] = (Math.random() * 2 - 1) * Math.pow(1 - i / len, 2.8);
    }
    conv.buffer = ir;
    wet = ctx.createGain();
    wet.gain.value = 0.55;
    wet.connect(conv); conv.connect(master);
    return ctx;
  }

  // Everything is scheduled on the audio clock, well ahead of time, so a
  // background tab whose timers get slowed down still never runs dry.
  function scheduler(ahead, step) {
    let alive = true;
    const run = () => { if (alive) step(ctx.currentTime + ahead); };
    run();
    const id = setInterval(run, 400);
    return () => { alive = false; clearInterval(id); };
  }

  function panner(bus, pan) {
    if (!ctx.createStereoPanner) return bus;
    const p = ctx.createStereoPanner();
    p.pan.value = pan;
    p.connect(bus);
    return p;
  }

  function noiseBuffer(seconds, kind) {
    const len = Math.floor(ctx.sampleRate * seconds);
    const buf = ctx.createBuffer(2, len, ctx.sampleRate);
    for (let ch = 0; ch < 2; ch++) {
      const d = buf.getChannelData(ch);
      let b0 = 0, b1 = 0, b2 = 0, b3 = 0, b4 = 0, b5 = 0, b6 = 0, last = 0;
      for (let i = 0; i < len; i++) {
        const w = Math.random() * 2 - 1;
        if (kind === "brown") {                  // deep and rumbling
          last = (last + 0.02 * w) / 1.02;
          d[i] = last * 3.5;
        } else {                                  // pink: softer than white noise
          b0 = 0.99886 * b0 + w * 0.0555179; b1 = 0.99332 * b1 + w * 0.0750759;
          b2 = 0.96900 * b2 + w * 0.1538520; b3 = 0.86650 * b3 + w * 0.3104856;
          b4 = 0.55000 * b4 + w * 0.5329522; b5 = -0.7616 * b5 - w * 0.0168980;
          d[i] = (b0 + b1 + b2 + b3 + b4 + b5 + b6 + w * 0.5362) * 0.11;
          b6 = w * 0.115926;
        }
      }
    }
    return buf;
  }

  // A warm pad note: a sine and a triangle, a hair out of tune with each
  // other, rising and falling slowly.
  function padNote(bus, m, at, dur, level, attack, release) {
    const g = ctx.createGain();
    g.gain.setValueAtTime(0, at);
    g.gain.linearRampToValueAtTime(level, at + attack);
    g.gain.setValueAtTime(level, at + dur);
    g.gain.linearRampToValueAtTime(0, at + dur + release);
    g.connect(bus);
    let ended = 0;
    [["sine", -4], ["triangle", 5]].forEach(([type, cents]) => {
      const o = ctx.createOscillator();
      o.type = type;
      o.frequency.value = midi(m);
      o.detune.value = cents;
      o.connect(g);
      o.start(at);
      o.stop(at + dur + release + 0.05);
      o.onended = () => { try { o.disconnect(); if (++ended === 2) g.disconnect(); } catch (e) {} };
    });
  }

  // A soft bell: a tone and two quieter overtones, each dying away on its own.
  function bell(bus, m, at, level, pan) {
    const out = panner(bus, pan);
    [[1, 1, 5.5], [2, 0.28, 2.4], [3.01, 0.1, 1.2]].forEach(([ratio, amp, decay]) => {
      const o = ctx.createOscillator(), g = ctx.createGain();
      o.type = "sine";
      o.frequency.value = midi(m) * ratio;
      g.gain.setValueAtTime(0.0001, at);
      g.gain.exponentialRampToValueAtTime(level * amp, at + 0.015);
      g.gain.exponentialRampToValueAtTime(0.0001, at + decay);
      o.connect(g); g.connect(out);
      o.start(at);
      o.stop(at + decay + 0.05);
      o.onended = () => { try { o.disconnect(); g.disconnect(); } catch (e) {} };
    });
  }

  // A drip: a tiny tone that falls in pitch as it fades.
  function drip(bus, at) {
    const o = ctx.createOscillator(), g = ctx.createGain(), f = rand(1400, 2600);
    o.type = "sine";
    o.frequency.setValueAtTime(f, at);
    o.frequency.exponentialRampToValueAtTime(f * 0.55, at + 0.09);
    g.gain.setValueAtTime(0.0001, at);
    g.gain.exponentialRampToValueAtTime(rand(0.006, 0.016), at + 0.005);
    g.gain.exponentialRampToValueAtTime(0.0001, at + 0.12);
    o.connect(g); g.connect(panner(bus, rand(-0.8, 0.8)));
    o.start(at);
    o.stop(at + 0.15);
    o.onended = () => { try { o.disconnect(); g.disconnect(); } catch (e) {} };
  }

  // ============================================================
  //  THE THEMES
  //  Each starts on its own bus and returns a function that stops it.
  // ============================================================
  const THEMES = [
    {
      id: "still-water", name: "Still Water", icon: "fa-water",
      blurb: "Slow, warm chords that drift like a quiet lake.",
      grad: "linear-gradient(135deg,#1769c9,#22d3ee)",
      start(bus) {
        const lp = ctx.createBiquadFilter();
        lp.type = "lowpass"; lp.frequency.value = 1500; lp.Q.value = 0.4;
        const lfo = ctx.createOscillator(), amt = ctx.createGain();
        lfo.frequency.value = 0.045; amt.gain.value = 450;         // the filter breathes
        lfo.connect(amt); amt.connect(lp.frequency); lfo.start();
        lp.connect(bus);
        // Dmaj9 → Bm11 → Gmaj7(#11) → A(add9): no tension, nowhere to arrive.
        const CHORDS = [[50, 57, 61, 64, 66], [47, 54, 57, 62, 64], [43, 50, 54, 59, 61], [45, 52, 59, 61, 64]];
        let i = 0, next = ctx.currentTime + 0.1;
        const stop = scheduler(14, until => {
          while (next < until) {
            CHORDS[i++ % CHORDS.length].forEach((m, k) => padNote(lp, m, next + k * 0.12, 8.5, 0.05, 3.2, 4.5));
            next += 9;
          }
        });
        return () => { stop(); try { lfo.stop(); } catch (e) {} };
      },
    },
    {
      id: "night-sky", name: "Night Sky", icon: "fa-moon",
      blurb: "Soft chimes over a low hum, like stars coming out.",
      grad: "linear-gradient(135deg,#0b2a6f,#3b82f6)",
      start(bus) {
        const now = ctx.currentTime;
        const drone = [45, 52, 57].map((m, k) => {
          const o = ctx.createOscillator(), g = ctx.createGain();
          const trem = ctx.createOscillator(), depth = ctx.createGain();
          o.type = "sine"; o.frequency.value = midi(m);
          g.gain.setValueAtTime(0, now);
          g.gain.linearRampToValueAtTime([0.08, 0.05, 0.03][k], now + 4);
          trem.frequency.value = 0.07 + k * 0.03; depth.gain.value = 0.015;
          trem.connect(depth); depth.connect(g.gain);
          o.connect(g); g.connect(bus); o.start(); trem.start();
          return [o, trem];
        });
        // A major pentatonic, high up. Every note sounds right with every other.
        const SCALE = [69, 71, 73, 76, 78, 81, 83, 85, 88];
        let next = now + 1.5;
        const stop = scheduler(10, until => {
          while (next < until) {
            const k = Math.floor(Math.random() * SCALE.length);
            bell(bus, SCALE[k], next, rand(0.12, 0.2), rand(-0.6, 0.6));
            if (Math.random() < 0.3) {                            // sometimes an answer, lower down
              bell(bus, SCALE[Math.max(0, k - pick([1, 2, 3]))], next + rand(0.25, 0.6),
                   rand(0.07, 0.11), rand(-0.6, 0.6));
            }
            next += rand(1.4, 3.8);
          }
        });
        return () => { stop(); drone.flat().forEach(o => { try { o.stop(); } catch (e) {} }); };
      },
    },
    {
      id: "rainfall", name: "Rainfall", icon: "fa-cloud-rain",
      blurb: "Gentle rain on the window, with a quiet chord inside.",
      grad: "linear-gradient(135deg,#1e3a5f,#0ea5b7)",
      start(bus) {
        const now = ctx.currentTime;
        const src = ctx.createBufferSource();
        src.buffer = noiseBuffer(6, "pink"); src.loop = true;
        const hp = ctx.createBiquadFilter(); hp.type = "highpass"; hp.frequency.value = 500;
        const lp = ctx.createBiquadFilter(); lp.type = "lowpass"; lp.frequency.value = 5200;
        const g = ctx.createGain();
        g.gain.setValueAtTime(0, now);
        g.gain.linearRampToValueAtTime(0.45, now + 3);
        const sway = ctx.createOscillator(), sg = ctx.createGain();      // the rain comes and goes a little
        sway.frequency.value = 0.06; sg.gain.value = 0.08;
        sway.connect(sg); sg.connect(g.gain);
        src.connect(hp); hp.connect(lp); lp.connect(g); g.connect(bus);
        src.start(); sway.start();
        const padLp = ctx.createBiquadFilter();
        padLp.type = "lowpass"; padLp.frequency.value = 900; padLp.connect(bus);
        const CHORDS = [[45, 52, 55, 59, 64], [41, 48, 52, 57, 60], [43, 50, 55, 59, 62], [40, 47, 52, 55, 59]];
        let i = 0, nextChord = now + 2, nextDrop = now + 1;
        const stop = scheduler(12, until => {
          while (nextChord < until) {
            CHORDS[i++ % CHORDS.length].forEach((m, k) => padNote(padLp, m, nextChord + k * 0.2, 10, 0.024, 4, 5));
            nextChord += 11;
          }
          while (nextDrop < until) { drip(bus, nextDrop); nextDrop += rand(0.35, 1.6); }
        });
        return () => { stop(); try { src.stop(); sway.stop(); } catch (e) {} };
      },
    },
    {
      id: "ocean", name: "Ocean", icon: "fa-umbrella-beach",
      blurb: "Waves rolling in and out, slow as breathing.",
      grad: "linear-gradient(135deg,#0e7490,#1fd6a6)",
      start(bus) {
        const now = ctx.currentTime;
        const src = ctx.createBufferSource();
        src.buffer = noiseBuffer(8, "brown"); src.loop = true;
        const hp = ctx.createBiquadFilter(); hp.type = "highpass"; hp.frequency.value = 40;
        const lp = ctx.createBiquadFilter(); lp.type = "lowpass"; lp.frequency.value = 400; lp.Q.value = 0.3;
        const g = ctx.createGain(); g.gain.value = 0.03;
        src.connect(hp); hp.connect(lp); lp.connect(g); g.connect(bus);
        src.start();
        const drone = [38, 45].map((m, k) => {
          const o = ctx.createOscillator(), dg = ctx.createGain();
          o.type = "sine"; o.frequency.value = midi(m);
          dg.gain.setValueAtTime(0, now);
          dg.gain.linearRampToValueAtTime(k ? 0.03 : 0.05, now + 5);
          o.connect(dg); dg.connect(bus); o.start();
          return o;
        });
        // Each wave swells in, brightens as it breaks, and draws back out.
        let next = now + 0.2;
        const stop = scheduler(24, until => {
          while (next < until) {
            const len = rand(7, 11.5), peak = rand(0.35, 0.55), crest = next + len * rand(0.38, 0.5);
            g.gain.setValueAtTime(0.03, next);
            g.gain.linearRampToValueAtTime(peak, crest);
            g.gain.linearRampToValueAtTime(0.03, next + len);
            lp.frequency.setValueAtTime(350, next);
            lp.frequency.exponentialRampToValueAtTime(rand(1300, 2000), crest);
            lp.frequency.exponentialRampToValueAtTime(350, next + len);
            next += len;
          }
        });
        return () => { stop(); try { src.stop(); } catch (e) {} drone.forEach(o => { try { o.stop(); } catch (e) {} }); };
      },
    },
  ];

  // ============================================================
  //  YOUR SONGS — kept in this browser, filed under the citizen
  // ============================================================
  let dbp = null;
  function db() {
    if (dbp) return dbp;
    dbp = new Promise((res, rej) => {
      if (!window.indexedDB) return rej(new Error("This browser can't keep songs."));
      const r = indexedDB.open(DB_NAME, 1);
      r.onupgradeneeded = () => {
        if (!r.result.objectStoreNames.contains(STORE)) r.result.createObjectStore(STORE, { keyPath: "id" });
      };
      r.onsuccess = () => res(r.result);
      r.onerror = () => rej(r.error || new Error("This browser won't let the page keep songs."));
      r.onblocked = () => rej(new Error("Close Cyvathon in your other tabs, then try again."));
    });
    dbp.catch(() => { dbp = null; });
    return dbp;
  }
  function req(mode, fn) {
    return db().then(d => new Promise((res, rej) => {
      const t = d.transaction(STORE, mode), r = fn(t.objectStore(STORE));
      t.oncomplete = () => res(r ? r.result : undefined);
      t.onerror = t.onabort = () => rej(t.error || new Error("Couldn't save that song."));
    }));
  }
  // On a shared computer, each citizen only ever sees their own songs.
  const listSongs = () => req("readonly", s => s.getAll())
    .then(a => (a || []).filter(x => x.owner === ME).sort((x, y) => x.added - y.added));
  const getSong = id => req("readonly", s => s.get(id)).then(x => (x && x.owner === ME ? x : null));
  const putSong = rec => req("readwrite", s => s.put(rec));
  const delSong = id => req("readwrite", s => s.delete(id));

  let SONGS = [];
  async function refreshSongs() {
    try { SONGS = await listSongs(); } catch (e) { SONGS = []; }
    render();
  }

  // ============================================================
  //  PLAYING
  // ============================================================
  let live = null;           // { kind:"theme", id, bus, stop } | { kind:"song", id, el, url }
  let blocked = false, pending = null, token = null;
  const isPlaying = () => !!live && S.on && !blocked;
  const themeLevel = () => Math.pow(S.vol, 1.6) * 1.4;
  const songLevel = () => Math.min(1, Math.pow(S.vol, 1.6));

  function fadeEl(el, to, secs, done) {
    const from = el.volume, t0 = performance.now();
    clearInterval(el._fade);
    el._fade = setInterval(() => {
      const p = Math.min(1, (performance.now() - t0) / (secs * 1000));
      el.volume = Math.max(0, Math.min(1, from + (to - from) * p));
      if (p >= 1) { clearInterval(el._fade); if (done) done(); }
    }, 40);
  }

  function stopLive(fade) {
    const l = live;
    live = null;
    if (!l) return;
    if (l.kind === "theme") {
      const t = ctx.currentTime;
      l.bus.gain.cancelScheduledValues(t);
      l.bus.gain.setValueAtTime(l.bus.gain.value, t);
      l.bus.gain.linearRampToValueAtTime(0, t + fade);
      setTimeout(() => { try { l.stop(); l.bus.disconnect(); } catch (e) {} }, fade * 1000 + 80);
    } else {
      fadeEl(l.el, 0, fade, () => {
        l.el.pause();
        l.el.removeAttribute("src");
        l.el.load();
        URL.revokeObjectURL(l.url);
      });
    }
  }

  async function start(trackId, opts) {
    opts = opts || {};
    const fade = opts.fade == null ? 1.2 : opts.fade;
    const mine = token = {};
    stopLive(0.6);
    S.track = trackId;
    save();
    try {
      if (!trackId.startsWith("up:")) {
        const theme = THEMES.find(t => t.id === trackId) || THEMES[0];
        if (!engine()) throw new Error("This browser can't play the built-in themes.");
        // resume() has to be asked for inside the tap, before anything is awaited.
        if (ctx.state !== "running") { try { await ctx.resume(); } catch (e) {} }
        if (mine !== token) return false;
        if (ctx.state !== "running") return needTap(trackId);
        const bus = ctx.createGain(), now = ctx.currentTime;
        bus.gain.value = 0;
        bus.connect(master); bus.connect(wet);
        master.gain.cancelScheduledValues(now);
        master.gain.setValueAtTime(master.gain.value, now);
        master.gain.linearRampToValueAtTime(themeLevel(), now + 0.3);
        bus.gain.linearRampToValueAtTime(1, now + fade);
        live = { kind: "theme", id: theme.id, bus, stop: theme.start(bus) };
        S.t = 0;
      } else {
        const rec = await getSong(trackId.slice(3));
        if (!rec) throw new Error("That song isn't on this device any more.");
        const url = URL.createObjectURL(rec.blob);
        const el = new Audio();
        el.preload = "auto"; el.loop = true; el.volume = 0; el.src = url;
        await new Promise((res, rej) => {
          const t = setTimeout(() => rej(new Error("That song took too long to load.")), 8000);
          el.onloadedmetadata = () => { clearTimeout(t); res(); };
          el.onerror = () => { clearTimeout(t); rej(new Error("This browser can't play that file.")); };
        });
        if (isFinite(el.duration) && el.duration > 0) el.currentTime = (opts.at || 0) % el.duration;
        try { await el.play(); }
        catch (e) {
          URL.revokeObjectURL(url);
          if (e && e.name === "NotAllowedError") return mine === token ? needTap(trackId) : false;
          throw new Error("This browser can't play that file.");
        }
        if (mine !== token) { el.pause(); URL.revokeObjectURL(url); return false; }
        fadeEl(el, songLevel(), fade);
        live = { kind: "song", id: trackId, el, url };
      }
      blocked = false; pending = null;
      S.on = true; save();
      if (channel) channel.postMessage({ type: "playing", from: TAB });
      render(); mediaSession();
      return true;
    } catch (e) {
      if (mine === token) { S.on = false; save(); say(e.message || "Couldn't play that."); render(); }
      return false;
    }
  }

  // Sound isn't allowed to start on this page yet. Wait for the first tap
  // or key press anywhere, which is.
  function needTap(trackId) {
    blocked = true;
    pending = trackId;
    render();
    const go = () => {
      document.removeEventListener("pointerdown", go, true);
      document.removeEventListener("keydown", go, true);
      if (blocked && pending) start(pending, { at: pending.startsWith("up:") ? position() : 0 });
    };
    document.addEventListener("pointerdown", go, true);
    document.addEventListener("keydown", go, true);
    return false;
  }

  function pause() {
    savePosition();
    S.on = false;
    save();
    blocked = false; pending = null; token = {};
    stopLive(0.8);
    render(); mediaSession();
  }

  function toggle() {
    if (isPlaying()) pause();
    else start(S.track, { at: S.track.startsWith("up:") ? S.t : 0 });
  }

  function setVol(v) {
    S.vol = Math.max(0, Math.min(1, v));
    save();
    if (live && live.kind === "theme") {
      const t = ctx.currentTime;
      master.gain.cancelScheduledValues(t);
      master.gain.setTargetAtTime(themeLevel(), t, 0.05);
    } else if (live && live.kind === "song") {
      clearInterval(live.el._fade);
      live.el.volume = songLevel();
    }
  }

  // Where the song had got to, plus the moment it took to change pages.
  function position() {
    const gap = S.savedAt ? Math.min(3, Math.max(0, (Date.now() - S.savedAt) / 1000)) : 0;
    return (S.t || 0) + gap;
  }
  function savePosition() {
    if (live && live.kind === "song" && S.on && !blocked) {
      S.t = live.el.currentTime || 0;
      S.savedAt = Date.now();
      save();
    }
  }

  // With Cyvathon open in two tabs, the music follows the one being used.
  const channel = "BroadcastChannel" in window ? new BroadcastChannel("cyv-music:" + ME) : null;
  if (channel) {
    channel.onmessage = e => {
      if (e.data && e.data.type === "playing" && e.data.from !== TAB && live) {
        token = {};
        stopLive(0.5);
        render();
      }
    };
  }

  function mediaSession() {
    if (!("mediaSession" in navigator)) return;
    try {
      const t = trackInfo(S.track);
      navigator.mediaSession.metadata = new MediaMetadata({ title: t.name, artist: "Cyvathon theme music", album: "Cyvathon" });
      navigator.mediaSession.playbackState = isPlaying() ? "playing" : "paused";
      navigator.mediaSession.setActionHandler("play", () => start(S.track, { at: S.t }));
      navigator.mediaSession.setActionHandler("pause", pause);
    } catch (e) {}
  }

  // ============================================================
  //  ADDING AND REMOVING SONGS
  // ============================================================
  async function addFile(file) {
    if (!file) return false;
    if (!((file.type || "").startsWith("audio/") || AUDIO_EXT.test(file.name || ""))) {
      say("That isn't an audio file. Try an MP3, M4A, OGG or WAV.");
      return false;
    }
    if (file.size > MAX_MB * 1048576) { say(`That song is over ${MAX_MB} MB. Try a smaller file.`); return false; }
    if (file.type && !document.createElement("audio").canPlayType(file.type)) {
      say("This browser can't play that kind of file.");
      return false;
    }
    let songs;
    try { songs = await listSongs(); } catch (e) { say(e.message); return false; }
    if (songs.length >= MAX_SONGS) { say(`You can keep ${MAX_SONGS} songs. Remove one to add another.`); return false; }
    const rec = {
      id: Date.now().toString(36) + Math.random().toString(36).slice(2, 7),
      owner: ME,
      name: (file.name || "").replace(/\.[^.]+$/, "").slice(0, 60) || "My song",
      type: file.type || "audio/mpeg", size: file.size, added: Date.now(), blob: file,
    };
    try { await putSong(rec); }
    catch (e) {
      say(e && e.name === "QuotaExceededError" ? "There's no room left on this device for that song."
                                               : (e.message || "Couldn't save that song."));
      return false;
    }
    try { if (navigator.storage && navigator.storage.persist) navigator.storage.persist(); } catch (e) {}
    say(`Added “${rec.name}”.`, true);
    await refreshSongs();
    start("up:" + rec.id);
    return true;
  }

  async function remove(id) {
    const s = SONGS.find(x => x.id === id);
    if (!confirm(`Remove “${s ? s.name : "this song"}” from this device?`)) return;
    if (S.track === "up:" + id) {
      token = {};
      stopLive(0.4);
      blocked = false; pending = null;
      S.track = THEMES[0].id; S.t = 0; S.on = false;
      save();
    }
    try { await delSong(id); say("Removed.", true); } catch (e) { say(e.message); }
    refreshSongs();
  }

  // ============================================================
  //  THE PLAYER
  // ============================================================
  function trackInfo(id) {
    if (id && id.startsWith("up:")) {
      const s = SONGS.find(x => "up:" + x.id === id);
      return { name: s ? s.name : "Your song", icon: "fa-compact-disc",
               blurb: "Your song · kept on this device", grad: "linear-gradient(135deg,#1769c9,#1fd6a6)" };
    }
    return THEMES.find(t => t.id === id) || THEMES[0];
  }

  function say(msg, ok) {
    const m = $("cmMsg");
    if (!m) return;
    m.textContent = msg;
    m.className = "cm-msg " + (ok ? "ok" : "err");
    clearTimeout(say.t);
    say.t = setTimeout(() => { m.textContent = ""; }, 5000);
  }

  function render() {
    const root = $("cyvMusic");
    if (!root) return;
    const playing = isPlaying(), t = trackInfo(S.track);
    root.classList.toggle("is-playing", playing);
    root.classList.toggle("is-blocked", blocked);
    $("cmHint").hidden = !blocked;
    $("cmFab").setAttribute("aria-label", playing ? `Theme music — playing ${t.name}` : blocked ? "Theme music — tap to resume" : "Theme music");

    $("cmNow").style.background = t.grad;
    $("cmNow").innerHTML = `
      <div class="cm-now-ic" aria-hidden="true"><i class="fas ${t.icon}"></i></div>
      <div class="cm-now-t"><small>${playing ? "Now playing" : blocked ? "Tap to resume" : "Paused"}</small>
        <b>${esc(t.name)}</b><span>${esc(t.blurb)}</span></div>
      <button type="button" class="cm-play" data-act="toggle" aria-label="${playing ? "Pause" : "Play"}">
        <i class="fas fa-${playing ? "pause" : "play"}"></i></button>`;

    const vol = $("cmVol");
    if (document.activeElement !== vol) vol.value = Math.round(S.vol * 100);

    $("cmThemes").innerHTML = THEMES.map(th => {
      const on = S.track === th.id;
      return `<button type="button" class="cm-theme ${on ? "on" : ""}" data-track="${th.id}"
          style="background:${th.grad}" aria-pressed="${on}" title="${esc(th.blurb)}">
        <i class="fas ${th.icon}" aria-hidden="true"></i><b>${esc(th.name)}</b>
        ${on && playing ? `<span class="cm-eq sm" aria-hidden="true"><i></i><i></i><i></i></span>` : ""}</button>`;
    }).join("");

    $("cmCount").textContent = SONGS.length ? `${SONGS.length}/${MAX_SONGS}` : "";
    $("cmSongs").innerHTML = SONGS.length ? SONGS.map(s => {
      const id = "up:" + s.id, on = S.track === id;
      return `<div class="cm-song ${on ? "on" : ""}">
          <button type="button" class="cm-song-play" data-track="${esc(id)}" aria-label="Play ${esc(s.name)}">
            <i class="fas fa-${on && playing ? "volume-high" : "play"}"></i></button>
          <span class="cm-song-t"><b>${esc(s.name)}</b><small>${mb(s.size)}</small></span>
          <button type="button" class="cm-song-del" data-del="${esc(s.id)}" aria-label="Remove ${esc(s.name)}">
            <i class="fas fa-trash"></i></button></div>`;
    }).join("") : `<p class="cm-empty">No songs yet. Add one and it'll loop softly while you use Cyvathon.</p>`;
  }

  function openPanel(open) {
    const p = $("cmPanel");
    p.hidden = !open;
    $("cmFab").setAttribute("aria-expanded", String(open));
    if (open) { refreshSongs(); $("cmClose").focus(); }
  }

  const CSS = `
  .cm{ position:fixed; left:18px; bottom:18px; z-index:900; font-family:inherit; }
  .cm button{ width:auto; margin:0; font-family:inherit; box-shadow:none; }
  .cm .cm-fab{ position:relative; width:56px; height:56px; padding:0; border:none; border-radius:50%; cursor:pointer;
    display:grid; place-items:center; color:#fff; background:linear-gradient(135deg,#1769c9,#22d3ee);
    box-shadow:0 12px 30px rgba(34,211,238,.35), inset 0 1px 0 rgba(255,255,255,.3) !important;
    transition:transform .2s, box-shadow .2s; }
  .cm-fab:hover{ transform:translateY(-3px) scale(1.05); box-shadow:0 16px 38px rgba(34,211,238,.55) !important; }
  .cm-fab .cm-note{ font-size:1.25rem; }
  .cm.is-playing .cm-fab .cm-note{ display:none; }
  .cm-eq{ display:none; align-items:flex-end; gap:3px; height:20px; }
  .cm.is-playing .cm-fab .cm-eq{ display:flex; }
  .cm-eq i{ display:block; width:4px; height:30%; border-radius:2px; background:#fff; animation:cmEq 1s ease-in-out infinite; }
  .cm-eq i:nth-child(2){ animation-delay:-.3s } .cm-eq i:nth-child(3){ animation-delay:-.6s } .cm-eq i:nth-child(4){ animation-delay:-.15s }
  @keyframes cmEq{ 0%,100%{ height:25% } 50%{ height:100% } }
  .cm.is-playing .cm-fab::after{ content:""; position:absolute; inset:-6px; border-radius:50%;
    border:2px solid rgba(34,211,238,.55); animation:cmPulse 2.4s ease-out infinite; }
  @keyframes cmPulse{ from{ transform:scale(.9); opacity:1 } to{ transform:scale(1.35); opacity:0 } }
  .cm.is-blocked .cm-fab{ color:#231500; background:linear-gradient(135deg,#c98a06,#ffce56); }
  .cm-hint{ position:absolute; left:68px; bottom:13px; white-space:nowrap; padding:.5rem .9rem; border-radius:20px;
    font-size:.8rem; font-weight:800; color:#231500; background:#ffce56; box-shadow:0 8px 20px rgba(0,0,0,.25); pointer-events:none; }
  .cm-hint[hidden], .cm-panel[hidden]{ display:none !important; }
  .cm-panel{ position:absolute; left:0; bottom:72px; width:350px; max-width:calc(100vw - 36px); max-height:min(72vh,660px);
    overflow:auto; padding:1rem; border-radius:22px; background:var(--surface,#111a2e); color:var(--text,#e8eef6);
    border:1px solid var(--panel-border-2,rgba(140,180,255,.24)); box-shadow:0 24px 60px rgba(0,0,0,.45);
    animation:cmIn .22s cubic-bezier(.2,.9,.3,1.2) both; }
  @keyframes cmIn{ from{ opacity:0; transform:translateY(12px) scale(.97) } to{ opacity:1; transform:none } }
  .cm-head{ display:flex; justify-content:space-between; align-items:center; margin-bottom:.8rem; }
  .cm-head b{ display:flex; align-items:center; gap:.5rem; font-size:1.05rem; }
  .cm-head b i{ color:var(--cyan,#22d3ee); }
  .cm-x{ width:34px !important; height:34px; padding:0; border:none; border-radius:50%; cursor:pointer;
    color:var(--text); background:var(--panel-2,rgba(120,170,255,.09)); }
  .cm-now{ display:flex; align-items:center; gap:.8rem; padding:1rem; border-radius:18px; color:#fff;
    box-shadow:0 12px 28px rgba(0,0,0,.25); }
  .cm-now-ic{ width:48px; height:48px; flex:none; border-radius:14px; display:grid; place-items:center;
    font-size:1.3rem; background:rgba(255,255,255,.22); }
  .cm-now-t{ flex:1; min-width:0; }
  .cm-now-t small{ display:block; font-size:.66rem; font-weight:800; letter-spacing:.12em; text-transform:uppercase; opacity:.9; }
  .cm-now-t b{ display:block; font-size:1.12rem; font-weight:800; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
  .cm-now-t span{ display:block; font-size:.78rem; line-height:1.35; opacity:.92; }
  .cm-play{ width:50px !important; height:50px; flex:none; padding:0; border:none; border-radius:50%; cursor:pointer;
    font-size:1.1rem; color:#0b2a4a; background:#fff; box-shadow:0 8px 18px rgba(0,0,0,.25) !important; transition:transform .15s; }
  .cm-play:hover{ transform:scale(1.07); }
  .cm-vol{ display:flex; align-items:center; gap:.6rem; margin:.9rem .2rem .1rem; font-size:.85rem; color:var(--muted,#9db0c6); }
  .cm-vol input{ flex:1; width:auto; margin:0; padding:0; border:none; background:transparent; box-shadow:none; accent-color:#22d3ee; }
  .cm-h{ display:flex; gap:.45rem; margin:1rem 0 .5rem; font-size:.72rem; font-weight:800; letter-spacing:.14em;
    text-transform:uppercase; color:var(--muted,#9db0c6); }
  .cm-sub{ letter-spacing:0; opacity:.8; }
  .cm-themes{ display:grid; grid-template-columns:1fr 1fr; gap:.5rem; }
  .cm-theme{ position:relative; min-height:76px; padding:.8rem .75rem; border:2px solid transparent; border-radius:16px;
    cursor:pointer; text-align:left; color:#fff; display:flex; flex-direction:column; justify-content:space-between; gap:.35rem;
    box-shadow:0 6px 16px rgba(0,0,0,.2) !important; transition:transform .18s, border-color .18s; }
  .cm-theme i{ font-size:1.15rem; }
  .cm-theme b{ font-size:.92rem; font-weight:800; }
  .cm-theme:hover{ transform:translateY(-2px); }
  .cm-theme.on{ border-color:#fff; box-shadow:0 0 0 3px rgba(34,211,238,.45) !important; }
  .cm-eq.sm{ position:absolute; top:.65rem; right:.65rem; display:flex; height:14px; }
  .cm-eq.sm i{ width:3px; }
  .cm-songs{ display:grid; gap:.4rem; }
  .cm-song{ display:flex; align-items:center; gap:.55rem; padding:.45rem .5rem; border-radius:12px;
    background:var(--panel-2,rgba(120,170,255,.09)); border:1px solid var(--panel-border,rgba(140,180,255,.14)); }
  .cm-song.on{ border-color:rgba(34,211,238,.6); }
  .cm-song-play, .cm-song-del{ width:34px !important; height:34px; flex:none; padding:0; border:none; border-radius:10px; cursor:pointer; }
  .cm-song-play{ color:#fff; background:linear-gradient(135deg,#1769c9,#22d3ee); }
  .cm-song-del{ color:var(--muted,#9db0c6); background:transparent; }
  .cm-song-del:hover{ color:var(--red,#ff5d6c); background:rgba(255,93,108,.12); }
  .cm-song-t{ flex:1; min-width:0; }
  .cm-song-t b{ display:block; font-size:.86rem; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
  .cm-song-t small{ font-size:.72rem; color:var(--muted,#9db0c6); }
  .cm-empty{ margin:0; font-size:.82rem; line-height:1.45; color:var(--muted,#9db0c6); }
  .cm-up{ display:flex; align-items:center; justify-content:center; gap:.5rem; margin-top:.7rem; padding:.7rem;
    border-radius:14px; cursor:pointer; font-weight:800; font-size:.9rem; color:var(--accent,#58c4ff);
    border:2px dashed rgba(88,196,255,.5); background:rgba(88,196,255,.06); }
  .cm-up:hover{ background:rgba(88,196,255,.14); }
  .cm-priv{ margin:.6rem 0 0; font-size:.74rem; line-height:1.45; color:var(--muted,#9db0c6); }
  .cm-msg{ margin:.5rem 0 0; min-height:1.1em; font-size:.8rem; font-weight:600; }
  .cm-msg.ok{ color:var(--green,#1fd6a6); } .cm-msg.err{ color:var(--red,#ff5d6c); }
  .cm button:focus-visible, .cm-up:focus-within{ outline:2px solid #22d3ee; outline-offset:2px; }
  body.playing .cm{ display:none; }
  @media (max-width:480px){
    .cm{ left:12px; bottom:12px; }
    .cm-panel{ position:fixed; left:10px; right:10px; bottom:80px; width:auto; max-width:none; }
    .cm-hint{ display:none !important; }
  }
  @media (prefers-reduced-motion: reduce){
    .cm-eq i, .cm.is-playing .cm-fab::after, .cm-panel{ animation:none !important; }
    .cm-eq i{ height:70%; }
  }`;

  function mount() {
    const style = document.createElement("style");
    style.id = "cyvMusicCss";
    style.textContent = CSS;
    document.head.appendChild(style);
    const root = document.createElement("div");
    root.id = "cyvMusic";
    root.className = "cm";
    root.innerHTML = `
      <button type="button" class="cm-fab" id="cmFab" aria-expanded="false" aria-controls="cmPanel" aria-label="Theme music">
        <span class="cm-eq" aria-hidden="true"><i></i><i></i><i></i><i></i></span>
        <i class="fas fa-music cm-note" aria-hidden="true"></i>
      </button>
      <span class="cm-hint" id="cmHint" hidden>Tap anywhere to keep the music playing</span>
      <section class="cm-panel" id="cmPanel" role="dialog" aria-label="Theme music" hidden>
        <header class="cm-head"><b><i class="fas fa-headphones" aria-hidden="true"></i> Theme music</b>
          <button type="button" class="cm-x" id="cmClose" aria-label="Close"><i class="fas fa-xmark"></i></button></header>
        <div class="cm-now" id="cmNow"></div>
        <div class="cm-vol"><i class="fas fa-volume-low" aria-hidden="true"></i>
          <input type="range" id="cmVol" min="0" max="100" step="1" aria-label="Volume">
          <i class="fas fa-volume-high" aria-hidden="true"></i></div>
        <h4 class="cm-h">Soothing themes</h4>
        <div class="cm-themes" id="cmThemes"></div>
        <h4 class="cm-h">Your songs <span class="cm-sub" id="cmCount"></span></h4>
        <div class="cm-songs" id="cmSongs"></div>
        <label class="cm-up"><input type="file" id="cmFile" hidden
            accept="audio/*,.mp3,.m4a,.aac,.ogg,.oga,.opus,.wav,.flac,.webm">
          <i class="fas fa-cloud-arrow-up" aria-hidden="true"></i> Upload a song</label>
        <p class="cm-priv"><i class="fas fa-lock" aria-hidden="true"></i> Songs you add stay on this device and only you
          can see them. They're never uploaded to Cyvathon. Up to ${MAX_SONGS} songs, ${MAX_MB} MB each.</p>
        <p class="cm-msg" id="cmMsg" role="status"></p>
      </section>`;
    document.body.appendChild(root);

    $("cmFab").addEventListener("click", () => openPanel($("cmPanel").hidden));
    $("cmClose").addEventListener("click", () => { openPanel(false); $("cmFab").focus(); });
    $("cmVol").addEventListener("input", e => setVol(e.target.value / 100));
    $("cmFile").addEventListener("change", e => {
      const f = e.target.files && e.target.files[0];
      e.target.value = "";
      addFile(f);
    });
    root.addEventListener("click", e => {
      const b = e.target.closest("[data-act],[data-track],[data-del]");
      if (!b) return;
      if (b.dataset.act === "toggle") return toggle();
      if (b.dataset.del) return remove(b.dataset.del);
      const id = b.dataset.track;
      if (S.track === id && isPlaying()) return pause();
      start(id);
    });
    document.addEventListener("keydown", e => {
      if (e.key === "Escape" && !$("cmPanel").hidden) { openPanel(false); $("cmFab").focus(); }
    });
    document.addEventListener("click", e => {
      if (!$("cmPanel").hidden && !root.contains(e.target)) openPanel(false);
    });
  }

  // ---------------- start up ----------------
  mount();
  render();
  refreshSongs();
  if (S.on) start(S.track, { at: S.track.startsWith("up:") ? position() : 0, fade: 1.6 });
  setInterval(savePosition, 2000);
  addEventListener("pagehide", savePosition);
  document.addEventListener("visibilitychange", () => { if (document.hidden) savePosition(); });

  window.CyvMusic = {
    start, pause, toggle, setVol, addFile, remove,
    themes: () => THEMES.map(t => ({ id: t.id, name: t.name })),
    state: () => Object.assign({ playing: isPlaying(), blocked, live: live && { kind: live.kind, id: live.id } }, S),
    songs: () => SONGS.map(s => ({ id: s.id, name: s.name, size: s.size })),
    level() {                                  // how loud the themes are right now, 0..1
      if (!analyser) return 0;
      const d = new Float32Array(analyser.fftSize);
      analyser.getFloatTimeDomainData(d);
      let peak = 0, sum = 0;
      for (const v of d) { peak = Math.max(peak, Math.abs(v)); sum += v * v; }
      return { peak, rms: Math.sqrt(sum / d.length) };
    },
    song: () => live && live.kind === "song" ? { paused: live.el.paused, time: live.el.currentTime, volume: live.el.volume } : null,
  };
})();
