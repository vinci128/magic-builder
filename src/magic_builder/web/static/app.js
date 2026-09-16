const $ = (id) => document.getElementById(id);

const COLOR_NAMES = { W: "White", U: "Blue", B: "Black", R: "Red", G: "Green", C: "Colorless" };

// Formats built around a commander, as opposed to constructed 4-of decks.
// Mirrors formats.py — the tabs in index.html are the same four keys.
const SINGLETON = new Set(["commander", "brawl"]);

const money = (n) =>
  n.toLocaleString(undefined, { style: "currency", currency: "USD", maximumFractionDigits: n < 100 ? 2 : 0 });

const state = {
  collectionId: null,
  format: "commander",
  commander: null,      // chosen commander name, null = best available
  colors: new Set(),    // forced colours for constructed formats
  targetBracket: null,  // build to this bracket, null = wherever it lands
  deck: null,
  precon: null,         // swap report for the precon currently on the stage
  preconList: [],       // detected precons, with EDHREC's pairings for each
  pairing: "",          // "A // B" chosen for the precon on the stage, "" = EDHREC default
};

// What asking for each bracket actually does to the build. Mirrors the
// restrictions in brackets.py, in the second person.
const TARGET_HINTS = {
  "": "Builds the strongest deck it can and reports whichever bracket it lands in.",
  1: "Exhibition: no Game Changers, no mass land denial, no extra turns at all. " +
     "Still reported as a 2 — bracket 1 is a claim about intent that no decklist can make.",
  2: "Core: no Game Changers, no mass land denial, no chained extra turns. " +
     "The classic casual game, decided on turn 8 or later.",
  3: "Upgraded: seeds in up to 3 Game Changers, which is what reaches this bracket. " +
     "Still no land denial or chained extra turns.",
  4: "Optimized: no restrictions, and every Game Changer you own goes in.",
  5: "cEDH: same build as 4 — nothing in a decklist separates the two, so the deck " +
     "is reported as a 4 at most.",
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
    loadPrecons();
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

  const cells = [
    ["Unique cards", data.unique.toLocaleString()],
    ["Total copies", data.total.toLocaleString()],
    ...data.formats.map((f) => [`${f.label} legal`, (data.legal[f.key] || 0).toLocaleString()]),
  ];
  const grid = $("stat-grid");
  grid.replaceChildren();
  for (const [label, value] of cells) {
    const cell = document.createElement("div");
    cell.innerHTML = "<dt></dt><dd></dd>";
    cell.querySelector("dt").textContent = label;
    cell.querySelector("dd").textContent = value;
    grid.append(cell);
  }

  renderPipMeter($("collection-colors"), data.colors);
  $("stat-value").hidden = !data.value;
  if (data.value) $("stat-value").textContent = `${money(data.value)} of paper, at Scryfall's prices.`;
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
    const previous = state.format;
    state.format = tab.dataset.format;
    document.querySelectorAll(".tab").forEach((t) => {
      const on = t === tab;
      t.classList.toggle("is-on", on);
      t.setAttribute("aria-selected", String(on));
    });
    const singleton = SINGLETON.has(state.format);
    $("pane-commander").hidden = !singleton;
    $("pane-standard").hidden = singleton;
    // Brawl and Commander rank different card pools, so the list has to be redrawn.
    if (singleton && state.format !== previous) loadCommanders();
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

$("bracket-picker").addEventListener("click", (e) => {
  const btn = e.target.closest(".bracket-btn");
  if (!btn) return;
  state.targetBracket = btn.dataset.bracket ? Number(btn.dataset.bracket) : null;
  $("bracket-picker").querySelectorAll(".bracket-btn").forEach((b) => {
    const on = b === btn;
    b.classList.toggle("is-on", on);
    b.setAttribute("aria-pressed", String(on));
  });
  showTargetHint();
});

function showTargetHint() {
  $("target-hint").textContent = TARGET_HINTS[state.targetBracket ?? ""];
}
showTargetHint();

// ── Commander candidates ────────────────────────────────────────────────────

async function loadCommanders() {
  if (!state.collectionId) return;
  const fmt = SINGLETON.has(state.format) ? state.format : "commander";
  const list = $("commander-list");
  const edhrec = $("opt-edhrec").checked;
  list.innerHTML = '<p class="pane-note">Scoring commanders…</p>';

  try {
    const res = await fetch(
      `/api/collection/${state.collectionId}/commanders?limit=30&edhrec=${edhrec}&fmt=${fmt}`
    );
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "Could not score commanders.");
    // A slower request for a tab the user has since left must not overwrite it.
    if (SINGLETON.has(state.format) && state.format !== fmt) return;
    list.replaceChildren();
    if (!data.commanders.length) {
      list.innerHTML = '<p class="pane-note">No legendary creatures legal in this format. The constructed tabs still work.</p>';
      state.commander = null;
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

  const commanderMode = SINGLETON.has(state.format);
  const url = `/api/collection/${state.collectionId}/deck/${commanderMode ? "commander" : "standard"}`;
  const payload = commanderMode
    ? { commander_name: state.commander, fmt: state.format,
        target_bracket: state.targetBracket }
    : { colors: [...state.colors].join("") || null, fmt: state.format };

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
    $("precon").hidden = true;
  }
}

// ── Precons ─────────────────────────────────────────────────────────────────

async function loadPrecons() {
  const panel = $("precon-panel");
  const list = $("precon-list");
  panel.hidden = false;
  list.replaceChildren();
  $("precon-deck-cards-wrap").hidden = true;
  $("precon-note").textContent = "Checking the collection against EDHREC's precon lists…";
  state.precon = null;

  try {
    const res = await fetch(`/api/collection/${state.collectionId}/precons`);
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "Precon lookup failed.");
    if (!data.precons.length) {
      // No precon, no panel: it would only say so.
      panel.hidden = true;
      return;
    }
    state.preconList = data.precons;
    $("precon-note").textContent = data.precons.length === 1
      ? "One preconstructed deck is in this collection. Pick it to see what you own that beats what it runs."
      : `${data.precons.length} preconstructed decks are in this collection. Pick one to see what you own that beats what it runs.`;
    data.precons.forEach((precon) => {
      const btn = document.createElement("button");
      btn.className = "cmd precon-btn";
      btn.dataset.slug = precon.slug;
      btn.innerHTML = `<span class="cmd-name"></span>
        <span class="cmd-meta"><span class="cmd-score">${Math.round(precon.coverage * 100)}%</span></span>
        <span class="precon-sub"></span>`;
      btn.querySelector(".cmd-name").textContent = precon.name;
      btn.querySelector(".precon-sub").textContent = precon.binder
        ? `${precon.series} · binder “${precon.binder}”`
        : precon.series;
      btn.title = `${Math.round(precon.coverage * 100)}% of the list is in the collection`
        + (precon.missing.length ? `\nNot found: ${precon.missing.join(", ")}` : "");
      btn.addEventListener("click", () => {
        list.querySelectorAll(".cmd").forEach((c) => c.classList.toggle("is-on", c === btn));
        state.pairing = "";
        loadPreconSwaps(precon.slug);
      });
      list.append(btn);
    });
    $("precon-deck-cards-wrap").hidden = data.precons.length < 2;
  } catch (err) {
    $("precon-note").textContent = err.message;
  }
}

