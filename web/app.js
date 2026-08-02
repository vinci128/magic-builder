const $ = (id) => document.getElementById(id);

const COLOR_NAMES = { W: "White", U: "Blue", B: "Black", R: "Red", G: "Green", C: "Colorless" };

const state = {
  collectionId: null,
  format: "commander",
  commander: null,      // chosen commander name, null = best available
  colors: new Set(),    // forced Standard colours
  deck: null,
};

// ── Card database status ────────────────────────────────────────────────────

async function pollStatus() {
  try {
    const res = await fetch("/api/status");
    const data = await res.json();
    const box = $("db-status");
    if (data.card_db_error) {
      box.dataset.state = "error";
      $("db-status-text").textContent = "Card database failed to load";
      return;
    }
    if (data.card_db_ready) {
      box.dataset.state = "ready";
      $("db-status-text").textContent = "Card database ready";
      return;
    }
    $("db-status-text").textContent = "Loading card database…";
    setTimeout(pollStatus, 2000);
  } catch {
    setTimeout(pollStatus, 3000);
  }
}

// ── Upload ──────────────────────────────────────────────────────────────────

const drop = $("drop");
const fileInput = $("file-input");

drop.addEventListener("keydown", (e) => {
  if (e.key === "Enter" || e.key === " ") { e.preventDefault(); fileInput.click(); }
});

["dragenter", "dragover"].forEach((evt) =>
  drop.addEventListener(evt, (e) => { e.preventDefault(); drop.classList.add("is-over"); })
);
["dragleave", "drop"].forEach((evt) =>
  drop.addEventListener(evt, () => drop.classList.remove("is-over"))
);

drop.addEventListener("drop", (e) => {
  e.preventDefault();
  const file = e.dataTransfer.files[0];
  if (file) upload(file);
});

fileInput.addEventListener("change", () => {
  if (fileInput.files[0]) upload(fileInput.files[0]);
});

async function upload(file) {
  showError("upload-error", "");
  $("drop").querySelector(".drop-line").textContent = "Reading collection…";

  const body = new FormData();
  body.append("file", file);
  try {
    const res = await fetch("/api/collection", { method: "POST", body });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "Upload failed.");
    state.collectionId = data.id;
    renderStats(data);
    $("build-panel").hidden = false;
    $("build-panel").classList.add("enter");
    loadCommanders();
  } catch (err) {
    showError("upload-error", err.message);
  } finally {
    $("drop").querySelector(".drop-line").textContent = "Drop your collection file";
  }
}

function renderStats(data) {
  $("stats").hidden = false;
  $("stat-filename").textContent = data.filename || "collection";
  $("stat-format").textContent = data.source_format;
  $("stat-unique").textContent = data.unique.toLocaleString();
  $("stat-total").textContent = data.total.toLocaleString();
  $("stat-cmd").textContent = data.commander_legal.toLocaleString();
  $("stat-std").textContent = data.standard_legal.toLocaleString();
  renderPipMeter($("collection-colors"), data.colors);
}

// ── Pip meter: stacked colour bar, every segment labelled ───────────────────

function renderPipMeter(el, counts) {
  el.replaceChildren();
  const entries = Object.entries(counts).filter(([, n]) => n > 0);
  const total = entries.reduce((sum, [, n]) => sum + n, 0);
  if (!total) {
    el.innerHTML = '<span class="curve-label">No coloured cards</span>';
    return;
  }
  for (const [color, n] of entries) {
    const share = n / total;
    const seg = document.createElement("div");
    seg.className = `pip-seg pip-${color}`;
    seg.style.flex = `${share}`;
    seg.textContent = share > 0.06 ? color : "";
    seg.title = `${COLOR_NAMES[color] || color}: ${n} (${Math.round(share * 100)}%)`;
    el.append(seg);
  }
}

// ── Format tabs ─────────────────────────────────────────────────────────────

document.querySelectorAll(".tab").forEach((tab) => {
  tab.addEventListener("click", () => {
    state.format = tab.dataset.format;
    document.querySelectorAll(".tab").forEach((t) => {
      const on = t === tab;
      t.classList.toggle("is-on", on);
      t.setAttribute("aria-selected", String(on));
    });
    $("pane-commander").hidden = state.format !== "commander";
    $("pane-standard").hidden = state.format !== "standard";
  });
});

$("color-picker").addEventListener("click", (e) => {
  const btn = e.target.closest(".color-btn");
  if (!btn) return;
  const color = btn.dataset.color;
  const on = !state.colors.has(color);
  on ? state.colors.add(color) : state.colors.delete(color);
  btn.setAttribute("aria-pressed", String(on));
});

$("opt-edhrec").addEventListener("change", loadCommanders);

