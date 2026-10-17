// KNX-Gruppenmonitor: Live-Kacheln, Bus-Netz, Bedienen, Funktionstest, Aufnahme & Wiedergabe.
// Nutzt aus app.js: escapeHtml, monitorRunning, busConnected, monitorTelegrams; Ereignisse knx-telegram / knx-project / knx-connection.
(() => {
  const $ = (id) => document.getElementById(id);
  const esc = (value) => escapeHtml(value ?? "");
  const nf = new Intl.NumberFormat("de-CH", {maximumFractionDigits: 2});

  const LIGHT = /licht|lampe|leuchte|\bled\b|spot|beleuchtung|downlight|pendel|deckenl|wandl|stehl|light|lamp/i;
  const BLIND = /jalou|rollla|rolll|storen|store\b|beschatt|raff|markise|lamelle|blind|shutter|rollo|screen/i;
  const STATUS = /status|rückmeld|rueckmeld|\brm\b|zustand|feedback|istwert|ist-wert/i;
  const DPT_OPTIONS = [
    ["1.001", "Schalten (Ein/Aus)"], ["1.008", "Auf/Ab"], ["1.009", "Öffnen/Schliessen"], ["1.019", "Fenster/Tür"],
    ["3.007", "Dimmen relativ"], ["5.001", "Prozent 0–100 %"], ["5.003", "Winkel"], ["5.010", "Zähler 1 Byte"],
    ["7.001", "Zähler 2 Byte"], ["9.001", "Temperatur °C"], ["9.004", "Helligkeit lx"], ["9.005", "Wind m/s"],
    ["9.007", "Feuchte %"], ["9.008", "CO₂ ppm"], ["12.001", "Zähler 4 Byte"], ["13.010", "Energie Wh"],
    ["13.013", "Energie kWh"], ["14.056", "Leistung W"], ["16.001", "Text"], ["17.001", "Szene"],
    ["18.001", "Szenensteuerung"], ["20.102", "HVAC-Modus"], ["232.600", "RGB-Farbe"],
  ];
  const HVAC = {auto: "Auto", comfort: "Komfort", standby: "Standby", night: "Nacht", economy: "Nacht",
    frost_protection: "Frostschutz", building_protection: "Gebäudeschutz"};
  const KIND_LABELS = {light: "Licht", level: "Licht", dim: "Licht", blind: "Beschattung", updown: "Beschattung",
    angle: "Beschattung", blindstep: "Beschattung", temp: "Klima", hvac: "Klima", humidity: "Klima", air: "Klima",
    lux: "Wetter & Messwerte", wind: "Wetter & Messwerte", power: "Energie", number: "Wetter & Messwerte",
    switch: "Schalten", window: "Fenster & Kontakte", openclose: "Fenster & Kontakte", scene: "Szenen",
    rgb: "Licht", text: "Sonstiges", time: "Sonstiges", raw: "Unbekannter Datentyp"};
  const CONTROLLABLE = new Set(["light", "level", "dim", "blind", "updown", "angle", "blindstep", "switch", "scene",
    "hvac", "rgb", "openclose", "temp", "number", "text", "raw"]);

  const gm = {
    groups: new Map(), ranges: {}, pairs: [], devices: new Map(), projectName: "",
    sendMode: {enabled: false, remaining_s: 0}, tab: "tiles", total: 0,
    stamps: [], rate: Array(60).fill(0), sources: new Map(), renderTimer: null,
    recording: null, recordings: [], player: null, tests: [], testStop: false, testRunning: false,
    secure: new Set(), undecoded: 0,
  };

  // --- Hilfen --------------------------------------------------------------------------
  async function api(path, options = {}) {
    const response = await fetch(path, {headers: {"Content-Type": "application/json"}, ...options});
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      const error = new Error(data.detail || `HTTP ${response.status}`);
      error.status = response.status;
      throw error;
    }
    return data;
  }
  const dptMain = (dpt) => Number(String(dpt || "").match(/(\d+)/)?.[1]) || null;
  const dptSub = (dpt) => Number(String(dpt || "").match(/\d+[.-](\d+)/)?.[1]) || null;
  const gaParts = (address) => String(address).split("/");
  const isStatusName = (group) => STATUS.test(`${group.name || ""} ${gm.ranges[gaParts(group.address).slice(0, 2).join("/")] || ""}`);
  function since(time) {
    if (!time) return "";
    const seconds = Math.max(0, Math.round((Date.now() - new Date(time).getTime()) / 1000));
    if (seconds < 5) return "gerade eben";
    if (seconds < 60) return `vor ${seconds} s`;
    if (seconds < 3600) return `vor ${Math.round(seconds / 60)} min`;
    return `vor ${Math.round(seconds / 3600)} h`;
  }

  function kindFor(group) {
    const main = dptMain(group.dpt);
    const sub = dptSub(group.dpt);
    const parts = gaParts(group.address);
    const name = `${group.name || ""} ${gm.ranges[parts.slice(0, 2).join("/")] || ""} ${gm.ranges[parts[0]] || ""}`;
    if (main === 1) {
      if (sub === 8) return "updown";
      if (sub === 7) return "blindstep";
      if (sub === 9) return "openclose";
      if (sub === 19) return "window";
      return LIGHT.test(name) ? "light" : BLIND.test(name) ? "updown" : "switch";
    }
    if (main === 3) return sub === 8 ? "blindstep" : "dim";
    if (main === 5) {
      if (sub === 3) return "angle";
      if (sub === 1 || sub === 4) return BLIND.test(name) ? "blind" : "level";
      return "number";
    }
    if (main === 9) return {1: "temp", 4: "lux", 5: "wind", 7: "humidity", 8: "air"}[sub] || "number";
    if (main === 14 && sub === 56) return "power";
    if ([6, 7, 8, 12, 13, 14, 29].includes(main)) return "number";
    if (main === 16) return "text";
    if ([10, 11, 19].includes(main)) return "time";
    if (main === 17 || main === 18) return "scene";
    if (main === 20) return "hvac";
    if (main === 232 || main === 251) return "rgb";
    return "raw";
  }

  function valueText(group, value = group.value) {
    if ((value === null || value === undefined) && group.locked) return "🔒 Schlüssel fehlt";
    if (value === null || value === undefined) return group.hex ? `Roh ${group.hex}` : "–";
    const sub = dptSub(group.dpt);
    if (typeof value === "boolean") {
      if (group.kind === "updown") return value ? "Ab ▼" : "Auf ▲";
      if (group.kind === "blindstep") return value ? "Schritt ▼" : "Schritt ▲";
      if (group.kind === "openclose") return value ? "Geschlossen" : "Offen";
      if (group.kind === "window") return value ? "Offen" : "Geschlossen";
      if (dptMain(group.dpt) === 1 && sub === 3) return value ? "Freigegeben" : "Gesperrt";
      return value ? "Ein" : "Aus";
    }
    if (typeof value === "number") return `${nf.format(value)}${group.unit ? ` ${group.unit}` : ""}`;
    if (typeof value === "string") return HVAC[value] || value;
    if (group.kind === "dim" && typeof value === "object") return value.step_code === 0 ? "Stopp" : value.control ? "Heller ▲" : "Dunkler ▼";
    if (typeof value === "object" && "red" in value) return rgbHex(value);
    if (typeof value === "object" && "hour" in value) return [value.hour, value.minutes, value.seconds].map((n) => String(n ?? 0).padStart(2, "0")).join(":");
    if (typeof value === "object" && "day" in value && "month" in value) return `${value.day}.${value.month}.${value.year}`;
    if (typeof value === "object" && "scene_number" in value) return `Szene ${value.scene_number}${value.learn ? " lernen" : ""}`;
    return JSON.stringify(value);
  }
  const rgbHex = (value) => `#${[value.red, value.green, value.blue].map((n) => Number(n || 0).toString(16).padStart(2, "0")).join("")}`;

  function ensureGroup(address, data = {}) {
    let group = gm.groups.get(address);
    const isNew = !group;
    if (!group) {
      group = {address, name: null, dpt: null, unit: null, value: null, hex: "", count: 0, last: null, source: null, history: []};
      gm.groups.set(address, group);
    }
    if (data.name !== undefined && data.name !== null) group.name = data.name;
    if (data.dpt && !(data.dpt_guessed && group.dpt && !group.dptGuessed)) {
      group.dpt = data.dpt;
      group.dptGuessed = Boolean(data.dpt_guessed);
    }
    if (data.dpt_source) group.dptSource = data.dpt_source;
    if (data.unit) group.unit = data.unit;
    if (!group.unit && data.info?.unit) group.unit = data.info.unit;
    group.kind = kindFor(group);
    group.status = isStatusName(group);
    if (isNew) scheduleRender();
    return group;
  }

  // --- Telegramme verarbeiten -------------------------------------------------------------
  function processTelegram(item, {replay = false} = {}) {
    if (!item || !item.destination) return;
    if (!replay) {
      gm.total += 1;
      const now = Date.now();
      gm.stamps.push(now);
      const source = item.direction === "Outgoing" ? "IBS" : item.source;
      const entry = gm.sources.get(source) || {count: 0};
      entry.count += 1;
      gm.sources.set(source, entry);
      if (gm.recording) gm.recording.telegrams.push({...item, t: now - gm.recording.started});
    }
    const group = ensureGroup(item.destination, {name: item.group_name, dpt: item.dpt, unit: item.unit, dpt_guessed: item.dpt_guessed});
    if (item.secure) { group.secure = true; gm.secure.add(group.address); }
    if (item.undecoded) {
      group.locked = true;
      gm.undecoded += 1;
      showSecureHint();
    } else if (item.secure) {
      group.locked = false;
    }
    group.count += 1;
    group.last = item.time || new Date().toISOString();
    group.source = item.direction === "Outgoing" ? "IBS Scanner" : item.source;
    if (item.service === "GroupValueWrite" || item.service === "GroupValueResponse") {
      if (item.value_json !== undefined && item.value_json !== null) group.value = item.value_json;
      else if (item.value && group.kind === "raw") group.value = null;
      group.hex = item.hex || group.hex;
      group.history.unshift({time: group.last, text: valueText(group), service: item.service, source: group.source});
      group.history.length = Math.min(group.history.length, 12);
    }
    updateTile(group, item);
    addTicker(group, item, replay);
    netTelegram(item, group);
    if ($("gm-control").open && $("gm-control").dataset.ga === group.address) fillControlHistory(group);
  }

  window.addEventListener("knx-telegram", (event) => processTelegram(event.detail));
  window.addEventListener("knx-project", (event) => {
    gm.projectName = event.detail?.name || "";
    gm.devices = new Map((event.detail?.devices || []).map((device) => [device.address, device.name]));
    loadGroups();
  });
  window.addEventListener("knx-connection", (event) => {
    refreshSendMode();
    if (event.detail?.status?.data_secure) applyDataSecure(event.detail.status.data_secure);
    else refreshDataSecure();
  });

  async function loadGroups() {
    try {
      const data = await api("/api/monitor/groups");
      gm.ranges = data.ranges || {};
      gm.pairs = data.pairs || [];
      gm.secure = new Set(data.secure_groups || []);
      (data.groups || []).forEach((row) => { const group = ensureGroup(row.address, row); group.secure = Boolean(row.secure) || gm.secure.has(row.address); });
      gm.secure.forEach((address) => { if (gm.groups.has(address)) gm.groups.get(address).secure = true; });
      applyDataSecure(data.data_secure);
      Object.entries(data.state || {}).forEach(([address, state]) => {
        const group = ensureGroup(address, {dpt: state.dpt, unit: state.unit, dpt_guessed: state.dpt_guessed});
        group.value = state.value_json ?? group.value;
        group.hex = state.hex || group.hex;
        group.last = state.time;
        group.source = state.source;
      });
      gm.groups.forEach((group) => { group.kind = kindFor(group); group.status = isStatusName(group); });
      applySendMode(data.send_mode);
      fillDatalists();
      buildTestsFromPairs();
      await restoreDptOverrides();
      renderTiles();
    } catch { /* ohne Server-Antwort bleibt die bisherige Ansicht */ }
  }

  // --- Live-Kacheln -------------------------------------------------------------------
  function visual(group) {
    const kind = group.kind;
    const value = group.value;
    const level = typeof value === "number" ? Math.max(0, Math.min(100, value)) : 0;
    switch (kind) {
      case "light": case "switch":
        return kind === "light" ? '<i class="gm-bulb"></i>' : '<i class="gm-toggle"><b></b></i>';
      case "level": case "dim":
        return `<i class="gm-bulb" style="--level:${kind === "level" ? level / 100 : 0.5}"></i><i class="gm-levelbar"><b style="width:${level}%"></b></i>`;
      case "blind":
        return `<i class="gm-window"><b style="height:${level}%"></b></i>`;
      case "updown": case "blindstep":
        return `<i class="gm-window gm-moving"><b style="height:${value === true ? 80 : 20}%"></b><em>${value === true ? "▼" : value === false ? "▲" : "↕"}</em></i>`;
      case "angle":
        return `<i class="gm-slat" style="--angle:${typeof value === "number" ? value / 2 : 0}deg"><b></b><b></b><b></b></i>`;
      case "temp": {
        const t = typeof value === "number" ? value : 20;
        const fill = Math.max(5, Math.min(100, ((t - 10) / 20) * 100));
        const hue = Math.max(0, Math.min(220, 220 - ((t - 14) / 12) * 220));
        return `<i class="gm-thermo" style="--fill:${fill}%;--hue:${hue}"><b></b></i>`;
      }
      case "humidity": return `<i class="gm-drop" style="--fill:${level}%"><b></b></i>`;
      case "air": {
        const ppm = typeof value === "number" ? value : 400;
        const hue = ppm < 800 ? 140 : ppm < 1200 ? 45 : 0;
        return `<i class="gm-ring" style="--hue:${hue}"></i>`;
      }
      case "lux": return `<i class="gm-sun" style="--level:${typeof value === "number" ? Math.min(1, Math.log10(value + 1) / 5) : 0.2}"></i>`;
      case "wind": return `<i class="gm-fan" style="--speed:${typeof value === "number" && value > 0 ? Math.max(0.25, 3 / value) : 0}s"></i>`;
      case "power": return '<i class="gm-bolt">ϟ</i>';
      case "rgb": return `<i class="gm-swatch" style="--color:${value && typeof value === "object" ? rgbHex(value) : "#3a4658"}"></i>`;
      case "hvac": return `<i class="gm-hvac">${{comfort: "☀", standby: "◐", night: "☾", economy: "☾", frost_protection: "❄", building_protection: "❄"}[value] || "♨"}</i>`;
      case "window": case "openclose": return `<i class="gm-door">${value === (kind === "window") ? "◫" : "▯"}</i>`;
      case "scene": return '<i class="gm-scene">✦</i>';
      case "time": return '<i class="gm-clock">◷</i>';
      case "text": return '<i class="gm-text">T</i>';
      case "number": return '<i class="gm-number">#</i>';
      default: return '<i class="gm-raw">⌁</i>';
    }
  }

  function tileState(group) {
    const on = group.value === true || (typeof group.value === "number" && group.value > 0 && ["level", "dim"].includes(group.kind));
    return `gm-tile kind-${group.kind}${on ? " on" : ""}${group.status ? " is-status" : ""}${group.count ? " seen" : ""}${group.locked ? " locked" : ""}`;
  }

  function tileHtml(group) {
    return `<button class="${tileState(group)}" type="button" data-ga="${esc(group.address)}" title="${esc(group.address)} · ${esc(group.name || "ohne Namen")}${group.dpt ? ` · DPT ${esc(group.dpt)}` : ""}">
      <span class="gm-visual">${visual(group)}</span>
      <span class="gm-tile-text"><strong>${esc(group.name || group.address)}</strong><small>${group.secure ? "🔒 " : ""}${esc(group.address)}${group.dpt ? ` · ${esc(group.dpt)}${group.dptGuessed ? " vermutet" : ""}` : " · DPT ?"}${group.status ? " · Status" : ""}</small></span>
      <span class="gm-value">${esc(valueText(group))}</span>
      <span class="gm-meta" data-since="${esc(group.last || "")}">${group.count ? `${since(group.last)} · ${esc(group.source || "")} · ${group.count}×` : "noch kein Telegramm"}</span>
    </button>`;
  }

  function sectionKey(group, grouping) {
    const parts = gaParts(group.address);
    if (grouping === "kind") return {key: KIND_LABELS[group.kind] || "Sonstiges", label: KIND_LABELS[group.kind] || "Sonstiges"};
    const main = parts[0];
    const mainName = gm.ranges[main] || `Hauptgruppe ${main}`;
    if (grouping === "main" || parts.length < 3) return {key: main.padStart(3, "0"), label: `${main} · ${mainName}`};
    const middle = parts.slice(0, 2).join("/");
    return {key: `${main.padStart(3, "0")}/${parts[1].padStart(3, "0")}`, label: `${middle} · ${mainName} › ${gm.ranges[middle] || `Mittelgruppe ${parts[1]}`}`};
  }

  function gaSortKey(address) {
    return gaParts(address).map((part) => part.padStart(5, "0")).join("/");
  }

  function tileVisible(group, query, filter) {
    if (filter === "active" && !group.count) return false;
    if (filter === "control" && (group.status || !CONTROLLABLE.has(group.kind))) return false;
    if (filter === "status" && !(group.status || ["temp", "lux", "wind", "humidity", "air", "power", "number"].includes(group.kind))) return false;
    if (!query) return true;
    return `${group.address} ${group.name || ""} ${valueText(group)} ${gm.ranges[gaParts(group.address)[0]] || ""}`.toLocaleLowerCase("de-CH").includes(query);
  }

  function renderTiles() {
    clearTimeout(gm.renderTimer);
    gm.renderTimer = null;
    const container = $("gm-tile-groups");
    const query = $("gm-tile-search").value.trim().toLocaleLowerCase("de-CH");
    const filter = $("gm-tile-filter").value;
    const grouping = $("gm-tile-grouping").value;
    const sections = new Map();
    [...gm.groups.values()].sort((a, b) => gaSortKey(a.address).localeCompare(gaSortKey(b.address))).forEach((group) => {
      if (!tileVisible(group, query, filter)) return;
      const {key, label} = sectionKey(group, grouping);
      if (!sections.has(key)) sections.set(key, {label, groups: []});
      sections.get(key).groups.push(group);
    });
    const hint = $("gm-tile-hint");
    if (!gm.groups.size) {
      container.innerHTML = '<div class="gm-empty"><span>▦</span><strong>Noch keine Gruppenadressen</strong><small>ETS-Projekt in Schritt 1 laden – oder einfach mit dem Bus verbinden: Gruppenadressen erscheinen hier, sobald Telegramme kommen. Den Datentyp lässt sich danach pro Kachel festlegen.</small></div>';
      hint.textContent = "";
      return;
    }
    const shown = [...sections.values()].reduce((sum, section) => sum + section.groups.length, 0);
    hint.textContent = `${shown} von ${gm.groups.size} Gruppenadressen · Kachel anklicken zum Bedienen${gm.sendMode.enabled ? "" : " (Senden zuerst oben freigeben)"}`;
    const openState = new Map([...container.querySelectorAll("details[data-key]")].map((details) => [details.dataset.key, details.open]));
    container.innerHTML = [...sections.entries()].sort(([a], [b]) => a.localeCompare(b)).map(([key, section]) => `
      <details class="gm-section" data-key="${esc(key)}" ${openState.get(key) === false ? "" : "open"}>
        <summary><span>${esc(section.label)}</span><small>${section.groups.length}</small></summary>
        <div class="gm-grid">${section.groups.map(tileHtml).join("")}</div>
      </details>`).join("") || '<div class="gm-empty"><span>⌕</span><strong>Keine passende Gruppenadresse</strong><small>Suche oder Filter anpassen.</small></div>';
  }

  function scheduleRender() {
    if (gm.renderTimer) return;
    gm.renderTimer = setTimeout(renderTiles, 400);
  }

  function updateTile(group, item) {
    const tile = document.querySelector(`.gm-tile[data-ga="${CSS.escape(group.address)}"]`);
    if (!tile) { if ($("gm-tile-filter").value === "active") scheduleRender(); return; }
    tile.className = tileState(group);
    tile.querySelector(".gm-visual").innerHTML = visual(group);
    tile.querySelector(".gm-value").textContent = valueText(group);
    const meta = tile.querySelector(".gm-meta");
    meta.dataset.since = group.last || "";
    meta.textContent = `${since(group.last)} · ${group.source || ""} · ${group.count}×`;
    const service = item.direction === "Outgoing" ? "own" : item.service === "GroupValueRead" ? "read" : item.service === "GroupValueResponse" ? "response" : "write";
    tile.classList.remove("hit-write", "hit-read", "hit-response", "hit-own");
    void tile.offsetWidth; // Animation neu starten
    tile.classList.add(`hit-${service}`);
  }

  setInterval(() => {
    document.querySelectorAll(".gm-tile .gm-meta[data-since]").forEach((meta) => {
      if (!meta.dataset.since) return;
      meta.textContent = meta.textContent.replace(/^[^·]+·/, `${since(meta.dataset.since)} ·`);
    });
  }, 5000);

  ["gm-tile-search"].forEach((id) => $(id).addEventListener("input", scheduleRender));
  ["gm-tile-filter", "gm-tile-grouping"].forEach((id) => $(id).addEventListener("change", renderTiles));
  $("gm-tile-groups").addEventListener("click", (event) => {
    const tile = event.target.closest(".gm-tile");
    if (tile) openControl(tile.dataset.ga);
  });

  // --- Ticker & Kennzahlen --------------------------------------------------------------
  function addTicker(group, item, replay) {
    const ticker = $("gm-ticker");
    ticker.querySelector(".gm-ticker-empty")?.remove();
    const service = item.direction === "Outgoing" ? "own" : item.service === "GroupValueRead" ? "read" : item.service === "GroupValueResponse" ? "response" : "write";
    const chip = document.createElement("span");
    chip.className = `gm-chip ${service}${replay ? " replay" : ""}`;
    chip.innerHTML = `<b>${esc(group.address)}</b> ${esc(group.name || "")} <em>${item.service === "GroupValueRead" ? "? lesen" : esc(valueText(group))}</em>`;
    ticker.prepend(chip);
    while (ticker.children.length > 8) ticker.lastChild.remove();
  }

  function updateStats() {
    const now = Date.now();
    gm.stamps = gm.stamps.filter((stamp) => now - stamp < 60000);
    const lastSecond = gm.stamps.filter((stamp) => now - stamp < 1000).length;
    gm.rate.push(lastSecond);
    gm.rate.shift();
    const average = gm.stamps.filter((stamp) => now - stamp < 5000).length / 5;
    $("gm-rate").textContent = nf.format(average);
    const load = Math.min(100, Math.round(average * 2)); // ≈ 20 ms Buszeit je Telegramm auf TP (9600 Bit/s)
    $("gm-load").textContent = `${load} %`;
    $("gm-load-bar").style.width = `${load}%`;
    $("gm-load-bar").className = load > 60 ? "high" : load > 30 ? "mid" : "";
    $("gm-total").textContent = nf.format(gm.total);
    $("gm-active-groups").textContent = `${[...gm.groups.values()].filter((group) => group.count).length} aktive Gruppen`;
    const top = [...gm.sources.entries()].sort((a, b) => b[1].count - a[1].count)[0];
    $("gm-top-source").textContent = top ? (top[0] === "IBS" ? "IBS Scanner" : top[0]) : "–";
    $("gm-top-source-name").textContent = top ? `${gm.devices.get(top[0]) || (top[0] === "IBS" ? "diese Anwendung" : "")} · ${top[1].count}×` : " ";
    drawRate();
  }
  setInterval(updateStats, 1000);

  function cssVar(name, fallback) {
    return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || fallback;
  }

  function drawRate() {
    const canvas = $("gm-rate-canvas");
    const width = canvas.clientWidth || 160;
    const ratio = window.devicePixelRatio || 1;
    canvas.width = width * ratio; canvas.height = 54 * ratio;
    const ctx = canvas.getContext("2d");
    ctx.scale(ratio, ratio);
    const max = Math.max(4, ...gm.rate);
    const barWidth = width / gm.rate.length;
    gm.rate.forEach((count, index) => {
      const height = (count / max) * 46;
      const gradient = ctx.createLinearGradient(0, 54 - height, 0, 54);
      gradient.addColorStop(0, cssVar("--blue", "#49a6ff"));
      gradient.addColorStop(1, "rgba(73,166,255,.15)");
      ctx.fillStyle = gradient;
      ctx.fillRect(index * barWidth + 1, 54 - height, Math.max(1, barWidth - 2), height);
    });
  }

  // --- Reiter -----------------------------------------------------------------------------
  const TAB_HELP = {
    tiles: "Jede Gruppenadresse als Kachel. Sie leuchtet kurz auf, sobald ein Telegramm kommt. Kachel anklicken, um zu schalten, einen Wert zu senden oder zu lesen.",
    net: "Wer sendet an wen? Links die Geräte, rechts die Gruppenadressen – jedes Telegramm fliegt als Lichtpunkt über den Bus. Punkt anklicken zum Bedienen.",
    telegrams: "Alle Telegramme als Liste – wie der Gruppenmonitor der ETS. Mit Filtern eingrenzen und als CSV speichern.",
    test: "Funktionen mit Rückmeldung prüfen und das Ergebnis als Prüfprotokoll speichern – Schritt für Schritt erklärt.",
    rec: "Busverkehr aufnehmen (z. B. einen sporadischen Fehler), später abspielen oder Schreib-Telegramme erneut senden.",
  };

  function selectTab(tab) {
    gm.tab = tab;
    $("gm-tab-help").textContent = TAB_HELP[tab] || "";
    document.querySelectorAll("[data-gm-tab]").forEach((button) => button.classList.toggle("active", button.dataset.gmTab === tab));
    document.querySelectorAll("[data-gm-pane]").forEach((pane) => { pane.hidden = pane.dataset.gmPane !== tab; });
    if (tab === "net") resizeNet();
    if (tab === "telegrams" && typeof renderMonitor === "function") renderMonitor();
  }
  document.querySelectorAll("[data-gm-tab]").forEach((button) => button.addEventListener("click", () => selectTab(button.dataset.gmTab)));

  // --- Senden freigeben -------------------------------------------------------------------
  function applySendMode(mode) {
    if (!mode) return;
    gm.sendMode = mode;
    const button = $("gm-send-mode");
    const minutes = Math.max(1, Math.ceil((mode.remaining_s || 0) / 60));
    button.className = `gm-send-mode${mode.enabled ? " enabled" : ""}`;
    button.textContent = mode.enabled ? `⚡ Senden · ${minutes} min` : "◉ Nur beobachten";
    button.title = mode.enabled ? "Senden ist freigegeben und schaltet sich automatisch ab. Klicken, um sofort zu sperren." : "Senden ist gesperrt. Klicken, um Senden für 30 Minuten freizugeben.";
    $("gm-control-lock").hidden = mode.enabled;
    document.body.classList.toggle("gm-can-send", Boolean(mode.enabled));
  }
  async function refreshSendMode() {
    try { applySendMode(await api("/api/monitor/send-mode")); } catch { /* Anzeige bleibt */ }
  }
  async function setSendMode(enabled) {
    applySendMode(await api("/api/monitor/send-mode", {method: "POST", body: JSON.stringify({enabled})}));
  }
  async function ensureSendMode() {
    if (gm.sendMode.enabled) return true;
    if (!window.confirm("Senden für 30 Minuten freigeben?\n\nTelegramme wirken sofort auf die Anlage – Licht, Storen, Heizung. Danach schaltet die Freigabe automatisch ab.")) return false;
    await setSendMode(true);
    return true;
  }
  $("gm-send-mode").addEventListener("click", async () => {
    try {
      if (gm.sendMode.enabled) await setSendMode(false);
      else await ensureSendMode();
    } catch (error) { window.alert(error.message); }
  });
  setInterval(refreshSendMode, 15000);

  async function send(address, payload) {
    const data = await api("/api/monitor/send", {method: "POST", body: JSON.stringify({group_address: address, ...payload})});
    // Ohne laufenden Monitor kommt das eigene Telegramm nicht über den Websocket – direkt anzeigen
    if (typeof monitorRunning !== "undefined" && !monitorRunning && data.sent) processTelegram(data.sent);
    return data.sent;
  }

  // --- Bedienen ------------------------------------------------------------------------------
  function controlBody(group) {
    const kind = group.kind;
    const value = group.value;
    const buttons = (items) => `<div class="gm-buttons">${items.map(([label, val, cls = ""]) => `<button type="button" class="gm-send ${cls}" data-value='${esc(JSON.stringify(val))}'>${label}</button>`).join("")}</div>`;
    const slider = (min, max, step, current, unit) => `<div class="gm-slider"><input id="gm-slider" type="range" min="${min}" max="${max}" step="${step}" value="${current}" /><output id="gm-slider-out">${nf.format(current)} ${unit}</output><button type="button" class="primary gm-send-slider">Senden</button></div>`;
    switch (kind) {
      case "light": case "switch":
        return buttons([["Ein", true, "on"], ["Aus", false], [value === true ? "Umschalten → Aus" : "Umschalten → Ein", !(value === true)]]);
      case "updown": return buttons([["▲ Auf", false], ["▼ Ab", true]]);
      case "blindstep": return buttons([["▲ Schritt / Stopp", false], ["▼ Schritt / Stopp", true]]);
      case "openclose": return buttons([["Öffnen", false], ["Schliessen", true]]);
      case "window": return '<p class="gm-hint">Fenster-/Türkontakt – nur Anzeige. Mit «Wert lesen» den aktuellen Zustand abfragen.</p>';
      case "dim": return buttons([["☀ Heller", {control: 1, step_code: 1}], ["☾ Dunkler", {control: 0, step_code: 1}], ["■ Stopp", {control: 0, step_code: 0}]]);
      case "level": case "blind":
        return `${slider(0, 100, 1, typeof value === "number" ? Math.round(value) : 50, "%")}${buttons([[kind === "blind" ? "0 % (offen)" : "0 % (aus)", 0], ["25 %", 25], ["50 %", 50], ["75 %", 75], [kind === "blind" ? "100 % (zu)" : "100 %", 100]])}`;
      case "angle": return slider(0, 360, 1, typeof value === "number" ? value : 0, "°");
      case "hvac": return buttons(Object.entries({comfort: "☀ Komfort", standby: "◐ Standby", night: "☾ Nacht", frost_protection: "❄ Frostschutz"}).map(([key, label]) => [label, key]));
      case "scene": return `<div class="gm-inline"><label>Szene (1–64)<input id="gm-value-input" type="number" min="1" max="64" value="1" /></label><button type="button" class="primary gm-send-input">Szene aufrufen</button></div>`;
      case "rgb": return `<div class="gm-inline"><label>Farbe<input id="gm-color-input" type="color" value="${value && typeof value === "object" ? rgbHex(value) : "#ffffff"}" /></label><button type="button" class="primary gm-send-color">Farbe senden</button></div>`;
      case "raw":
        return `<p class="gm-hint">Datentyp unbekannt. DPT festlegen – dann zeigt die Kachel den Wert lesbar und das passende Bedienelement.</p>
          <div class="gm-inline"><label>Datentyp (DPT)<select id="gm-dpt-select"><option value="">Bitte wählen …</option>${DPT_OPTIONS.map(([dpt, label]) => `<option value="${dpt}">${dpt} · ${label}</option>`).join("")}</select></label><button type="button" class="secondary gm-set-dpt">Übernehmen</button></div>
          <div class="gm-inline"><label>Rohwert (Hex)<input id="gm-raw-input" placeholder="z. B. 01 oder 0C 1A" /></label><label class="gm-check"><input id="gm-raw-small" type="checkbox" /> kurz (1–6 Bit)</label><button type="button" class="primary gm-send-raw">Senden</button></div>`;
      default:
        return `<div class="gm-inline"><label>Neuer Wert${group.unit ? ` (${esc(group.unit)})` : ""}<input id="gm-value-input" value="${typeof value === "number" || typeof value === "string" ? esc(value) : ""}" /></label><button type="button" class="primary gm-send-input">Senden</button></div>`;
    }
  }

  function dptSection(group) {
    if (group.kind === "raw") return "";
    const label = group.dpt ? `${group.dpt}${group.dptGuessed ? " – vermutet (im Projekt ohne Datentyp)" : group.dptSource === "objekt" ? " – vom Kommunikationsobjekt" : ""}` : "unbekannt";
    const options = DPT_OPTIONS.some(([dpt]) => dpt === group.dpt) ? DPT_OPTIONS : [...(group.dpt ? [[group.dpt, "aktueller Datentyp"]] : []), ...DPT_OPTIONS];
    return `<details class="gm-dpt-change"${group.dptGuessed ? " open" : ""}><summary>Datentyp: <b>${esc(label)}</b></summary>
      <div class="gm-inline"><select id="gm-dpt-select">${options.map(([dpt, text]) => `<option value="${dpt}"${dpt === group.dpt ? " selected" : ""}>${dpt} · ${text}</option>`).join("")}</select>
      <button type="button" class="secondary gm-set-dpt">${group.dptGuessed ? "Bestätigen / übernehmen" : "Übernehmen"}</button></div>
      <small class="gm-muted">Wird auf diesem PC gespeichert und gilt sofort – auch für den letzten Wert.</small></details>`;
  }

  function fillControlHistory(group) {
    $("gm-control-history").innerHTML = group.history.length
      ? group.history.map((entry) => `<span><time>${esc(new Date(entry.time).toLocaleTimeString("de-CH"))}</time> ${esc(entry.text)} <small>${esc(entry.source || "")}${entry.service === "GroupValueResponse" ? " · Antwort" : ""}</small></span>`).join("")
      : "<span>Noch keine Werte empfangen.</span>";
    $("gm-control-visual").className = `gm-control-visual ${tileState(group)}`;
    $("gm-control-visual").innerHTML = `<span class="gm-visual">${visual(group)}</span>`;
    $("gm-control-meta").textContent = `${group.secure ? "🔒 Data Secure · " : ""}${group.dpt ? `DPT ${group.dpt}` : "Datentyp unbekannt"} · aktuell ${valueText(group)}${group.count ? ` · ${group.count} Telegramme` : ""}${group.locked ? " · Schlüsselbund in Schritt 1 «Projekt» laden" : ""}`;
  }

  function openControl(address) {
    const group = gm.groups.get(address);
    if (!group) return;
    const dialog = $("gm-control");
    dialog.dataset.ga = address;
    $("gm-control-address").textContent = `${group.address}${group.status ? " · STATUS" : ""}`;
    $("gm-control-title").textContent = group.name || group.address;
    $("gm-control-body").innerHTML = controlBody(group) + dptSection(group);
    $("gm-control-message").textContent = "";
    $("gm-control-message").className = "message";
    const pair = gm.pairs.find((item) => item.command === address);
    $("gm-control-test").hidden = !pair && group.status;
    $("gm-control-test").textContent = pair ? `✓ Funktionstest mit ${pair.status}` : "✓ Als Funktionstest";
    fillControlHistory(group);
    applySendMode(gm.sendMode);
    if (!dialog.open) dialog.showModal();
    const sliderInput = $("gm-slider");
    if (sliderInput) sliderInput.addEventListener("input", () => { $("gm-slider-out").textContent = `${nf.format(Number(sliderInput.value))} ${group.kind === "angle" ? "°" : "%"}`; });
  }

  async function controlSend(payload) {
    const dialog = $("gm-control");
    const message = $("gm-control-message");
    try {
      if (!(await ensureSendMode())) return;
      const sent = await send(dialog.dataset.ga, payload);
      message.className = sent.secure_warning ? "message error" : "message success";
      message.textContent = payload.service === "read" ? "Lese-Anfrage gesendet – Antwort erscheint in der Kachel."
        : `Gesendet${sent.secure ? " (Data Secure, verschlüsselt)" : ""}: ${sent.value ?? sent.hex}${sent.secure_warning ? ` – ⚠ ${sent.secure_warning}` : ""}`;
    } catch (error) {
      message.className = "message error";
      message.textContent = error.message;
    }
  }

  $("gm-control-body").addEventListener("click", async (event) => {
    const group = gm.groups.get($("gm-control").dataset.ga);
    if (!group) return;
    const button = event.target.closest("button");
    if (!button) return;
    if (button.classList.contains("gm-send")) await controlSend({dpt: group.dpt, value: JSON.parse(button.dataset.value)});
    else if (button.classList.contains("gm-send-slider")) await controlSend({dpt: group.dpt, value: Number($("gm-slider").value)});
    else if (button.classList.contains("gm-send-input")) await controlSend({dpt: group.dpt, value: $("gm-value-input").value});
    else if (button.classList.contains("gm-send-color")) {
      const hex = $("gm-color-input").value;
      await controlSend({dpt: group.dpt, value: {red: parseInt(hex.slice(1, 3), 16), green: parseInt(hex.slice(3, 5), 16), blue: parseInt(hex.slice(5, 7), 16)}});
    } else if (button.classList.contains("gm-send-raw")) await controlSend({raw_hex: $("gm-raw-input").value, raw_small: $("gm-raw-small").checked});
    else if (button.classList.contains("gm-set-dpt")) await setDpt(group, $("gm-dpt-select").value);
  });

  async function setDpt(group, dpt) {
    if (!dpt) return;
    try {
      const data = await api("/api/monitor/dpt", {method: "PUT", body: JSON.stringify({group_address: group.address, dpt})});
      group.dpt = data.group.dpt;
      group.dptGuessed = false;
      group.unit = data.group.info?.unit || null;
      if (data.group.state) {
        group.value = data.group.state.value_json ?? null;
        group.unit = data.group.state.unit || group.unit;
      }
      group.kind = kindFor(group);
      saveDptOverride(group.address, dpt);
      renderTiles();
      openControl(group.address);
      $("gm-control-message").className = "message success";
      $("gm-control-message").textContent = `Datentyp ${dpt} festgelegt${data.group.state ? ` – aktueller Wert: ${valueText(group)}` : ""}.`;
    } catch (error) {
      $("gm-control-message").className = "message error";
      $("gm-control-message").textContent = error.message;
    }
  }

  const DPT_STORE = "cko-ibs-gm-dpts";
  function saveDptOverride(address, dpt) {
    try {
      const saved = JSON.parse(localStorage.getItem(DPT_STORE) || "{}");
      saved[address] = dpt;
      localStorage.setItem(DPT_STORE, JSON.stringify(saved));
    } catch { /* ohne Speicher gilt die Wahl bis zum Schliessen */ }
  }
  async function restoreDptOverrides() {
    let saved = {};
    try { saved = JSON.parse(localStorage.getItem(DPT_STORE) || "{}"); } catch { return; }
    for (const [address, dpt] of Object.entries(saved)) {
      const group = gm.groups.get(address);
      if (group?.dpt) continue; // Projekt hat Vorrang
      try {
        await api("/api/monitor/dpt", {method: "PUT", body: JSON.stringify({group_address: address, dpt})});
        const target = ensureGroup(address, {dpt});
        target.kind = kindFor(target);
      } catch { /* ungültige Einträge ignorieren */ }
    }
  }

  $("gm-control-read").addEventListener("click", () => controlSend({service: "read"}));
  $("gm-control-unlock").addEventListener("click", async () => { try { await ensureSendMode(); } catch (error) { window.alert(error.message); } });
  $("gm-control-test").addEventListener("click", () => {
    const address = $("gm-control").dataset.ga;
    const group = gm.groups.get(address);
    const pair = gm.pairs.find((item) => item.command === address);
    addTest({command: address, command_name: group?.name, status: pair?.status || address, status_name: pair?.status_name || group?.name, dpt: group?.dpt});
    $("gm-control").close();
    selectTab("test");
  });

  // --- Werte lesen -------------------------------------------------------------------------
  $("gm-read-all").addEventListener("click", async () => {
    const button = $("gm-read-all");
    if (button.dataset.running === "true") { button.dataset.stop = "true"; return; }
    const query = $("gm-tile-search").value.trim().toLocaleLowerCase("de-CH");
    const filter = $("gm-tile-filter").value;
    const targets = [...gm.groups.values()].filter((group) => tileVisible(group, query, filter)
      && (group.status || ["temp", "lux", "wind", "humidity", "air", "power", "number", "window", "openclose"].includes(group.kind)));
    if (!targets.length) { window.alert("Unter den angezeigten Kacheln sind keine Status- oder Messwert-Adressen. Filter «Nur Status/Messwerte» hilft beim Eingrenzen."); return; }
    if (targets.length > 40 && !window.confirm(`${targets.length} Lese-Anfragen senden? Das dauert etwa ${Math.ceil(targets.length / 6)} Sekunden und belastet den Bus kurzzeitig.`)) return;
    try { if (!(await ensureSendMode())) return; } catch (error) { window.alert(error.message); return; }
    button.dataset.running = "true";
    button.dataset.stop = "false";
    for (let index = 0; index < targets.length && button.dataset.stop !== "true"; index += 1) {
      button.textContent = `■ ${index + 1}/${targets.length} lesen …`;
      try { await send(targets[index].address, {service: "read", label: "Werte lesen"}); } catch (error) { window.alert(error.message); break; }
      await new Promise((resolve) => setTimeout(resolve, 160));
    }
    button.dataset.running = "false";
    button.textContent = "⟳ Werte lesen";
  });

  // --- Bus-Netz ----------------------------------------------------------------------------
  const net = {left: new Map(), right: new Map(), particles: [], width: 0, height: 0, hover: null};
  const NET_MAX = 36;

  function netNode(map, key, label, sub) {
    let node = map.get(key);
    if (!node) {
      if (map.size >= NET_MAX) {
        const oldest = [...map.values()].sort((a, b) => a.seen - b.seen)[0];
        map.delete(oldest.key);
      }
      node = {key, label, sub, heat: 0, count: 0, seen: 0, x: 0, y: 0, ty: null};
      map.set(key, node);
      layoutNet();
    }
    node.label = label || node.label;
    node.sub = sub || node.sub;
    return node;
  }

  function netTelegram(item, group) {
    const own = item.direction === "Outgoing";
    const sourceKey = own ? "IBS" : item.source || "?";
    const sourceName = own ? "IBS Scanner (du)" : gm.devices.get(sourceKey) || "";
    const left = netNode(net.left, sourceKey, sourceName, own ? "" : sourceKey);
    const right = netNode(net.right, group.address, group.name || "", group.address);
    const now = performance.now();
    left.seen = right.seen = now;
    left.count += 1; right.count += 1;
    left.heat = Math.min(1.6, left.heat + 0.8);
    const service = own ? "own" : item.service === "GroupValueRead" ? "read" : item.service === "GroupValueResponse" ? "response" : "write";
    net.particles.push({from: left, to: right, start: now, duration: 1100, service});
    if (net.particles.length > 220) net.particles.splice(0, net.particles.length - 220);
    $("gm-net-empty").hidden = true;
  }

  function lineOf(key) {
    return key === "IBS" ? "0" : String(key).split(".").slice(0, 2).join(".");
  }

  function layoutNet() {
    const pad = 46;
    const place = (map, sortKey, x, headerOf) => {
      const nodes = [...map.values()].sort((a, b) => sortKey(a).localeCompare(sortKey(b)));
      const headers = [];
      let previous = null;
      const slots = nodes.length + new Set(nodes.map(headerOf)).size;
      const step = Math.min(34, (net.height - pad * 2) / Math.max(1, slots));
      let y = Math.max(pad, (net.height - step * slots) / 2);
      nodes.forEach((node) => {
        const header = headerOf(node);
        if (header !== previous) { headers.push({label: header, y: y + step * 0.35}); y += step; previous = header; }
        node.x = x; node.ty = y + step / 2;
        if (!node.y) node.y = node.ty;
        y += step;
      });
      return headers;
    };
    net.leftX = Math.max(150, net.width * 0.2);
    net.rightX = Math.min(net.width - 170, net.width * 0.78);
    net.leftHeaders = place(net.left, (node) => node.key === "IBS" ? "" : node.key.split(".").map((part) => part.padStart(4, "0")).join("."),
      net.leftX, (node) => node.key === "IBS" ? "Diese Anwendung" : `Linie ${lineOf(node.key)}`);
    net.rightHeaders = place(net.right, (node) => gaSortKey(node.key), net.rightX,
      (node) => { const main = gaParts(node.key)[0]; return `${main} · ${gm.ranges[main] || `Hauptgruppe ${main}`}`; });
  }

  function resizeNet() {
    const canvas = $("gm-net-canvas");
    const wrap = canvas.parentElement;
    const ratio = window.devicePixelRatio || 1;
    net.width = wrap.clientWidth;
    net.height = Math.max(460, Math.min(900, window.innerHeight - 330));
    canvas.style.height = `${net.height}px`;
    canvas.width = net.width * ratio;
    canvas.height = net.height * ratio;
    canvas.getContext("2d").setTransform(ratio, 0, 0, ratio, 0, 0);
    layoutNet();
  }
  window.addEventListener("resize", () => { if (gm.tab === "net") resizeNet(); });

  const SERVICE_COLORS = () => ({write: cssVar("--blue", "#49a6ff"), read: cssVar("--orange", "#f1a23d"),
    response: cssVar("--green", "#37cc8a"), own: cssVar("--violet", "#a878ff")});

  function bezier(p0, p1, p2, p3, t) {
    const u = 1 - t;
    return u * u * u * p0 + 3 * u * u * t * p1 + 3 * u * t * t * p2 + t * t * t * p3;
  }

  function drawNet(now) {
    requestAnimationFrame(drawNet);
    if (gm.tab !== "net" || !net.width) return;
    const ctx = $("gm-net-canvas").getContext("2d");
    const colors = SERVICE_COLORS();
    const text = cssVar("--text", "#f4f7fb");
    const muted = cssVar("--muted", "#8f9caf");
    const line = cssVar("--line", "#263346");
    const spine = net.width * 0.5;
    ctx.clearRect(0, 0, net.width, net.height);
    // Bus-Rückgrat
    const gradient = ctx.createLinearGradient(spine - 3, 0, spine + 3, 0);
    gradient.addColorStop(0, "rgba(73,166,255,0)"); gradient.addColorStop(0.5, "rgba(73,166,255,.35)"); gradient.addColorStop(1, "rgba(73,166,255,0)");
    ctx.fillStyle = gradient;
    ctx.fillRect(spine - 6, 20, 12, net.height - 40);
    ctx.fillStyle = muted; ctx.font = "600 10px Inter, Segoe UI, sans-serif"; ctx.textAlign = "center";
    ctx.fillText("KNX-BUS", spine, 14);
    const labels = $("gm-net-labels").checked;
    const drawSide = (map, headers, align) => {
      ctx.textAlign = align;
      ctx.fillStyle = muted; ctx.font = "700 10px Inter, Segoe UI, sans-serif";
      (headers || []).forEach((header) => ctx.fillText(header.label.toUpperCase().slice(0, 34), map === net.left ? net.leftX + 10 : net.rightX - 10, header.y));
      map.forEach((node) => {
        node.y += (node.ty - node.y) * 0.15;
        node.heat *= 0.965;
        ctx.strokeStyle = line; ctx.lineWidth = 1;
        ctx.beginPath(); ctx.moveTo(node.x, node.y);
        ctx.bezierCurveTo((node.x + spine) / 2, node.y, spine, node.y, spine, node.y); ctx.stroke();
        const radius = 4 + Math.min(5, Math.log2(node.count + 1));
        if (node.heat > 0.05) {
          ctx.fillStyle = `rgba(73,166,255,${Math.min(0.45, node.heat * 0.35)})`;
          ctx.beginPath(); ctx.arc(node.x, node.y, radius + 10 * node.heat, 0, Math.PI * 2); ctx.fill();
        }
        ctx.fillStyle = node.key === "IBS" ? colors.own : node.heat > 0.3 ? colors.write : muted;
        ctx.beginPath(); ctx.arc(node.x, node.y, radius, 0, Math.PI * 2); ctx.fill();
        if (labels || net.hover === node) {
          ctx.fillStyle = node.heat > 0.3 ? text : muted;
          ctx.font = "600 11px Inter, Segoe UI, sans-serif";
          const caption = map === net.left ? `${node.label ? `${node.label.slice(0, 26)} ` : ""}${node.sub}` : `${node.sub} ${node.label.slice(0, 30)}`;
          ctx.fillText(caption.trim(), map === net.left ? node.x - radius - 8 : node.x + radius + 8, node.y + 4);
        }
      });
    };
    drawSide(net.left, net.leftHeaders, "right");
    drawSide(net.right, net.rightHeaders, "left");
    // Telegramme als Lichtpunkte
    net.particles = net.particles.filter((particle) => {
      if (!net.left.has(particle.from.key) || !net.right.has(particle.to.key)) return false;
      const progress = (now - particle.start) / particle.duration;
      if (progress >= 1) {
        particle.to.heat = Math.min(1.8, particle.to.heat + 1);
        return false;
      }
      const t = progress < 0 ? 0 : progress;
      const color = colors[particle.service] || colors.write;
      const points = [particle.from, {x: spine, y: particle.from.y}, {x: spine, y: particle.to.y}, particle.to];
      for (let trail = 0; trail < 7; trail += 1) {
        const tt = Math.max(0, t - trail * 0.025);
        const x = bezier(points[0].x, points[1].x, points[2].x, points[3].x, tt);
        const y = bezier(points[0].y, points[1].y, points[2].y, points[3].y, tt);
        ctx.globalAlpha = (1 - trail / 7) * 0.9;
        ctx.fillStyle = color;
        ctx.beginPath(); ctx.arc(x, y, trail ? 3.4 - trail * 0.35 : 4.5, 0, Math.PI * 2); ctx.fill();
        if (!trail) {
          ctx.globalAlpha = 0.25;
          ctx.beginPath(); ctx.arc(x, y, 11, 0, Math.PI * 2); ctx.fill();
        }
      }
      ctx.globalAlpha = 1;
      return true;
    });
    // Ziel-Kreise zeigen den Wert kurz an
    net.right.forEach((node) => {
      if (node.heat > 0.9) {
        ctx.strokeStyle = colors.write; ctx.globalAlpha = Math.min(1, node.heat - 0.8);
        ctx.beginPath(); ctx.arc(node.x, node.y, 9 + (1.8 - node.heat) * 22, 0, Math.PI * 2); ctx.stroke();
        ctx.globalAlpha = 1;
      }
    });
  }
  requestAnimationFrame(drawNet);

  $("gm-net-canvas").addEventListener("mousemove", (event) => {
    const rect = event.currentTarget.getBoundingClientRect();
    const x = event.clientX - rect.left;
    const y = event.clientY - rect.top;
    const nodes = [...net.left.values(), ...net.right.values()];
    const hit = nodes.find((node) => Math.hypot(node.x - x, node.y - y) < 12);
    net.hover = hit || null;
    const tip = $("gm-net-tip");
    if (!hit) { tip.hidden = true; return; }
    const group = gm.groups.get(hit.key);
    tip.hidden = false;
    tip.style.left = `${Math.min(rect.width - 240, x + 14)}px`;
    tip.style.top = `${y + 10}px`;
    tip.innerHTML = group
      ? `<strong>${esc(group.address)} · ${esc(group.name || "")}</strong><span>${esc(valueText(group))}</span><small>${group.count} Telegramme · ${esc(group.dpt || "DPT ?")} · Klick zum Bedienen</small>`
      : `<strong>${esc(hit.key === "IBS" ? "IBS Scanner" : hit.key)}</strong><span>${esc(hit.label || "Gerät")}</span><small>${hit.count} Telegramme gesendet</small>`;
  });
  $("gm-net-canvas").addEventListener("mouseleave", () => { $("gm-net-tip").hidden = true; net.hover = null; });
  $("gm-net-canvas").addEventListener("click", () => { if (net.hover && gm.groups.has(net.hover.key)) openControl(net.hover.key); });
  $("gm-net-clear").addEventListener("click", () => {
    net.left.clear(); net.right.clear(); net.particles = [];
    $("gm-net-empty").hidden = false;
  });

  // --- Funktionstest ---------------------------------------------------------------------------
  function defaultValues(group) {
    const kind = group ? kindFor(group) : "switch";
    if (["light", "switch"].includes(kind)) return ["ein", "aus"];
    if (kind === "updown") return ["ab", "auf"];
    if (kind === "openclose") return ["zu", "offen"];
    if (kind === "level") return ["50", "0"];
    if (kind === "blind") return ["30", "0"];
    if (kind === "dim") return [];
    if (kind === "hvac") return ["standby", "comfort"];
    return [];
  }

  function addTest(test, selected = true) {
    const command = gm.groups.get(test.command);
    const entry = {
      id: `${test.command}>${test.status}`, command: test.command, command_name: test.command_name || command?.name || "",
      status: test.status || test.command, status_name: test.status_name || gm.groups.get(test.status)?.name || "",
      dpt: test.dpt || command?.dpt || null, values: test.values || defaultValues(command || {dpt: test.dpt, address: test.command}),
      selected, results: [], reason: test.reason || "", auto: Boolean(test.reason),
    };
    const existing = gm.tests.find((item) => item.id === entry.id);
    if (existing) { if (selected) existing.selected = true; } else gm.tests.push(entry);
    renderTests();
  }

  function buildTestsFromPairs() {
    // Vorschläge aus dem Projekt – bewusst nicht vorausgewählt
    gm.pairs.forEach((pair) => addTest({command: pair.command, command_name: pair.command_name, status: pair.status,
      status_name: pair.status_name, dpt: pair.command_dpt, reason: pair.reason}, false));
  }

  const RESULT_LABEL = {ok: "✓", mismatch: "≠", no_reply: "×", error: "!"};
  const testCollapsed = new Set();

  function testState(test) {
    if (test.running) return "running";
    if (!test.results.length) return "open";
    return test.results.some((result) => result.result !== "ok") ? "problem" : "ok";
  }

  function testVisible(test) {
    const query = $("gm-test-search").value.trim().toLocaleLowerCase("de-CH");
    const show = $("gm-test-show").value;
    if (show === "selected" && !test.selected) return false;
    if (show === "problems" && testState(test) !== "problem") return false;
    if (show === "open" && testState(test) !== "open") return false;
    return !query || `${test.command} ${test.command_name} ${test.status} ${test.status_name}`.toLocaleLowerCase("de-CH").includes(query);
  }

  function testSection(test) {
    const grouping = $("gm-test-grouping").value;
    const group = gm.groups.get(test.command) || {address: test.command, dpt: test.dpt, name: test.command_name};
    if (!group.kind) group.kind = kindFor(group);
    return sectionKey(group, grouping);
  }

  function resultChips(test) {
    if (test.running && !test.results.length) return '<span class="gm-result running">läuft …</span>';
    return test.results.map((result) => `<span class="gm-result ${result.result}" title="${esc(result.message || "")}${result.received_text ? ` · Rückmeldung ${esc(result.received_text)}` : ""}">${RESULT_LABEL[result.result] || "?"} ${esc(result.value)}${result.latency_ms ? ` <small>${result.latency_ms} ms</small>` : ""}</span>`).join("")
      || '<small class="gm-muted">ungeprüft</small>';
  }

  function renderTests() {
    const body = $("gm-test-body");
    const visible = gm.tests.map((test, index) => ({test, index})).filter(({test}) => testVisible(test));
    if (!gm.tests.length) {
      body.innerHTML = '<tr class="gm-test-empty"><td colspan="6">ETS-Projekt laden – bedienbare Befehls- und Status-Adressen werden automatisch zugeordnet (nichts ist vorausgewählt). Ohne Projekt: «+ Test hinzufügen» oder in einer Kachel «Als Funktionstest».</td></tr>';
    } else if (!visible.length) {
      body.innerHTML = '<tr class="gm-test-empty"><td colspan="6">Kein Test passt zu Suche oder Filter.</td></tr>';
    } else {
      const sections = new Map();
      visible.forEach((entry) => {
        const {key, label} = testSection(entry.test);
        if (!sections.has(key)) sections.set(key, {label, items: []});
        sections.get(key).items.push(entry);
      });
      body.innerHTML = [...sections.entries()].sort(([a], [b]) => a.localeCompare(b)).map(([key, section]) => {
        const selected = section.items.filter(({test}) => test.selected).length;
        const done = section.items.filter(({test}) => test.results.length);
        const problems = done.filter(({test}) => testState(test) === "problem").length;
        const collapsed = testCollapsed.has(key);
        return `<tr class="gm-test-group" data-section="${esc(key)}"><td><input type="checkbox" class="gm-test-group-check" ${selected === section.items.length ? "checked" : ""} title="Ganze Gruppe auswählen" /></td>
          <td colspan="3"><button type="button" class="gm-test-group-toggle">${collapsed ? "▸" : "▾"} ${esc(section.label)}</button> <small>${section.items.length} Tests · ${selected} ausgewählt${done.length ? ` · ${done.length - problems} ✓ · ${problems} Probleme` : ""}</small></td>
          <td colspan="2"><button type="button" class="secondary compact gm-test-group-run">▶ Gruppe testen</button></td></tr>`
          + (collapsed ? "" : section.items.map(({test, index}) => `<tr data-index="${index}" class="gm-test-row ${testState(test)}">
            <td><input type="checkbox" class="gm-test-check" ${test.selected ? "checked" : ""} /></td>
            <td><strong>${esc(test.command)}</strong><small>${esc(test.command_name)}${test.dpt ? ` · ${esc(test.dpt)}` : ""}</small></td>
            <td><strong>${esc(test.status)}</strong><small>${esc(test.status_name)}</small>${test.reason ? `<small class="gm-reason">Vorschlag: ${esc(test.reason)}</small>` : ""}</td>
            <td><input class="gm-test-values" value="${esc(test.values.join("; "))}" placeholder="Werte, z. B. ein; aus" /></td>
            <td class="gm-test-results">${resultChips(test)}</td>
            <td class="gm-test-actions"><button type="button" class="secondary compact gm-test-one" title="Nur diesen Test ausführen">▶ Test</button><button type="button" class="gm-test-remove" title="Test entfernen">Entfernen</button></td>
          </tr>`).join(""));
      }).join("");
    }
    const results = gm.tests.flatMap((test) => test.results);
    const count = (kind) => results.filter((result) => result.result === kind).length;
    const selectedCount = gm.tests.filter((test) => test.selected).length;
    $("gm-test-summary").innerHTML = `<span>${gm.tests.length} Tests · ${selectedCount} ausgewählt${gm.pairs.length ? ` · ${gm.pairs.length} Vorschläge aus dem Projekt` : ""}</span>`
      + (results.length ? `<span class="ok">✓ ${count("ok")} in Ordnung</span><span class="mismatch">≠ ${count("mismatch")} abweichend</span><span class="no_reply">× ${count("no_reply")} ohne Rückmeldung</span>${count("error") ? `<span class="error">! ${count("error")} Fehler</span>` : ""}` : "");
    $("gm-test-protocol").disabled = !results.length;
    $("gm-test-run").textContent = selectedCount ? `▶ ${selectedCount} ausgewählte testen` : "▶ Ausgewählte testen";
    $("gm-test-run").disabled = gm.testRunning || !selectedCount;
  }

  ["gm-test-search"].forEach((id) => $(id).addEventListener("input", renderTests));
  ["gm-test-grouping", "gm-test-show"].forEach((id) => $(id).addEventListener("change", renderTests));
  $("gm-test-body").addEventListener("change", (event) => {
    const groupRow = event.target.closest("tr.gm-test-group");
    if (groupRow && event.target.classList.contains("gm-test-group-check")) {
      gm.tests.filter((test) => testVisible(test) && testSection(test).key === groupRow.dataset.section)
        .forEach((test) => { test.selected = event.target.checked; });
      renderTests();
      return;
    }
    const row = event.target.closest("tr[data-index]");
    if (!row) return;
    const test = gm.tests[Number(row.dataset.index)];
    if (event.target.classList.contains("gm-test-check")) { test.selected = event.target.checked; renderTests(); }
    if (event.target.classList.contains("gm-test-values")) test.values = event.target.value.split(";").map((value) => value.trim()).filter(Boolean);
  });
  $("gm-test-body").addEventListener("click", async (event) => {
    const groupRow = event.target.closest("tr.gm-test-group");
    if (groupRow) {
      const key = groupRow.dataset.section;
      if (event.target.closest(".gm-test-group-toggle")) {
        if (testCollapsed.has(key)) testCollapsed.delete(key); else testCollapsed.add(key);
        renderTests();
      }
      if (event.target.closest(".gm-test-group-run")) await runTests(gm.tests.filter((test) => testVisible(test) && testSection(test).key === key));
      return;
    }
    const row = event.target.closest("tr[data-index]");
    if (!row) return;
    const index = Number(row.dataset.index);
    if (event.target.closest(".gm-test-remove")) { gm.tests.splice(index, 1); renderTests(); }
    if (event.target.closest(".gm-test-one")) await runTests([gm.tests[index]]);
  });
  $("gm-test-select-visible").addEventListener("click", () => { gm.tests.filter(testVisible).forEach((test) => { test.selected = true; }); renderTests(); });
  $("gm-test-select-none").addEventListener("click", () => { gm.tests.forEach((test) => { test.selected = false; }); renderTests(); });
  $("gm-test-remove-selected").addEventListener("click", () => {
    const count = gm.tests.filter((test) => test.selected).length;
    if (!count || !window.confirm(`${count} ausgewählte Tests entfernen?`)) return;
    gm.tests = gm.tests.filter((test) => !test.selected);
    renderTests();
  });
  $("gm-test-remove-all").addEventListener("click", () => {
    if (!gm.tests.length || !window.confirm(`Alle ${gm.tests.length} Tests entfernen? Vorschläge aus dem Projekt lassen sich durch erneutes Laden des Projekts wiederherstellen.`)) return;
    gm.tests = [];
    renderTests();
  });
  $("gm-test-run").addEventListener("click", () => runTests(gm.tests.filter((test) => test.selected)));
  $("gm-test-stop").addEventListener("click", () => { gm.testStop = true; });

  async function runTests(tests) {
    if (gm.testRunning) return;
    const runnable = tests.filter((test) => test.values.length);
    if (!runnable.length) { window.alert("Keine Testwerte eingetragen. In der Spalte «Testwerte» z. B. «ein; aus» oder «50; 0» eingeben."); return; }
    try { if (!(await ensureSendMode())) return; } catch (error) { window.alert(error.message); return; }
    gm.testRunning = true; gm.testStop = false;
    $("gm-test-run").disabled = true; $("gm-test-stop").disabled = false;
    const timeout = Number($("gm-test-timeout").value);
    const pause = Number($("gm-test-pause").value) * 1000;
    for (const test of runnable) {
      if (gm.testStop) break;
      test.results = []; test.running = true; renderTests();
      for (const value of test.values) {
        if (gm.testStop) break;
        try {
          const result = await api("/api/monitor/function-test", {method: "POST", body: JSON.stringify({command: test.command, status: test.status, value, dpt: test.dpt, timeout})});
          test.results.push({...result, value});
        } catch (error) {
          test.results.push({result: "error", value, message: error.message});
          if (error.status === 409 || error.status === 423) gm.testStop = true;
        }
        renderTests();
        await new Promise((resolve) => setTimeout(resolve, pause));
      }
      test.running = false; renderTests();
    }
    gm.testRunning = false;
    $("gm-test-stop").disabled = true;
    renderTests();
  }

  $("gm-test-add").addEventListener("click", () => {
    ["gm-test-command", "gm-test-status", "gm-test-dpt", "gm-test-values"].forEach((id) => { $(id).value = ""; });
    $("gm-test-dialog-message").textContent = "";
    $("gm-test-dialog").showModal();
  });
  $("gm-test-command").addEventListener("change", () => {
    const address = $("gm-test-command").value.trim();
    const group = gm.groups.get(address);
    const pair = gm.pairs.find((item) => item.command === address);
    if (group?.dpt && !$("gm-test-dpt").value) $("gm-test-dpt").value = group.dpt;
    if (pair && !$("gm-test-status").value) $("gm-test-status").value = pair.status;
    if (!$("gm-test-values").value) $("gm-test-values").value = defaultValues(group || {dpt: $("gm-test-dpt").value, address}).join("; ");
  });
  $("gm-test-dialog-add").addEventListener("click", (event) => {
    const command = $("gm-test-command").value.trim();
    if (!/^\d+\/\d+(\/\d+)?$/.test(command)) {
      event.preventDefault();
      $("gm-test-dialog-message").className = "message error";
      $("gm-test-dialog-message").textContent = "Bitte eine Gruppenadresse wie 1/2/3 eingeben.";
      return;
    }
    addTest({command, status: $("gm-test-status").value.trim() || command, dpt: $("gm-test-dpt").value.trim() || null,
      values: $("gm-test-values").value.split(";").map((value) => value.trim()).filter(Boolean)});
  });

  async function saveFile(kind, content, openAfter = false) {
    const result = await api("/api/monitor/save", {method: "POST", body: JSON.stringify({kind, content})});
    if (openAfter) await api("/api/monitor/open", {method: "POST", body: JSON.stringify({path: result.path})}).catch(() => null);
    return result;
  }

  $("gm-test-protocol").addEventListener("click", async () => {
    const rows = gm.tests.flatMap((test) => test.results.map((result) => ({test, result})));
    const label = {ok: "in Ordnung", mismatch: "abweichend", no_reply: "keine Rückmeldung", error: "Fehler"};
    const date = new Date().toLocaleString("de-CH");
    const count = (kind) => rows.filter((row) => row.result.result === kind).length;
    const html = `<!doctype html><html lang="de"><head><meta charset="utf-8"><title>KNX-Funktionstest ${esc(gm.projectName)}</title><style>
      body{font:13px/1.45 "Segoe UI",Arial,sans-serif;color:#142337;margin:28px}h1{margin:0 0 4px;font-size:22px}.sub{color:#56657a;margin:0 0 16px}
      .sum{display:flex;gap:10px;margin:0 0 16px}.sum span{padding:6px 10px;border-radius:6px;background:#eef2f7;font-weight:600}
      table{width:100%;border-collapse:collapse}th,td{border:1px solid #c9d3df;padding:6px 8px;text-align:left;vertical-align:top}th{background:#eef2f7;font-size:12px}
      .ok{color:#147a3d;font-weight:700}.mismatch{color:#9a5b00;font-weight:700}.no_reply,.error{color:#b4202f;font-weight:700}small{color:#56657a}
      footer{margin-top:14px;color:#6b7a8f;font-size:11px}@page{size:A4 landscape;margin:12mm}@media print{body{margin:0}}</style></head><body>
      <h1>KNX-Funktionstest</h1><p class="sub">${esc(gm.projectName || "ohne ETS-Projekt")} · ${esc(date)} · erstellt mit dem CKO KNX IBS Scanner</p>
      <div class="sum"><span class="ok">✓ ${count("ok")} in Ordnung</span><span class="mismatch">≠ ${count("mismatch")} abweichend</span><span class="no_reply">× ${count("no_reply") + count("error")} ohne Rückmeldung/Fehler</span></div>
      <table><thead><tr><th>Befehl</th><th>Rückmeldung</th><th>Gesendet</th><th>Ergebnis</th><th>Empfangen</th><th>Zeit</th><th>Bemerkung</th></tr></thead><tbody>
      ${rows.map(({test, result}) => `<tr><td><b>${esc(test.command)}</b><br><small>${esc(test.command_name)}</small></td><td><b>${esc(test.status)}</b><br><small>${esc(test.status_name)}</small></td>
        <td>${esc(result.value)}</td><td class="${esc(result.result)}">${esc(label[result.result] || result.result)}</td><td>${esc(result.received_text ?? "–")}${result.source ? `<br><small>von ${esc(result.source)}</small>` : ""}</td>
        <td>${result.latency_ms ? `${result.latency_ms} ms` : "–"}${result.read_used ? "<br><small>nach Lesen</small>" : ""}</td><td><small>${esc(result.message || "")}</small></td></tr>`).join("")}
      </tbody></table><footer>Rückmeldung = Status-Telegramm (Schreiben oder Antwort) der zugeordneten Gruppenadresse innerhalb der Wartezeit.</footer></body></html>`;
    const csv = [["Befehl", "Befehl Name", "Status", "Status Name", "Gesendet", "Ergebnis", "Empfangen", "Quelle", "Zeit ms", "Bemerkung"],
      ...rows.map(({test, result}) => [test.command, test.command_name, test.status, test.status_name, result.value, label[result.result] || result.result,
        result.received_text ?? "", result.source ?? "", result.latency_ms ?? "", result.message ?? ""])]
      .map((row) => row.map((cell) => `"${String(cell).replaceAll('"', '""')}"`).join(";")).join("\r\n");
    try {
      await saveFile("protocol_csv", csv);
      const saved = await saveFile("protocol", html, true);
      $("gm-test-summary").insertAdjacentHTML("beforeend", `<span class="saved">Gespeichert: ${esc(saved.filename)} (+ CSV) im Ordner Downloads</span>`);
    } catch (error) { window.alert(error.message); }
  });

  // --- Telegramme als CSV -------------------------------------------------------------------
  $("gm-telegrams-csv").addEventListener("click", async () => {
    const rows = (typeof monitorTelegrams !== "undefined" ? monitorTelegrams : []).slice().reverse();
    if (!rows.length) { window.alert("Noch keine Telegramme aufgezeichnet."); return; }
    const csv = [["Zeit", "Richtung", "Quelle", "Gruppenadresse", "Bezeichnung", "DPT", "Dienst", "Wert", "Hex"],
      ...rows.map((item) => [item.time, item.direction, item.source, item.destination, item.group_name ?? "", item.dpt ?? "", item.service, item.value ?? "", item.hex ?? ""])]
      .map((row) => row.map((cell) => `"${String(cell ?? "").replaceAll('"', '""')}"`).join(";")).join("\r\n");
    try {
      const saved = await saveFile("telegrams", csv);
      await api("/api/monitor/open", {method: "POST", body: JSON.stringify({path: saved.path})}).catch(() => null);
      $("monitor-filter-note").textContent = `Gespeichert: ${saved.filename}`;
    } catch (error) { window.alert(error.message); }
  });

  // --- Aufnahme & Wiedergabe ------------------------------------------------------------------
  const fmtDuration = (ms) => `${String(Math.floor(ms / 60000)).padStart(2, "0")}:${String(Math.floor(ms / 1000) % 60).padStart(2, "0")}`;

  $("gm-rec-toggle").addEventListener("click", () => {
    const button = $("gm-rec-toggle");
    if (!gm.recording) {
      gm.recording = {started: Date.now(), telegrams: [], name: `Aufnahme ${new Date().toLocaleTimeString("de-CH", {hour: "2-digit", minute: "2-digit"})}`};
      button.classList.add("recording");
      button.querySelector("span").textContent = "Aufnahme stoppen";
      $("gm-rec-message").textContent = (typeof monitorRunning !== "undefined" && !monitorRunning) ? "Hinweis: Der Monitor läuft nicht – erst verbinden bzw. «Monitor starten»." : "";
    } else {
      const recording = {...gm.recording, duration: Date.now() - gm.recording.started};
      gm.recording = null;
      button.classList.remove("recording");
      button.querySelector("span").textContent = "Aufnahme starten";
      if (recording.telegrams.length) gm.recordings.unshift(recording);
      renderRecordings();
    }
  });
  setInterval(() => {
    if (!gm.recording) return;
    $("gm-rec-time").textContent = fmtDuration(Date.now() - gm.recording.started);
    $("gm-rec-count").textContent = `${gm.recording.telegrams.length} Telegramme`;
  }, 500);

  function renderRecordings() {
    $("gm-rec-list").innerHTML = gm.recordings.length ? gm.recordings.map((recording, index) => `<div class="gm-rec-item">
      <div><strong>${esc(recording.name)}</strong><small>${recording.telegrams.length} Telegramme · ${fmtDuration(recording.duration || 0)}${recording.project ? ` · ${esc(recording.project)}` : ""}</small></div>
      <button type="button" class="secondary compact" data-rec-play="${index}">▶ Öffnen</button><button type="button" class="secondary compact" data-rec-save="${index}">Speichern</button></div>`).join("")
      : '<p class="gm-hint">Noch keine Aufnahme in dieser Sitzung.</p>';
  }

  $("gm-rec-list").addEventListener("click", async (event) => {
    const play = event.target.closest("[data-rec-play]");
    const save = event.target.closest("[data-rec-save]");
    if (play) openPlayer(gm.recordings[Number(play.dataset.recPlay)]);
    if (save) {
      const recording = gm.recordings[Number(save.dataset.recSave)];
      try {
        const saved = await saveFile("recording", JSON.stringify({schema: "cko-knx-recording", schema_version: 1, name: recording.name,
          project: gm.projectName || recording.project || "", created: new Date(recording.started).toISOString(), duration: recording.duration,
          telegrams: recording.telegrams}, null, 1));
        $("gm-rec-message").className = "message success";
        $("gm-rec-message").textContent = `Gespeichert im Ordner Downloads: ${saved.filename}`;
      } catch (error) {
        $("gm-rec-message").className = "message error";
        $("gm-rec-message").textContent = error.message;
      }
    }
  });

  $("gm-rec-load").addEventListener("click", () => $("gm-rec-file").click());
  $("gm-rec-file").addEventListener("change", async (event) => {
    const file = event.target.files[0];
    event.target.value = "";
    if (!file) return;
    try {
      const data = JSON.parse(await file.text());
      if (data.schema !== "cko-knx-recording" || !Array.isArray(data.telegrams)) throw new Error("Keine Aufnahme des KNX IBS Scanners.");
      const recording = {name: data.name || file.name, project: data.project, started: Date.parse(data.created) || Date.now(),
        duration: data.duration || (data.telegrams.at(-1)?.t ?? 0), telegrams: data.telegrams};
      gm.recordings.unshift(recording);
      renderRecordings();
      openPlayer(recording);
    } catch (error) {
      $("gm-rec-message").className = "message error";
      $("gm-rec-message").textContent = error.message;
    }
  });

  function logValue(item) {
    if (item.value_json === null || item.value_json === undefined) return item.value ?? item.hex ?? "";
    const known = gm.groups.get(item.destination);
    const group = known || {address: item.destination, dpt: item.dpt, unit: item.unit, name: item.group_name};
    if (!known) group.kind = kindFor(group);
    return valueText(group, item.value_json);
  }

  function openPlayer(recording) {
    stopPlayback();
    gm.player = {recording, position: 0, playing: false, timer: null};
    $("gm-player").hidden = false;
    $("gm-player-title").textContent = recording.name;
    const writes = recording.telegrams.filter((item) => item.service === "GroupValueWrite" && item.direction !== "Outgoing").length;
    $("gm-player-info").textContent = `${recording.telegrams.length} Telegramme · ${fmtDuration(recording.duration || 0)} · ${writes} Schreib-Telegramme von Busgeräten`;
    $("gm-play-position").value = 0;
    $("gm-play-log").innerHTML = recording.telegrams.slice(0, 200).map((item, index) => `<div data-log="${index}"><time>${fmtDuration(item.t || 0)}.${String((item.t || 0) % 1000).padStart(3, "0")}</time><b>${esc(item.destination)}</b> ${esc(item.group_name || "")} <em>${esc(item.service === "GroupValueRead" ? "lesen" : logValue(item))}</em><small>${esc(item.source || "")}</small></div>`).join("")
      + (recording.telegrams.length > 200 ? `<div class="gm-hint">… und ${recording.telegrams.length - 200} weitere</div>` : "");
    $("gm-player").scrollIntoView({behavior: "smooth", block: "start"});
  }

  function stopPlayback() {
    if (gm.player?.timer) clearTimeout(gm.player.timer);
    if (gm.player) gm.player.playing = false;
    $("gm-play").textContent = "▶ Abspielen";
    $("gm-replay-banner").hidden = true;
  }

  function playStep() {
    const player = gm.player;
    if (!player?.playing) return;
    const items = player.recording.telegrams;
    if (player.position >= items.length) { stopPlayback(); return; }
    const item = items[player.position];
    processTelegram({...item, time: new Date().toISOString()}, {replay: true});
    document.querySelectorAll("#gm-play-log .current").forEach((row) => row.classList.remove("current"));
    document.querySelector(`#gm-play-log [data-log="${player.position}"]`)?.classList.add("current");
    $("gm-play-position").value = Math.round((player.position / Math.max(1, items.length - 1)) * 1000);
    player.position += 1;
    const next = items[player.position];
    const delay = next ? Math.max(0, (next.t - item.t) / Number($("gm-play-speed").value)) : 0;
    player.timer = setTimeout(playStep, Math.min(delay, 5000));
  }

  $("gm-play").addEventListener("click", () => {
    if (!gm.player) return;
    if (gm.player.playing) { stopPlayback(); return; }
    if (gm.player.position >= gm.player.recording.telegrams.length) gm.player.position = 0;
    gm.player.playing = true;
    $("gm-play").textContent = "❚❚ Pause";
    $("gm-replay-banner").hidden = false;
    playStep();
  });
  $("gm-play-position").addEventListener("input", (event) => {
    if (!gm.player) return;
    gm.player.position = Math.round((Number(event.target.value) / 1000) * Math.max(0, gm.player.recording.telegrams.length - 1));
  });
  $("gm-replay-stop-banner").addEventListener("click", stopPlayback);

  $("gm-play-bus").addEventListener("click", async () => {
    if (!gm.player) return;
    const items = gm.player.recording.telegrams.filter((item) => item.service === "GroupValueWrite" && item.hex);
    if (!items.length) { window.alert("Die Aufnahme enthält keine Schreib-Telegramme mit Rohwert."); return; }
    const duration = (items.at(-1).t - items[0].t) / Number($("gm-play-speed").value);
    if (!window.confirm(`${items.length} Schreib-Telegramme erneut auf den Bus senden (Dauer ca. ${fmtDuration(duration)})?\n\nDas löst die aufgezeichneten Funktionen erneut aus – Licht, Storen, Szenen. Nur mit Sicht auf die Anlage.`)) return;
    try { if (!(await ensureSendMode())) return; } catch (error) { window.alert(error.message); return; }
    const button = $("gm-play-bus");
    button.disabled = true;
    try {
      for (let index = 0; index < items.length; index += 1) {
        button.textContent = `⚡ ${index + 1}/${items.length} …`;
        await send(items[index].destination, {raw_hex: items[index].hex, raw_small: Boolean(items[index].small), label: "Wiedergabe"});
        const next = items[index + 1];
        if (next) await new Promise((resolve) => setTimeout(resolve, Math.min(5000, Math.max(60, (next.t - items[index].t) / Number($("gm-play-speed").value)))));
      }
    } catch (error) { window.alert(error.message); }
    button.disabled = false;
    button.textContent = "⚡ Auf den Bus senden";
  });

  // --- KNX Data Secure (Schritt 1 › Projekt) -----------------------------------------------
  function showSecureHint() {
    if (gm.secureHintShown || gm.dataSecure?.active) return;
    gm.secureHintShown = true;
    const hint = $("gm-tile-hint");
    hint.insertAdjacentHTML("afterend", '<div class="gm-secure-hint">🔒 Verschlüsselte Telegramme (KNX Data Secure) empfangen, aber kein passender Schlüssel geladen. In <b>Schritt 1 «Projekt»</b> unter «KNX Secure» die .knxkeys-Datei aus der ETS laden – dann erscheinen die Werte lesbar.</div>');
  }

  function applyDataSecure(status) {
    if (!status) return;
    gm.dataSecure = status;
    const badge = $("ds-badge");
    const box = $("ds-status");
    badge.className = `ds-badge${status.loaded ? (status.active || !busConnected ? " loaded" : " warn") : ""}`;
    badge.textContent = status.loaded ? `${status.group_address_count} Adressen${status.active ? " · aktiv" : busConnected ? " · nicht aktiv" : " · bereit"}` : "nicht geladen";
    $("ds-clear").hidden = !status.loaded;
    const note = $("secure-keyring-note");
    if (note) note.innerHTML = status.loaded
      ? `✓ Schlüsselbund aus Schritt 1 (<b>${esc(status.filename || ".knxkeys")}</b>) wird für den Secure-Tunnel verwendet – hier nichts weiter nötig.`
      : 'Tipp: Den Schlüsselbund einmal in <button type="button" class="link-button" data-go-view="project">Schritt 1 «Projekt»</button> laden – dann muss hier nichts mehr ausgewählt werden.';
    if (!status.loaded) { box.hidden = true; return; }
    box.hidden = false;
    const current = status.current_interface;
    box.innerHTML = `<span><b>${esc(status.filename || "Schlüsselbund")}</b> · ${status.group_address_count} Data-Secure-Gruppenadressen${status.undecoded ? ` · ${status.undecoded} Telegramme ohne Schlüssel` : ""}</span>
      <span>${status.active ? "✓ Entschlüsselung aktiv auf der laufenden Verbindung." : "Wird beim Verbinden automatisch verwendet."}
      ${busConnected && status.current_address ? (status.can_send ? ` Senden mit ${esc(status.current_address)} möglich.` : ` ⚠ Quelladresse ${esc(status.current_address)} ist nicht als Data-Secure-Sender eingetragen – Lesen geht, Senden wird ignoriert.`) : ""}</span>
      ${status.interfaces.length ? `<span>Tunneladressen aus dem Schlüsselbund (Klick übernimmt sie als Tunneladresse):</span><div class="ds-interfaces">${status.interfaces.map((item) => `<button type="button" data-ds-ia="${esc(item.individual_address)}" class="${current && current.individual_address === item.individual_address ? "current" : ""}">${esc(item.individual_address)} · ${esc(item.type)}${item.user_id ? ` · Benutzer ${item.user_id}` : ""} · ${item.secure_group_count} Secure-GA</button>`).join("")}</div>` : ""}`;
    gm.secure = new Set([...gm.secure]);
  }

  async function refreshDataSecure() {
    try { applyDataSecure(await api("/api/knx/data-secure")); } catch { /* Anzeige bleibt */ }
  }

  $("ds-file").addEventListener("change", (event) => {
    $("ds-file-label").textContent = event.target.files[0]?.name || ".knxkeys auswählen";
  });
  $("ds-load").addEventListener("click", async () => {
    const file = $("ds-file").files[0];
    const message = $("ds-message");
    if (!file) { message.className = "message error"; message.textContent = "Bitte zuerst die .knxkeys-Datei auswählen."; return; }
    const form = new FormData();
    form.append("keyring", file);
    form.append("password", $("ds-password").value);
    $("ds-load").disabled = true;
    message.className = "message";
    message.textContent = "Schlüsselbund wird lokal geöffnet …";
    try {
      const response = await fetch("/api/knx/data-secure", {method: "POST", body: form});
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || "Schlüsselbund konnte nicht geladen werden");
      message.className = "message success";
      message.textContent = data.group_address_count ? `${data.group_address_count} Data-Secure-Gruppenadressen geladen.` : "Geladen – der Schlüsselbund enthält aber keine Data-Secure-Gruppenadressen.";
      $("ds-password").value = "";
      applyDataSecure(data);
      loadGroups();
    } catch (error) {
      message.className = "message error";
      message.textContent = error.message;
    } finally {
      $("ds-load").disabled = false;
    }
  });
  $("ds-clear").addEventListener("click", async () => {
    try {
      applyDataSecure(await api("/api/knx/data-secure", {method: "DELETE"}));
      $("ds-message").className = "message";
      $("ds-message").textContent = "Schlüsselbund entfernt.";
      gm.secure = new Set();
      gm.groups.forEach((group) => { group.secure = false; });
      renderTiles();
    } catch (error) { $("ds-message").textContent = error.message; }
  });
  $("ds-status").addEventListener("click", (event) => {
    const button = event.target.closest("[data-ds-ia]");
    if (!button) return;
    $("individual-address").value = button.dataset.dsIa;
    $("ds-message").className = "message success";
    $("ds-message").textContent = `Tunneladresse ${button.dataset.dsIa} übernommen – in Schritt 2 mit «Verbinden» (TCP) neu verbinden.`;
  });

  // --- Schnellbedienung (in jeder Ansicht) ------------------------------------------------------
  const RECENT_STORE = "cko-ibs-gm-recent";
  const GA_PATTERN = /^\d{1,2}\/\d{1,2}\/\d{1,3}$|^\d{1,2}\/\d{1,4}$/;
  let quickWait = null;

  function quickGroup() {
    const text = $("qb-ga").value.trim();
    if (!text) return null;
    if (GA_PATTERN.test(text)) return ensureGroupLight(text);
    const query = text.toLocaleLowerCase("de-CH");
    const match = [...gm.groups.values()].find((group) => (group.name || "").toLocaleLowerCase("de-CH").includes(query));
    if (match) $("qb-ga").value = match.address;
    return match || null;
  }

  function ensureGroupLight(address) {
    return gm.groups.get(address) || {address, name: null, dpt: null, kind: "raw", value: null};
  }

  function updateQuickInfo() {
    const group = quickGroup();
    const info = $("qb-info");
    if (!group) { info.textContent = $("qb-ga").value.trim() ? "Unbekannt – als Gruppenadresse eingeben, z. B. 1/1/5" : "Gruppenadresse eingeben"; $("qb-toggle").hidden = true; return; }
    if (!$("qb-dpt").dataset.manual) $("qb-dpt").value = group.dpt || "";
    const kind = group.dpt ? kindFor({...group, dpt: $("qb-dpt").value || group.dpt}) : "raw";
    info.textContent = `${group.name || "ohne Namen"}${group.dpt ? ` · ${group.dpt}${group.dptGuessed ? " vermutet" : ""}` : ""}${group.count ? ` · aktuell ${valueText(group)}` : ""}`;
    $("qb-toggle").hidden = dptMain($("qb-dpt").value || group.dpt) !== 1;
    $("qb-value").placeholder = {light: "ein / aus", switch: "ein / aus", updown: "auf / ab", openclose: "offen / zu", level: "0–100 %", blind: "0–100 %", temp: "z. B. 21.5", hvac: "comfort, standby, night"}[kind] || "Wert, z. B. ein, 50, 21.5";
  }

  function rememberQuick(address) {
    let recent = [];
    try { recent = JSON.parse(localStorage.getItem(RECENT_STORE) || "[]"); } catch { /* leer */ }
    recent = [address, ...recent.filter((item) => item !== address)].slice(0, 8);
    try { localStorage.setItem(RECENT_STORE, JSON.stringify(recent)); } catch { /* optional */ }
    renderQuickRecent(recent);
  }

  function renderQuickRecent(recent) {
    if (!recent) { try { recent = JSON.parse(localStorage.getItem(RECENT_STORE) || "[]"); } catch { recent = []; } }
    $("qb-recent").innerHTML = recent.map((address) => `<button type="button" data-qb-ga="${esc(address)}" title="${esc(gm.groups.get(address)?.name || "")}">${esc(address)}</button>`).join("");
  }

  function quickResult(text, type = "") {
    const box = $("qb-result");
    box.className = `qb-result ${type}`;
    box.textContent = text;
  }

  function waitForReply(address, startedText) {
    if (quickWait) clearTimeout(quickWait.timer);
    const started = performance.now();
    // Rückmeldung kommt oft auf der zugehörigen Status-Adresse (z. B. 1/4/1 → 1/5/1)
    const status = gm.pairs.find((pair) => pair.command === address)?.status;
    quickWait = {address, status, started, timer: setTimeout(() => {
      if (quickWait?.address === address) { quickResult(`${startedText} – keine Rückmeldung innert 3 s`, "warn"); quickWait = null; }
    }, 3000)};
  }

  window.addEventListener("knx-telegram", (event) => {
    const item = event.detail;
    if (!quickWait || item.direction === "Outgoing" || ![quickWait.address, quickWait.status].includes(item.destination)) return;
    if (!["GroupValueResponse", "GroupValueWrite"].includes(item.service)) return;
    const group = gm.groups.get(item.destination);
    clearTimeout(quickWait.timer);
    const where = item.destination === quickWait.address ? "" : ` auf Status ${item.destination}`;
    quickResult(`${item.service === "GroupValueResponse" ? "Antwort" : "Rückmeldung"}${where} von ${item.source}: ${group ? valueText(group) : item.value} · ${Math.round(performance.now() - quickWait.started)} ms`, "ok");
    updateQuickInfo();
    quickWait = null;
  });

  async function quickAction(mode) {
    const group = quickGroup();
    const address = $("qb-ga").value.trim();
    if (!GA_PATTERN.test(address)) { quickResult("Bitte eine Gruppenadresse wie 1/1/5 eingeben oder einen Namen aus dem Projekt wählen.", "error"); $("qb-ga").focus(); return; }
    const dpt = $("qb-dpt").value.trim() || group?.dpt || null;
    try {
      if (!(await ensureSendMode())) return;
      rememberQuick(address);
      if (mode === "read") {
        await send(address, {service: "read", label: "Schnellbedienung"});
        quickResult(`Lese-Anfrage an ${address} gesendet …`);
        waitForReply(address, "Lese-Anfrage gesendet");
        return;
      }
      let value = $("qb-value").value.trim();
      if (mode === "toggle") value = group?.value === true ? "aus" : "ein";
      if (!value) { quickResult("Bitte einen Wert eingeben, z. B. «ein» oder «50».", "error"); $("qb-value").focus(); return; }
      const payload = dpt ? {dpt, value} : {raw_hex: value, raw_small: /^[0-3]?[0-9a-f]$/i.test(value)};
      const sent = await send(address, {...payload, label: "Schnellbedienung"});
      quickResult(`Gesendet: ${sent.value ?? sent.hex}${sent.secure_warning ? " – ⚠ Quelladresse ist kein Data-Secure-Sender" : ""} – warte auf Rückmeldung …`, sent.secure_warning ? "warn" : "");
      waitForReply(address, `Gesendet: ${sent.value ?? sent.hex}`);
    } catch (error) {
      quickResult(error.message, "error");
    }
  }

  $("qb-ga").addEventListener("input", () => { delete $("qb-dpt").dataset.manual; updateQuickInfo(); });
  $("qb-ga").addEventListener("change", updateQuickInfo);
  $("qb-dpt").addEventListener("input", () => { $("qb-dpt").dataset.manual = "1"; updateQuickInfo(); });
  $("qb-read").addEventListener("click", () => quickAction("read"));
  $("qb-send").addEventListener("click", () => quickAction("send"));
  $("qb-toggle").addEventListener("click", () => quickAction("toggle"));
  $("qb-value").addEventListener("keydown", (event) => { if (event.key === "Enter") quickAction("send"); });
  $("qb-ga").addEventListener("keydown", (event) => { if (event.key === "Enter") { updateQuickInfo(); $("qb-value").focus(); } });
  $("qb-recent").addEventListener("click", (event) => {
    const button = event.target.closest("[data-qb-ga]");
    if (!button) return;
    $("qb-ga").value = button.dataset.qbGa;
    delete $("qb-dpt").dataset.manual;
    updateQuickInfo();
    $("qb-value").focus();
  });
  renderQuickRecent();

  // --- Auswahllisten --------------------------------------------------------------------------
  function fillDatalists() {
    $("gm-ga-list").innerHTML = [...gm.groups.values()].sort((a, b) => gaSortKey(a.address).localeCompare(gaSortKey(b.address))).slice(0, 4000)
      .map((group) => `<option value="${esc(group.address)}" label="${esc(`${group.name || ""}${group.dpt ? ` · ${group.dpt}` : ""}`)}">${esc(group.name || "")}</option>`).join("");
    $("gm-dpt-list").innerHTML = DPT_OPTIONS.map(([dpt, label]) => `<option value="${dpt}">${label}</option>`).join("");
  }

  // Schnittstelle für die Gebäude-Seite (building.js)
  window.gmApi = {groups: gm.groups, valueText, openControl, kindFor};
  $("gm-tab-help").textContent = TAB_HELP.tiles;
  fillDatalists();
  loadGroups();
  refreshSendMode();
  refreshDataSecure();
  renderTests();
})();