$("opt-precon-deck-cards").addEventListener("change", () => {
  if (state.precon) loadPreconSwaps(state.precon.precon.slug);
});

async function loadPreconSwaps(slug) {
  showError("build-error", "");
  setWorking(true, "Reading the precon and scoring your cards against it…");
  const include = $("opt-precon-deck-cards").checked;
  const pairing = state.pairing ? `&commanders=${encodeURIComponent(state.pairing)}` : "";
  try {
    const res = await fetch(
      `/api/collection/${state.collectionId}/precons/${encodeURIComponent(slug)}/swaps?include_deck_cards=${include}${pairing}`
    );
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "Could not score the precon.");
    state.precon = data;
    renderPrecon(data);
  } catch (err) {
    showError("build-error", err.message);
    $("empty").hidden = false;
  } finally {
    setWorking(false);
  }
}

function renderPrecon(data) {
  $("empty").hidden = true;
  $("deck").hidden = true;
  const article = $("precon");
  article.hidden = false;
  article.classList.remove("enter");
  void article.offsetWidth;
  article.classList.add("enter");

  $("precon-kicker").textContent = `${data.precon.series} · precon`;
  $("precon-name").textContent = data.precon.name;
  const commanders = data.commanders.map((c) => c.name).join(" // ");
  $("precon-type").textContent = data.precon.binder
    ? `${commanders} · ${data.deck_size} cards in binder “${data.precon.binder}”`
    : `${commanders} · stock list`;
  if (data.commanders[0]) attachPreview($("precon-name"), data.commanders[0].image_url);
  $("precon-identity").innerHTML = pips(data.colors);
  renderPairings(data);

  const themes = $("precon-themes");
  themes.replaceChildren();
  if (!data.themes.length) {
    themes.innerHTML = '<span class="curve-label">No recurring mechanic stands out.</span>';
  }
  data.themes.forEach((t) => {
    const chip = document.createElement("span");
    chip.className = "theme-chip";
    chip.innerHTML = `<b></b><i>${Math.round(t.share * 100)}%</i>`;
    chip.querySelector("b").textContent = t.label;
    chip.title = `${t.label}: in ${Math.round(t.share * 100)}% of the deck's rules text`;
    themes.append(chip);
  });

  const evalBox = $("precon-eval");
  evalBox.replaceChildren();
  [["Stock", data.before], ["After swaps", data.after]].forEach(([label, ev]) => {
    const cell = document.createElement("div");
    cell.innerHTML = `<dt></dt><dd><span class="eval-bracket"></span><span class="eval-crispi"></span></dd>`;
    cell.querySelector("dt").textContent = label;
    cell.querySelector(".eval-bracket").textContent = `Bracket ${ev.bracket} · ${ev.bracket_name}`;
    cell.querySelector(".eval-crispi").textContent = `CRISPI ${ev.crispi.toFixed(2)}`;
    cell.title = `${ev.line}\n${ev.crispi_summary}`;
    evalBox.append(cell);
  });

  $("swap-count").textContent = data.swaps.length ? `(${data.swaps.length})` : "";
  $("swap-note").textContent = data.swaps.length
    ? "Out of the deck on the left, in from your collection on the right. Cuts follow EDHREC's most-cut list for this precon; each card you own is scored on EDHREC's upgrade data, the commander's page, the deck's own mechanics and the roles it is short on."
    : "Nothing you own clears the bar for this deck — what it runs is already the best fit in the collection.";

  const list = $("swap-list");
  list.replaceChildren();
  data.swaps.forEach((swap) => {
    const li = document.createElement("li");
    li.className = "swap enter";
    li.innerHTML = `
      <div class="swap-card swap-out"><span class="swap-tag">out</span><span class="swap-name"></span><span class="swap-cmc">${swap.out.mana_cost || ""}</span></div>
      <span class="swap-arrow">→</span>
      <div class="swap-card swap-in"><span class="swap-tag">in</span><span class="swap-name"></span><span class="swap-cmc">${swap.in.mana_cost || ""}</span></div>
      <ul class="swap-reasons"></ul>`;
    const out = li.querySelector(".swap-out");
    const inn = li.querySelector(".swap-in");
    out.querySelector(".swap-name").textContent = swap.out.name;
    inn.querySelector(".swap-name").textContent = swap.in.name;
    out.title = swap.out_reason ? `${swap.out.type_line}\n${swap.out_reason}` : swap.out.type_line;
    inn.title = `${swap.in.type_line} · score ${swap.score}`
      + (swap.binders.length ? ` · in binder ${swap.binders.join(", ")}` : "");
    attachPreview(out, swap.out.image_url);
    attachPreview(inn, swap.in.image_url);
    const reasons = li.querySelector(".swap-reasons");
    swap.reasons.slice(0, 3).forEach((text) => {
      const r = document.createElement("li");
      r.textContent = text;
      reasons.append(r);
    });
    list.append(li);
  });

  const more = $("swap-more");
  more.hidden = !data.more.length;
  const moreList = $("swap-more-list");
  moreList.replaceChildren();
  data.more.forEach((card) => {
    const row = document.createElement("div");
    row.className = "rec";
    row.innerHTML = `<span class="rec-role">${card.score}</span><span class="rec-name"></span><span class="rec-price">${card.mana_cost || ""}</span>`;
    row.querySelector(".rec-name").textContent = card.name;
    row.title = `${card.type_line}\n${card.reasons.join("\n")}`;
    attachPreview(row, card.image_url);
    moreList.append(row);
  });

  renderBuylist(data);

  const locked = $("swap-locked");
  locked.hidden = !data.locked;
  locked.textContent = data.locked
    ? `${data.locked} candidate${data.locked === 1 ? "" : "s"} left alone because ${data.locked === 1 ? "it sits" : "they sit"} in another of your precons — tick the box on the left to consider them.`
    : "";

  article.scrollIntoView({ behavior: "smooth", block: "start" });
}