// ── Commander candidates ────────────────────────────────────────────────────

async function loadCommanders() {
  if (!state.collectionId) return;
  const list = $("commander-list");
  const edhrec = $("opt-edhrec").checked;
  list.innerHTML = '<p class="pane-note">Scoring commanders…</p>';

  try {
    const res = await fetch(
      `/api/collection/${state.collectionId}/commanders?limit=30&edhrec=${edhrec}`
    );
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "Could not score commanders.");
    list.replaceChildren();
    if (!data.commanders.length) {
      list.innerHTML = '<p class="pane-note">No Commander-legal legendary creatures here. The Standard tab still works.</p>';
      return;
    }
    data.commanders.forEach((card, i) => {
      const btn = document.createElement("button");
      btn.className = "cmd" + (i === 0 ? " is-on" : "");
      btn.dataset.name = card.name;
      btn.innerHTML = `<span class="cmd-name"></span>
        <span class="cmd-meta">${pips(card.colors)}<span class="cmd-score">${card.score}</span></span>`;
      btn.querySelector(".cmd-name").textContent = card.name;
      btn.title = `${card.type_line} · ${card.score} cards in your collection fit its colour identity`;
      btn.addEventListener("click", () => {
        state.commander = card.name;
        list.querySelectorAll(".cmd").forEach((c) => c.classList.toggle("is-on", c === btn));
      });
      attachPreview(btn, card.image_url);
      list.append(btn);
    });
    state.commander = data.commanders[0].name;
  } catch (err) {
    list.innerHTML = "";
    showError("build-error", err.message);
  }
}

const pips = (colors) =>
  (colors && colors.length ? colors : ["C"]).map((c) => `<span class="pip pip-${c}"></span>`).join("");

// ── Build ───────────────────────────────────────────────────────────────────

$("build-btn").addEventListener("click", build);

async function build() {
  if (!state.collectionId) return;
  showError("build-error", "");
  setWorking(true, "Building the deck…");

  const commanderMode = state.format === "commander";
  const url = `/api/collection/${state.collectionId}/deck/${commanderMode ? "commander" : "standard"}`;
  const payload = commanderMode
    ? { commander_name: state.commander }
    : { colors: [...state.colors].join("") || null };

  try {
    const res = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "Deck build failed.");
    state.deck = data;
    renderDeck(data);
  } catch (err) {
    showError("build-error", err.message);
  } finally {
    setWorking(false);
  }
}

function setWorking(on, text = "") {
  $("build-btn").disabled = on;
  $("working").hidden = !on;
  if (on) {
    $("working-text").textContent = text;
    $("empty").hidden = true;
    $("deck").hidden = true;
  }
}

// ── Deck rendering ──────────────────────────────────────────────────────────

function renderDeck(deck) {
  $("empty").hidden = true;
  $("deck").hidden = false;
  $("deck").classList.remove("enter");
  void $("deck").offsetWidth;
  $("deck").classList.add("enter");

  const label = deck.colors.map((c) => COLOR_NAMES[c] || c).join(" · ");
  if (deck.commander) {
    $("deck-kicker").textContent = "Commander";
    $("deck-name").textContent = deck.commander.name;
    $("deck-type").textContent = deck.commander.type_line;
    attachPreview($("deck-name"), deck.commander.image_url);
  } else {
    $("deck-kicker").textContent = "Standard";
    $("deck-name").textContent = `${label} deck`;
    $("deck-type").textContent = "60 cards, Standard legal, built from what you own";
  }
  $("deck-identity").innerHTML = pips(deck.colors);

  const nonland = deck.curve.reduce((a, b) => a + b, 0);
  const weighted = deck.curve.reduce((sum, n, cmc) => sum + n * cmc, 0);
  const lands = (deck.categories.find((c) => c.name === "Lands") || { count: 0 }).count;
  $("deck-count").textContent = deck.total;
  $("deck-avg").textContent = nonland ? (weighted / nonland).toFixed(2) : "0";
  $("deck-lands").textContent = lands;

  renderCurve(deck.curve);
  renderPipMeter($("deck-pips"), deck.pips);
  renderSynergy(deck.synergy);
  renderColumns(deck.categories);

  $("plain").textContent = deck.pretty;
  $("plain").hidden = true;
  $("toggle-text").textContent = "Show plain text";

  $("deck").scrollIntoView({ behavior: "smooth", block: "start" });

  // Recommendations need a round trip to EDHREC, so they land after the deck.
  if (deck.commander) loadRecommendations(deck.commander.name);
  else $("recs").hidden = true;
}

// ── EDHREC recommendations ──────────────────────────────────────────────────

