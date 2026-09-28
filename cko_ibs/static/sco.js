// SCO-Objekt (6 Byte): Testmaske, Sicherheit setzen/aufheben, Mitschnitt und Sektorübersicht.
// Nutzt den Monitor-Datenstrom und die Hilfsfunktionen aus app.js (byId, escapeHtml, startMonitor …).

let scoMode = "drive";
let scoPreview = null;        // {kind, ga, steps: [{key, label, frames: [{hex, decoded}]}]}
const scoLocks = new Map();   // "GA|Ziel" → {ga, target, release: [hex], label}

function scoTarget() {
  if (byId("sco-target-kind").value === "group") {
    return {group: {start: Number(byId("sco-group-start").value), size: Number(byId("sco-group-size").value)}};
  }
  return {sector: Number(byId("sco-sector").value)};
}

function scoTargetLabel(target) {
  if (target.group) return `Sektoren ${target.group.start}–${target.group.start + target.group.size - 1}`;
  return `Sektor ${target.sector}`;
}

async function scoApi(path, options = {}) {
  const response = await fetch(path, {headers: {"Content-Type": "application/json"}, ...options});
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.detail || `HTTP ${response.status}`);
  return data;
}

function scoSetMode(mode) {
  scoMode = mode;
  document.querySelectorAll("[data-sco-mode]").forEach((button) => button.classList.toggle("active", button.dataset.scoMode === mode));
  document.querySelectorAll("#sco-view [data-mode]").forEach((form) => { form.hidden = form.dataset.mode !== mode; });
  scoClearPreview();
}

function scoClearPreview(message = "") {
  scoPreview = null;
  byId("sco-preview").hidden = true;
  byId("sco-message").textContent = message;
  byId("sco-message").className = "message";
  renderSCOCapture();
}

function scoUpdateTargetFields() {
  const group = byId("sco-target-kind").value === "group";
  document.querySelectorAll("#sco-view [data-target]").forEach((field) => { field.hidden = field.dataset.target !== (group ? "group" : "sector"); });
  const target = scoTarget();
  let info;
  if (target.group) {
    const {start, size} = target.group;
    info = (start - 1) % size ? `Gruppenstart muss 1, ${size + 1}, ${2 * size + 1} … sein` : `${scoTargetLabel(target)}`;
  } else {
    info = target.sector >= 1 && target.sector <= 512 ? `Sektorcode ${2 * target.sector - 1}` : "Sektor muss 1–512 sein";
  }
  byId("sco-target-info").textContent = info;
  scoClearPreview();
}

function frameRows(frames) {
  return frames.map(({hex, decoded}) => `<tr><td class="monitor-raw">${escapeHtml(hex)}</td><td>${escapeHtml(decoded.target)}</td><td>${escapeHtml(decoded.command)}</td><td>${escapeHtml(decoded.priority || "—")}${decoded.priority_confirmed === false ? ' <span class="sco-assumed" title="Lage der Priorität bei der Sperre ist noch nicht am Bus bestätigt">Annahme</span>' : ""}</td><td>${escapeHtml(decoded.action || "—")}</td></tr>`).join("");
}

function renderSCOPreview() {
  const box = byId("sco-preview");
  if (!scoPreview) { box.hidden = true; return; }
  const {steps, ga, protectedFrames} = scoPreview;
  box.innerHTML = `${steps.map((step) => `<div class="sco-step"><h3>${escapeHtml(step.label)} → ${escapeHtml(ga)}</h3>
    <table class="monitor-table"><thead><tr><th>Hex</th><th>Ziel</th><th>Befehl</th><th>Priorität</th><th>Aktion</th></tr></thead><tbody>${frameRows(step.frames)}</tbody></table>
    <button type="button" class="${step.key === "set" ? "danger" : "primary"}" data-sco-send="${step.key}" disabled>${escapeHtml(step.button)}</button></div>`).join("")}
    <label class="check sco-confirm"><input id="sco-confirm" type="checkbox" /> Vorschau geprüft${protectedFrames ? " – enthält Sicherheitsbefehle, Aufheben liegt bereit" : ""}</label>`;
  box.hidden = false;
  byId("sco-confirm").addEventListener("change", (event) => {
    box.querySelectorAll("[data-sco-send]").forEach((button) => { button.disabled = !event.target.checked || !busConnected; });
  });
  box.querySelectorAll("[data-sco-send]").forEach((button) => button.addEventListener("click", () => scoSend(button.dataset.scoSend)));
  renderSCOCapture();
}