// EDHREC's popular pairings for the precon, most played first. The default
// option is whatever EDHREC files the precon under; a partner precon is
// usually played as a pair that is not that.
function renderPairings(data) {
  const wrap = $("precon-pairing-wrap");
  const select = $("precon-pairing");
  const pairings = data.precon.pairings || [];
  const current = data.commanders.map((c) => c.name).join(" // ");
  wrap.hidden = pairings.length < 2;
  if (wrap.hidden) return;
  select.replaceChildren();
  const seen = new Set();
  const options = [{ commanders: data.precon.commanders, decks: null }, ...pairings];
  options.forEach((p) => {
    const value = p.commanders.join(" // ");
    if (seen.has(value)) return;
    seen.add(value);
    const opt = document.createElement("option");
    opt.value = value;
    opt.textContent = p.decks ? `${value} · ${p.decks.toLocaleString()} decks` : `${value} · EDHREC default`;
    if (value === current) opt.selected = true;
    select.append(opt);
  });
}

$("precon-pairing").addEventListener("change", () => {
  if (!state.precon) return;
  state.pairing = $("precon-pairing").value;
  loadPreconSwaps(state.precon.precon.slug);
});

// One column per price tier, most-demanded first, so a budget reads top to
// bottom and a wallet reads left to right.
function renderBuylist(data) {
  const section = $("buylist");
  const grid = $("buylist-grid");
  grid.replaceChildren();
  const acquire = data.acquire || [];
  section.hidden = !acquire.length;
  if (!acquire.length) return;
  data.tiers.forEach((tier) => {
    const cards = acquire.filter((a) => a.tier === tier).slice(0, 10);
    if (!cards.length) return;
    const col = document.createElement("section");
    col.className = "rec-col is-buy enter";
    const total = cards.reduce((sum, a) => sum + a.price, 0);
    col.innerHTML = `<div class="rec-head"><h4></h4><p></p></div>`;
    col.querySelector("h4").textContent = `${tier} (${cards.length})`;
    col.querySelector("p").textContent = `${money(total)} for all ${cards.length}`;
    cards.forEach((a) => {
      const row = document.createElement("div");
      row.className = "rec";
      const pct = Math.round(a.added * 100);
      row.innerHTML = `<span class="rec-role">${a.game_changer ? "GC" : ""}</span>
        <span class="rec-name"></span>
        <span class="rec-price">${money(a.price)}</span>
        <span class="rec-bar"><i><span style="width:${Math.min(pct, 100)}%"></span></i>${Math.round(a.run * 100)}%</span>`;
      row.querySelector(".rec-name").textContent = a.name;
      row.title = `${a.type_line} · ${money(a.price)}\nAdded to ${pct}% of upgraded lists · run in ${Math.round(a.run * 100)}% of the commander's decks · synergy ${a.synergy >= 0 ? "+" : ""}${a.synergy.toFixed(2)}`
        + (a.game_changer ? "\nGame Changer — moves the deck to bracket 3" : "");
      attachPreview(row, a.image_url);
      col.append(row);
    });
    grid.append(col);
  });
}