async function loadRecommendations(commanderName) {
  const section = $("recs");
  section.hidden = false;
  $("recs-note").textContent = "Reading EDHREC…";
  $("recs-grid").replaceChildren();

  try {
    const url = `/api/collection/${state.collectionId}/recommendations?commander=${encodeURIComponent(commanderName)}`;
    const res = await fetch(url);
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "EDHREC lookup failed.");
    if (data.error) {
      $("recs-note").textContent = data.error;
      return;
    }
    $("recs-note").textContent =
      `${data.in_deck} of the ${data.total} cards EDHREC associates with ${commanderName} are already in this deck.`;
    renderRecColumn("Own it, not in the deck", "is-own", data.upgrades,
      "The builder already used every recommended card you own.");
    renderRecColumn("Worth acquiring", "is-buy", data.acquire,
      "EDHREC has nothing to add here.");
  } catch (err) {
    $("recs-note").textContent = err.message;
  }
}

function renderRecColumn(title, variant, recs, emptyText) {
  const col = document.createElement("section");
  col.className = `rec-col ${variant} enter`;
  const head = document.createElement("div");
  head.className = "rec-head";
  head.innerHTML = `<h4></h4><p>${
    variant === "is-own"
      ? "Cards in your collection that fit this commander"
      : "Cards you don't own that most of these decks run"
  }</p>`;
  head.querySelector("h4").textContent = `${title} (${recs.length})`;
  col.append(head);

  if (!recs.length) {
    const empty = document.createElement("p");
    empty.className = "rec-empty";
    empty.textContent = emptyText;
    col.append(empty);
  }

  recs.forEach((rec) => {
    const row = document.createElement("div");
    row.className = "rec";
    const pct = Math.round(rec.inclusion * 100);
    row.innerHTML = `<span class="rec-name"></span>
      <span class="rec-bar"><i><span style="width:${Math.min(pct, 100)}%"></span></i>${pct}%</span>`;
    row.querySelector(".rec-name").textContent = rec.name;
    row.title = `${rec.type_line || rec.category} — in ${pct}% of ${rec.num_decks.toLocaleString()} decks, synergy ${rec.synergy >= 0 ? "+" : ""}${rec.synergy.toFixed(2)}`;
    attachPreview(row, rec.image_url);
    col.append(row);
  });

  $("recs-grid").append(col);
}

function renderCurve(curve) {
  const el = $("curve");
  el.replaceChildren();
  const max = Math.max(...curve, 1);
  curve.forEach((n, cmc) => {
    const col = document.createElement("div");
    col.className = "curve-col";
    col.title = `${n} card${n === 1 ? "" : "s"} at mana value ${cmc === 7 ? "7 or more" : cmc}`;
    col.innerHTML = `
      <span class="curve-val">${n || ""}</span>
      <div class="curve-bar" style="height:0"></div>
      <span class="curve-label">${cmc === 7 ? "7+" : cmc}</span>`;
    el.append(col);
    requestAnimationFrame(() => {
      col.querySelector(".curve-bar").style.height = `${Math.round((n / max) * 78)}%`;
    });
  });
}

// ── Synergy ─────────────────────────────────────────────────────────────────

function renderSynergy(syn) {
  const section = $("synergy");
  if (!syn || (!syn.themes.length && !syn.top.length)) {
    section.hidden = true;
    return;
  }
  section.hidden = false;

  $("synergy-title").textContent =
    syn.kind === "commander" ? "Why these cards" : "What this deck is doing";
  $("synergy-headline").textContent = syn.headline;

  // Bars are relative to the most common theme, so the shape reads at a glance.
  const themes = $("synergy-themes");
  themes.replaceChildren();
  const peak = Math.max(...syn.themes.map((t) => t.count), 1);
  syn.themes.forEach((theme) => {
    const item = document.createElement("div");
    item.className = "syn-theme";
    item.innerHTML = `<span class="syn-theme-name"></span>
      <span class="syn-bar"><i style="width:${Math.round((theme.count / peak) * 100)}%"></i></span>
      <span class="syn-theme-count">${theme.count}</span>`;
    item.querySelector(".syn-theme-name").textContent = theme.label;
    themes.append(item);
  });

  const top = $("synergy-top");
  top.replaceChildren();
  syn.top.forEach((card) => {
    const li = document.createElement("li");
    li.className = "syn-card";
    li.innerHTML = `<span class="syn-card-name"></span>
      <span class="syn-card-why"></span>
      <span class="syn-card-score">${card.score.toFixed(1)}</span>`;
    li.querySelector(".syn-card-name").textContent = card.name;
    li.querySelector(".syn-card-why").textContent = card.why.join(" · ");
    top.append(li);
  });

  const drags = $("synergy-drags");
  if (syn.drags && syn.drags.length) {
    drags.hidden = false;
    drags.replaceChildren();
    const head = document.createElement("p");
    head.className = "syn-drags-head";
    head.textContent = "Scored against, kept only because the slot had to be filled:";
    drags.append(head);
    syn.drags.forEach((drag) => {
      const line = document.createElement("p");
      line.className = "syn-drag";
      line.innerHTML = `<span class="syn-drag-name"></span><span class="syn-drag-why"></span>`;
      line.querySelector(".syn-drag-name").textContent = drag.name;
      line.querySelector(".syn-drag-why").textContent = drag.label;
      drags.append(line);
    });
  } else {
    drags.hidden = true;
  }
}