async function scoBuildPreview() {
  const ga = byId("sco-ga").value.trim();
  const message = byId("sco-message");
  message.className = "message";
  if (!ga) { message.textContent = "Bitte zuerst die Gruppenadresse des SCO-Objekts angeben."; message.className = "message error"; return; }
  const target = scoTarget();
  try {
    let steps;
    if (scoMode === "safety") {
      if (!byId("sco-allow-protected").checked) throw new Error("Sicherheitsbefehle zuerst ausdrücklich freigeben.");
      const pair = await scoApi("/api/sco/safety", {method: "POST", body: JSON.stringify({spec: {
        ...target, priority: byId("sco-safety-priority").value, drive: byId("sco-safety-drive").value,
        position: Number(byId("sco-safety-position").value), lock: byId("sco-lock").value}})});
      steps = [
        {key: "set", label: "Sicherheit setzen", button: "Sicherheit setzen", frames: pair.set},
        {key: "release", label: "Sicherheit aufheben", button: "Sicherheit aufheben", frames: pair.release},
      ];
    } else {
      let spec;
      let allowProtected = false;
      if (scoMode === "drive") spec = {...target, drive: byId("sco-drive").value, position: Number(byId("sco-position").value), priority: byId("sco-priority").value};
      if (scoMode === "operation") spec = {...target, operation: byId("sco-operation").value, local: byId("sco-local").checked};
      if (scoMode === "raw") { spec = {hex: byId("sco-raw").value}; allowProtected = byId("sco-raw-allow-protected").checked; }
      const result = await scoApi("/api/sco/encode", {method: "POST", body: JSON.stringify({spec, allow_protected: allowProtected})});
      steps = [{key: "single", label: "Telegramm", button: "Senden", frames: [result]}];
    }
    const protectedFrames = steps.some((step) => step.frames.some((frame) => frame.decoded.protected));
    scoPreview = {kind: scoMode, ga, target: scoTargetLabel(target), steps, protectedFrames};
    message.textContent = busConnected ? "Vorschau prüfen, bestätigen und senden." : "Vorschau erstellt. Zum Senden zuerst die KNX-Verbindung aufbauen.";
    renderSCOPreview();
  } catch (error) {
    scoClearPreview();
    message.textContent = error.message;
    message.className = "message error";
  }
}

async function scoSend(stepKey, preview = scoPreview) {
  const step = preview.steps.find((item) => item.key === stepKey);
  const message = byId("sco-message");
  try {
    await scoApi("/api/sco/send", {method: "POST", body: JSON.stringify({
      group_address: preview.ga, frames: step.frames.map((frame) => frame.hex), label: step.label,
      confirmed: true, allow_protected: step.frames.some((frame) => frame.decoded.protected)})});
    const lockKey = `${preview.ga}|${preview.target}`;
    if (stepKey === "set") {
      const release = preview.steps.find((item) => item.key === "release");
      scoLocks.set(lockKey, {ga: preview.ga, target: preview.target, label: step.frames[1]?.decoded.action || "Sperre", release});
    }
    if (stepKey === "release") scoLocks.delete(lockKey);
    renderSCOLocks();
    message.textContent = `${step.label}: ${step.frames.length} Telegramm(e) an ${preview.ga} gesendet.`;
    message.className = "message";
  } catch (error) {
    message.textContent = `Senden fehlgeschlagen: ${error.message}`;
    message.className = "message error";
  }
}