$("copy-buylist").addEventListener("click", async () => {
  if (!state.precon || !state.precon.acquire) return;
  const lines = state.precon.tiers.flatMap((tier) => {
    const cards = state.precon.acquire.filter((a) => a.tier === tier).slice(0, 10);
    return cards.length ? [`# ${tier}`, ...cards.map((a) => `1 ${a.name}  (${money(a.price)})`), ""] : [];
  });
  await navigator.clipboard.writeText(lines.join("\n"));
  flash($("copy-buylist"), "Copied");
});

$("copy-precon-after").addEventListener("click", async () => {
  if (!state.precon) return;
  await navigator.clipboard.writeText(state.precon.decklist_after);
  flash($("copy-precon-after"), "Copied");
});

$("download-precon-after").addEventListener("click", () => {
  if (!state.precon) return;
  const blob = new Blob([state.precon.decklist_after], { type: "text/plain" });
  const link = document.createElement("a");
  link.href = URL.createObjectURL(blob);
  link.download = `${state.precon.precon.slug}-after-swaps.txt`;
  link.click();
  URL.revokeObjectURL(link.href);
});

// ── Deck rendering ──────────────────────────────────────────────────────────

function renderDeck(deck) {
  $("empty").hidden = true;
  $("precon").hidden = true;
  $("deck").hidden = false;
  $("deck").classList.remove("enter");
  void $("deck").offsetWidth;
  $("deck").classList.add("enter");

  if (deck.commander) {
    $("deck-kicker").textContent = `${deck.format_label} · ${deck.archetype}`;
    $("deck-name").textContent = deck.commander.name;
    $("deck-type").textContent = deck.commander.type_line;
    attachPreview($("deck-name"), deck.commander.image_url);
  } else {
    $("deck-kicker").textContent = deck.format_label;
    $("deck-name").textContent = deck.archetype;
    $("deck-type").textContent =
      `${deck.total} cards, ${deck.format_label} legal, built from what you own`;
  }
  $("deck-identity").innerHTML = pips(deck.colors);

  const nonland = deck.curve.reduce((a, b) => a + b, 0);
  const weighted = deck.curve.reduce((sum, n, cmc) => sum + n * cmc, 0);
  const lands = (deck.categories.find((c) => c.name === "Lands") || { count: 0 }).count;
  $("deck-count").textContent = deck.total;
  $("deck-avg").textContent = nonland ? (weighted / nonland).toFixed(2) : "0";
  $("deck-lands").textContent = lands;
  $("deck-price-wrap").hidden = !deck.price;
  if (deck.price) $("deck-price").textContent = money(deck.price);

  renderCurve(deck.curve);
  renderPipMeter($("deck-pips"), deck.pips);
  // Shown straight away from the local criteria, then refined once the combo
  // lookup lands — the panel never blocks the deck.
  renderBracket(deck.bracket, { pending: !!deck.bracket });
  renderSynergy(deck.synergy);
  renderColumns($("columns"), deck.categories);
  renderSideboard(deck);

  $("plain").textContent = deck.pretty;
  $("plain").hidden = true;
  $("toggle-text").textContent = "Show plain text";

  $("deck").scrollIntoView({ behavior: "smooth", block: "start" });

  // Recommendations need a round trip to EDHREC, so they land after the deck.
  if (deck.commander) {
    loadRecommendations(deck.commander.name);
    if (deck.bracket) loadBracket(deck.commander.name, deck.format);
  } else {
    $("recs").hidden = true;
  }
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
      "EDHREC has nothing to add here.",
      data.acquire_price ? `${money(data.acquire_price)} for all ${data.acquire.length}` : "");
  } catch (err) {
    $("recs-note").textContent = err.message;
  }
}