function renderColumns(categories) {
  const el = $("columns");
  el.replaceChildren();
  categories.forEach((cat, i) => {
    const box = document.createElement("section");
    box.className = "cat enter";
    box.style.animationDelay = `${Math.min(i * 45, 300)}ms`;
    const head = document.createElement("div");
    head.className = "cat-head";
    head.innerHTML = `<h3></h3><span>${cat.count}</span>`;
    head.querySelector("h3").textContent = cat.name;
    box.append(head);

    cat.cards.forEach((card) => {
      const row = document.createElement("div");
      row.className = "row" + (card.is_filler ? " is-filler" : "");
      // Only Commander decks carry per-card synergy; Standard rows keep 3 columns.
      const hasSynergy = typeof card.synergy === "number";
      if (hasSynergy) row.classList.add("with-syn");
      row.innerHTML = `<span class="row-count">${card.count > 1 ? card.count + "×" : ""}</span>
        <span class="row-name"></span>
        ${hasSynergy ? '<span class="row-syn"></span>' : ""}
        <span class="row-cmc">${card.mana_cost || ""}</span>`;
      row.querySelector(".row-name").textContent = card.name;
      if (hasSynergy) {
        const syn = row.querySelector(".row-syn");
        syn.textContent = card.synergy ? card.synergy.toFixed(1) : "";
        if (card.synergy < 0) syn.classList.add("is-drag");
      }
      const why = (card.synergy_why || []).join(" · ");
      row.title = card.is_filler
        ? `${card.type_line} — added as filler, not from your collection`
        : why
          ? `${card.type_line}\nSynergy ${card.synergy.toFixed(1)} — ${why}`
          : card.type_line;
      attachPreview(row, card.image_url);
      box.append(row);
    });
    el.append(box);
  });
}

// ── Card art preview ────────────────────────────────────────────────────────

const preview = $("preview");
const previewImg = $("preview-img");

function attachPreview(el, url) {
  if (!url) return;
  const first = !el.dataset.preview;
  el.dataset.preview = url;   // re-rendered elements just update the url
  if (!first) return;
  el.addEventListener("pointerenter", (e) => {
    if (e.pointerType !== "mouse") return;
    previewImg.src = el.dataset.preview;
    preview.hidden = false;
    movePreview(e);
  });
  el.addEventListener("pointermove", movePreview);
  el.addEventListener("pointerleave", () => { preview.hidden = true; });
}

function movePreview(e) {
  const width = 244, height = 340, pad = 16;
  const x = Math.min(e.clientX + pad, window.innerWidth - width - pad);
  const y = Math.min(e.clientY + pad, window.innerHeight - height - pad);
  preview.style.left = `${Math.max(pad, x)}px`;
  preview.style.top = `${Math.max(pad, y)}px`;
}

// ── Deck actions ────────────────────────────────────────────────────────────

$("copy-list").addEventListener("click", async () => {
  if (!state.deck) return;
  await navigator.clipboard.writeText(state.deck.decklist);
  flash($("copy-list"), "Copied");
});

$("download-list").addEventListener("click", () => {
  if (!state.deck) return;
  const name = state.deck.commander
    ? state.deck.commander.name.replace(/[^a-z0-9]+/gi, "_").toLowerCase()
    : `standard_${state.deck.colors.join("").toLowerCase()}`;
  const blob = new Blob([state.deck.decklist], { type: "text/plain" });
  const link = document.createElement("a");
  link.href = URL.createObjectURL(blob);
  link.download = `${name}.txt`;
  link.click();
  URL.revokeObjectURL(link.href);
});

$("toggle-text").addEventListener("click", () => {
  const showing = !$("plain").hidden;
  $("plain").hidden = showing;
  $("columns").hidden = !showing;
  $("toggle-text").textContent = showing ? "Show plain text" : "Show card list";
});

function flash(btn, text) {
  const original = btn.textContent;
  btn.textContent = text;
  setTimeout(() => { btn.textContent = original; }, 1400);
}

function showError(id, message) {
  const el = $(id);
  el.textContent = message;
  el.hidden = !message;
}

pollStatus();