function renderSCOLocks() {
  const banner = byId("sco-lock-banner");
  if (!scoLocks.size) { banner.hidden = true; banner.innerHTML = ""; return; }
  banner.innerHTML = `<strong>⚠ Von hier gesetzte Sicherheit aktiv:</strong>${[...scoLocks.entries()].map(([key, lock]) => `<span>${escapeHtml(lock.target)} auf ${escapeHtml(lock.ga)} · ${escapeHtml(lock.label)} <button type="button" class="primary" data-sco-release="${escapeHtml(key)}">Aufheben</button></span>`).join("")}`;
  banner.hidden = false;
  banner.querySelectorAll("[data-sco-release]").forEach((button) => button.addEventListener("click", () => {
    const lock = scoLocks.get(button.dataset.scoRelease);
    if (lock) scoSend("release", {ga: lock.ga, target: lock.target, steps: [lock.release]});
  }));
  document.querySelector("#sco-nav")?.classList.toggle("sco-locked", scoLocks.size > 0);
}

function scoTime(value) {
  const date = new Date(value);
  const pad = (number, size = 2) => String(number).padStart(size, "0");
  return `${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}.${pad(date.getMilliseconds(), 3)}`;
}

function scoPreviewHexes() {
  return new Set((scoPreview?.steps || []).flatMap((step) => step.frames.map((frame) => frame.hex)));
}

function renderSCOCapture() {
  const body = byId("sco-body");
  if (!body) return;
  const rows = monitorTelegrams.filter((telegram) => telegram.sco).slice(0, 500);
  const preview = scoPreviewHexes();
  if (!rows.length) {
    body.innerHTML = `<tr class="monitor-empty"><td colspan="10">${monitorRunning ? "Noch keine SCO-Telegramme empfangen." : "Verbinden und Mitschnitt starten."}</td></tr>`;
    return;
  }
  body.innerHTML = rows.map((telegram) => {
    const decoded = telegram.sco;
    const time = scoTime(telegram.time);
    const match = preview.has(decoded.hex) ? '<br><span class="sco-match">= Vorschau</span>' : "";
    const origin = telegram.origin ? escapeHtml(telegram.origin) : "Bus";
    const ga = telegram.group_name ? `${telegram.destination} · ${telegram.group_name}` : telegram.destination;
    return `<tr class="${decoded.protected ? "sco-protected" : ""}"><td>${escapeHtml(time)}</td><td>${origin}${match}</td><td>${escapeHtml(telegram.source)}</td><td>${escapeHtml(ga)}</td><td>${escapeHtml(decoded.target)}</td><td>${escapeHtml(decoded.command)}</td><td>${escapeHtml(decoded.priority || "—")}${decoded.priority_confirmed === false ? ' <span class="sco-assumed">Annahme</span>' : ""}</td><td>${escapeHtml(decoded.action || "—")}</td><td class="monitor-raw">${escapeHtml(decoded.hex)}</td><td><button type="button" class="secondary compact" data-sco-replay="${escapeHtml(decoded.hex)}" data-sco-ga="${escapeHtml(telegram.destination)}">Übernehmen</button></td></tr>`;
  }).join("");
}

async function renderSCOSectors() {
  try {
    const {sectors} = await scoApi("/api/sco/sectors");
    const summary = (counts) => Object.entries(counts).map(([name, count]) => `${escapeHtml(name)} ×${count}`).join(", ") || "—";
    byId("sco-sectors-body").innerHTML = sectors.length ? sectors.map((row) => `<tr><td>${escapeHtml(row.group_name ? `${row.group_address} · ${row.group_name}` : row.group_address)}</td><td>${escapeHtml(row.target)}</td><td>${escapeHtml(row.source)}</td><td>${row.count}</td><td>${summary(row.commands)}</td><td>${summary(row.priorities)}</td><td>${escapeHtml(row.last_action)}</td></tr>`).join("") : '<tr class="monitor-empty"><td colspan="7">Noch keine SCO-Telegramme.</td></tr>';
  } catch (error) {
    byId("sco-sectors-body").innerHTML = `<tr class="monitor-empty"><td colspan="7">${escapeHtml(error.message)}</td></tr>`;
  }
}