function renderRecColumn(title, variant, recs, emptyText, footNote = "") {
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
    // Role first: it says at a glance whether this is a card the archetype
    // expects or one a minority of pilots chose.
    row.innerHTML = `<span class="rec-role" data-role="${rec.role}">${rec.role}</span>
      <span class="rec-name"></span>
      <span class="rec-price">${rec.price ? money(rec.price) : ""}</span>
      <span class="rec-bar"><i><span style="width:${Math.min(pct, 100)}%"></span></i>${pct}%</span>`;
    row.querySelector(".rec-name").textContent = rec.name;
    row.title = `${rec.type_line || rec.category} — ${rec.role.toLowerCase()}, in ${pct}% of ${rec.num_decks.toLocaleString()} decks, synergy ${rec.synergy >= 0 ? "+" : ""}${rec.synergy.toFixed(2)}${rec.price ? ` · ${money(rec.price)}` : ""}`;
    attachPreview(row, rec.image_url);
    col.append(row);
  });

  if (footNote) {
    const foot = document.createElement("p");
    foot.className = "rec-foot";
    foot.textContent = footNote;
    col.append(foot);
  }

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

// ── CRISPI ──────────────────────────────────────────────────────────────────

// Order follows the acronym, not the payload: Consistency, Resilience,
// Interaction, Speed.
const CRISPI_ROWS = [
  ["consistency", "Consistency"],
  ["resilience", "Resilience"],
  ["interaction", "Interaction"],
  ["speed", "Speed"],
];

function renderCrispi(crispi) {
  const box = $("crispi");
  box.hidden = !crispi;
  if (!crispi) return;

  $("crispi-score").textContent = crispi.score.toFixed(2);

  const rows = $("crispi-rows");
  rows.replaceChildren();
  CRISPI_ROWS.forEach(([key, label]) => {
    const attr = crispi.attributes[key];
    const li = document.createElement("li");
    li.className = "crispi-row";
    if (attr.estimated) li.classList.add("is-estimated");
    // The bar is the score on a 1-10 scale; the title carries the working.
    li.innerHTML = `<span class="crispi-label"></span>
      <span class="crispi-bar"><i style="width:${attr.score * 10}%"></i></span>
      <span class="crispi-value"></span>`;
    li.querySelector(".crispi-label").textContent = label;
    li.querySelector(".crispi-value").textContent =
      attr.score.toFixed(2) + (attr.estimated ? "~" : "");
    li.title = attr.detail;
    rows.append(li);
  });

  // Never let the estimated attributes pass as counted ones.
  const est = crispi.estimated || [];
  $("crispi-foot").textContent = est.length
    ? `~ ${est.join(" and ")} are estimated, not counted — a deck's ` +
      `fundamental turn needs hands played out, which this can't do.`
    : "";
}

// ── Commander bracket ───────────────────────────────────────────────────────

const BRACKET_STEPS = [
  { n: 1, name: "Exhibition" },
  { n: 2, name: "Core" },
  { n: 3, name: "Upgraded" },
  { n: 4, name: "Optimized" },
  { n: 5, name: "cEDH" },
];