async function loadSCOAddresses() {
  try {
    const {addresses, suggestions} = await scoApi("/api/sco/addresses");
    const options = new Map(suggestions.map((row) => [row.address, row.name]));
    addresses.forEach((address) => { if (!options.has(address)) options.set(address, "markiert"); });
    byId("sco-ga-list").innerHTML = [...options.entries()].map(([address, name]) => `<option value="${escapeHtml(address)}">${escapeHtml(name || "")}</option>`).join("");
    byId("sco-suggestions").innerHTML = suggestions.length
      ? `<small>Aus dem ETS-Projekt vorgeschlagen:</small> ${suggestions.slice(0, 12).map((row) => `<button type="button" class="secondary compact" data-sco-pick="${escapeHtml(row.address)}">${escapeHtml(row.address)} · ${escapeHtml(row.name)}</button>`).join("")}`
      : "<small>Keine SCO-Adressen im ETS-Projekt erkannt. Gruppenadresse von Hand eintragen.</small>";
  } catch {
    byId("sco-suggestions").innerHTML = "";
  }
}

function updateSCOBadges() {
  const connection = byId("sco-connection-badge");
  connection.className = busConnected ? "badge good" : "badge muted";
  connection.textContent = busConnected ? "● Verbunden" : "○ Nicht verbunden";
  const monitor = byId("sco-monitor-badge");
  monitor.className = monitorRunning ? "badge good" : "badge muted";
  monitor.textContent = monitorRunning ? "● Mitschnitt aktiv" : "○ Mitschnitt aus";
  byId("sco-monitor-button").textContent = monitorRunning ? "Mitschnitt stoppen" : "Mitschnitt starten";
  byId("sco-monitor-button").className = monitorRunning ? "danger" : "primary";
  byId("sco-preview").querySelectorAll("[data-sco-send]").forEach((button) => { button.disabled = !busConnected || !byId("sco-confirm")?.checked; });
}

// In den bestehenden Ablauf einklinken: neue Telegramme und Ansichtswechsel
const addTelegramWithoutSCO = addTelegram;
addTelegram = function addTelegramWithSCO(telegram) {
  addTelegramWithoutSCO(telegram);
  if (!byId("sco-view").hidden) renderSCOCapture();
};
const showViewWithoutSCO = showView;
showView = function showViewWithSCO(view) {
  showViewWithoutSCO(view);
  if (view === "sco") { loadSCOAddresses(); renderSCOCapture(); renderSCOSectors(); updateSCOBadges(); }
};
// Der Navigations-Handler aus app.js ruft showView zur Laufzeit auf und nutzt damit diese Erweiterung.

document.querySelectorAll("[data-sco-mode]").forEach((button) => button.addEventListener("click", () => scoSetMode(button.dataset.scoMode)));
["sco-target-kind", "sco-sector", "sco-group-start", "sco-group-size"].forEach((id) => byId(id).addEventListener("input", scoUpdateTargetFields));
document.querySelectorAll('#sco-view [data-mode] select, #sco-view [data-mode] input, #sco-ga').forEach((field) => field.addEventListener("input", () => scoClearPreview()));
byId("sco-preview-button").addEventListener("click", scoBuildPreview);
byId("sco-monitor-button").addEventListener("click", () => { if (monitorRunning) stopMonitor(); else startMonitor(); setTimeout(updateSCOBadges, 400); });
byId("sco-clear-button").addEventListener("click", async () => {
  await scoApi("/api/sco/log", {method: "DELETE"}).catch(() => {});
  monitorTelegrams = monitorTelegrams.filter((telegram) => !telegram.sco);
  renderSCOCapture(); renderSCOSectors();
});
byId("sco-sectors-button").addEventListener("click", renderSCOSectors);
byId("sco-view").addEventListener("click", (event) => {
  const replay = event.target.closest("[data-sco-replay]");
  if (replay) {
    scoSetMode("raw");
    byId("sco-raw").value = replay.dataset.scoReplay;
    byId("sco-ga").value = replay.dataset.scoGa;
    byId("sco-message").textContent = "Telegramm übernommen. Bei Bedarf den Sektor im Hex anpassen und eine Vorschau erstellen.";
  }
  const pick = event.target.closest("[data-sco-pick]");
  if (pick) { byId("sco-ga").value = pick.dataset.scoPick; scoClearPreview(); }
});
setInterval(() => { if (!byId("sco-view").hidden) updateSCOBadges(); }, 1500);
scoUpdateTargetFields();