function renderBracket(bracket, { pending = false } = {}) {
  const section = $("bracket");
  if (!bracket) {
    section.hidden = true;
    return;
  }
  section.hidden = false;
  section.classList.toggle("is-pending", pending);

  $("bracket-headline").textContent = bracket.headline;
  $("bracket-blurb").textContent = bracket.blurb;

  // Only shown when the deck was built for a bracket, and it says plainly
  // whether it got there — a target that quietly missed would be worse than
  // no target at all.
  const target = $("bracket-target");
  target.hidden = !bracket.target;
  if (bracket.target) {
    target.textContent = bracket.target.line;
    target.dataset.met = String(bracket.target.met);
  }

  // Bracket 5 is drawn but never lit: it is a statement of intent, not a
  // property of the list, so the backend never assigns it.
  const scale = $("bracket-scale");
  scale.replaceChildren();
  BRACKET_STEPS.forEach((step) => {
    const cell = document.createElement("div");
    cell.className = "bracket-step";
    if (step.n === bracket.number) cell.classList.add("is-current");
    if (step.n === 5) cell.classList.add("is-unreachable");
    cell.innerHTML = `<span class="bracket-step-n">${step.n}</span>
      <span class="bracket-step-name"></span>`;
    cell.querySelector(".bracket-step-name").textContent = step.name;
    cell.title = step.n === 5
      ? "cEDH is about intent — it can't be read off a decklist"
      : step.name;
    scale.append(cell);
  });

  const list = $("bracket-criteria");
  list.replaceChildren();
  bracket.criteria.forEach((crit) => {
    const li = document.createElement("li");
    li.className = "bracket-crit";
    if (crit.unchecked) li.classList.add("is-unchecked");
    else if (crit.count) li.classList.add("is-hit");
    li.innerHTML = `<span class="bracket-crit-label"></span>
      <span class="bracket-crit-count">${crit.unchecked ? "?" : crit.count}</span>
      <span class="bracket-crit-note"></span>`;
    li.querySelector(".bracket-crit-label").textContent = crit.label;
    li.querySelector(".bracket-crit-note").textContent = crit.note;
    // The cards behind the count are the actionable part — swap one out and
    // the bracket moves.
    if (crit.cards && crit.cards.length) {
      const named = document.createElement("span");
      named.className = "bracket-crit-cards";
      named.textContent = crit.cards.join(", ");
      li.append(named);
    }
    list.append(li);
  });

  renderCrispi(bracket.crispi);

  const notes = $("bracket-notes");
  const text = (bracket.notes || []).join(" ");
  notes.hidden = !text;
  notes.textContent = text;
}

async function loadBracket(commanderName, fmt) {
  const deckBracket = $("bracket");
  if (deckBracket.hidden) return;
  try {
    const res = await fetch(
      `/api/collection/${state.collectionId}/bracket?commander=${encodeURIComponent(commanderName)}` +
      `&fmt=${encodeURIComponent(fmt)}`
    );
    if (!res.ok) return; // keep the combo-free estimate already on screen
    renderBracket(await res.json());
  } catch {
    // Offline: the local estimate stands, and says combos went unchecked.
  }
}

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

  // Bars show how hard each theme pulled on selection, not how many cards it
  // touched — so the trailing card count can disagree with the bar. The title
  // spells both out.
  const themes = $("synergy-themes");
  themes.replaceChildren();
  const peak = Math.max(...syn.themes.map((t) => t.weight), 1);
  syn.themes.forEach((theme) => {
    const item = document.createElement("div");
    item.className = "syn-theme";
    item.innerHTML = `<span class="syn-theme-name"></span>
      <span class="syn-bar"><i style="width:${Math.round((theme.weight / peak) * 100)}%"></i></span>
      <span class="syn-theme-count">${theme.count}</span>`;
    item.querySelector(".syn-theme-name").textContent = theme.label;
    item.title = syn.kind === "commander"
      ? `${theme.count} cards · ${theme.weight} synergy points`
      : `${theme.count} copies`;
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

function renderSideboard(deck) {
  const section = $("sideboard");
  if (!deck.sideboard || !deck.sideboard.length) {
    section.hidden = true;
    return;
  }
  section.hidden = false;
  $("sideboard-count").textContent = `(${deck.sideboard_total})`;
  renderColumns($("sideboard-cards"), [
    { name: "Sideboard", count: deck.sideboard_total, cards: deck.sideboard },
  ]);
}

function renderColumns(el, categories) {
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
      const priced = card.price ? `${card.type_line} · ${money(card.price)}` : card.type_line;
      row.title = card.is_filler
        ? `${card.type_line} — added as filler, not from your collection`
        : why
          ? `${priced}\nSynergy ${card.synergy.toFixed(1)} — ${why}`
          : priced;
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
  const name = (state.deck.commander ? state.deck.commander.name : state.deck.archetype)
    .replace(/[^a-z0-9]+/gi, "_")
    .toLowerCase();
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
