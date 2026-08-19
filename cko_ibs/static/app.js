const byId = (id) => document.getElementById(id);
let projectDevices = [];
let monitorSocket = null;

async function loadAdapters() {
  const select = byId("adapter-select");
  try {
    const response = await fetch("/api/network/adapters");
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || "Adapter konnten nicht gelesen werden");
    data.adapters.forEach((adapter) => {
      const option = document.createElement("option");
      option.value = adapter.ip_address;
      option.textContent = `${adapter.name} · ${adapter.ip_address}`;
      select.appendChild(option);
    });
  } catch (error) {
    byId("connection-message").className = "message error";
    byId("connection-message").textContent = error.message;
  }
}

loadAdapters();

byId("scan-button").addEventListener("click", async () => {
  const button = byId("scan-button");
  const list = byId("gateway-list");
  button.disabled = true;
  button.textContent = "Suche läuft …";
  list.className = "empty-state";
  list.innerHTML = "<span>◌</span><strong>KNX/IP-Netzwerk wird durchsucht</strong><small>Dies dauert ungefähr drei Sekunden.</small>";
  try {
    const localIp = byId("adapter-select").value;
    const query = localIp ? `?local_ip=${encodeURIComponent(localIp)}` : "";
    const response = await fetch(`/api/knx/gateways${query}`);
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || "Suche fehlgeschlagen");
    if (!data.gateways.length) {
      list.innerHTML = "<span>⌁</span><strong>Keine KNX/IP-Schnittstelle gefunden</strong><small>Netzwerk, Firewall und lokalen Adapter prüfen.</small>";
    } else {
      list.className = "";
      list.innerHTML = data.gateways.map((g) => `
        <div class="gateway">
          <div><strong>${escapeHtml(g.name)}</strong><small>${escapeHtml(g.ip_address)}:${g.port} · ${escapeHtml(g.individual_address || "keine KNX-Adresse")}</small></div>
          <div class="gateway-tags">${g.supports_tunnelling ? "<span>TUNNEL</span>" : ""}${g.supports_routing ? "<span>ROUTING</span>" : ""}${g.supports_secure ? "<span>SECURE</span>" : ""}</div>
        </div>`).join("");
      byId("connection-badge").className = "badge info";
      byId("connection-badge").textContent = `● ${data.count} KNX/IP gefunden`;
    }
  } catch (error) {
    list.innerHTML = `<span>!</span><strong>Suche fehlgeschlagen</strong><small>${escapeHtml(error.message)}</small>`;
  } finally {
    button.disabled = false;
    button.textContent = "⟳ Netzwerk scannen";
  }
});

byId("test-connection-button").addEventListener("click", async () => {
  const button = byId("test-connection-button");
  const message = byId("connection-message");
  const gatewayIp = byId("gateway-ip").value.trim();
  if (!gatewayIp) {
    message.className = "message error";
    message.textContent = "Bitte zuerst die IP-Adresse der KNX/IP-Schnittstelle eingeben.";
    return;
  }
  button.disabled = true;
  message.className = "message";
  message.textContent = `Verbindung zu ${gatewayIp} wird geprüft …`;
  try {
    const response = await fetch("/api/knx/test-connection", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({gateway_ip: gatewayIp, local_ip: byId("adapter-select").value || null, mode: byId("connection-mode").value}),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || "Verbindung fehlgeschlagen");
    message.className = "message success";
    message.textContent = data.message;
    byId("connection-badge").className = "badge good";
    byId("connection-badge").textContent = `✓ ${data.connection_mode} · ${data.gateway_ip}`;
    byId("device-scan-button").disabled = projectDevices.length === 0;
    startMonitor();
  } catch (error) {
    message.className = "message error";
    message.textContent = error.message;
    byId("connection-badge").className = "badge muted";
    byId("connection-badge").textContent = "○ Nicht verbunden";
  } finally {
    button.disabled = false;
  }
});

byId("project-file").addEventListener("change", (event) => {
  const file = event.target.files[0];
  if (file) document.querySelector(".dropzone strong").textContent = file.name;
});

byId("project-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const message = byId("project-message");
  const form = new FormData(event.target);
  message.className = "message";
  message.textContent = "ETS-Projekt wird ausschließlich lokal analysiert …";
  try {
    const response = await fetch("/api/project/import", { method: "POST", body: form });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || "Import fehlgeschlagen");
    const project = data.project;
    projectDevices = project.devices;
    byId("project-name").textContent = project.name;
    byId("project-badge").className = "badge good";
    byId("project-badge").textContent = "✓ ETS-Projekt geladen";
    byId("device-count").textContent = project.device_count;
    byId("open-count").textContent = project.device_count;
    message.textContent = `${project.device_count} Geräte und ${project.group_address_count} Gruppenadressen eingelesen.`;
    const topology = byId("topology");
    renderTopology(project.topology, project.devices);
    byId("device-scan-button").disabled = projectDevices.length === 0 || byId("connection-badge").classList.contains("muted");
  } catch (error) {
    message.className = "message error";
    message.textContent = error.message;
  }
});

byId("device-scan-button").addEventListener("click", async () => {
  const button = byId("device-scan-button");
  const progress = byId("scan-progress");
  const bar = progress.querySelector("span");
  const label = progress.querySelector("small");
  let onlineCount = 0;
  let errorCount = 0;
  button.disabled = true;
  progress.hidden = false;
  bar.style.width = "0";
  for (let index = 0; index < projectDevices.length; index += 1) {
    const device = projectDevices[index];
    const card = document.querySelector(`.device-card[data-address="${CSS.escape(device.address)}"]`);
    card.className = "device-card checking";
    card.querySelector("small").textContent = "◌ Prüfung läuft …";
    try {
      const response = await fetch("/api/knx/check-device", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({address: device.address}),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || "Prüfung fehlgeschlagen");
      card.className = `device-card ${data.online ? "online" : "offline"}`;
      card.querySelector("small").textContent = data.online ? "✓ Gerät antwortet" : "× Keine Antwort";
      if (data.online) onlineCount += 1; else errorCount += 1;
    } catch (error) {
      card.className = "device-card offline";
      card.querySelector("small").textContent = `× ${error.message}`;
      errorCount += 1;
    }
    const completed = index + 1;
    bar.style.width = `${(completed / projectDevices.length) * 100}%`;
    label.textContent = `${completed} / ${projectDevices.length} Geräte geprüft`;
    document.querySelector(".stats article:nth-child(2) strong").textContent = onlineCount;
    document.querySelector(".stats article:nth-child(3) strong").textContent = errorCount;
    byId("open-count").textContent = projectDevices.length - completed;
    updateTopologySummaries();
  }
  button.disabled = false;
  button.textContent = "Geräte erneut prüfen";
});

function renderTopology(areas, fallbackDevices) {
  const topology = byId("topology");
  const source = areas?.length ? areas : [{address: "—", name: "Nicht zugeordnete Geräte", lines: [{address: "—", name: "Geräte", medium: "Unbekannt", devices: fallbackDevices}]}];
  topology.className = "topology-tree";
  topology.innerHTML = source.map((area, areaIndex) => {
    const areaCount = area.lines.reduce((sum, line) => sum + line.devices.length, 0);
    return `<details class="topology-area" ${areaIndex === 0 ? "open" : ""}>
      <summary class="area-header">
        <span class="node-icon area-icon">A</span>
        <span class="node-copy"><small>BEREICH ${escapeHtml(area.address)}</small><strong>${escapeHtml(area.name)}</strong></span>
        <span class="node-summary" data-scope="area">${areaCount} Geräte · ${area.lines.length} Linien</span>
        <span class="chevron">⌄</span>
      </summary>
      <div class="area-content">${area.lines.map((line, lineIndex) => `<details class="topology-line" ${areaIndex === 0 && lineIndex === 0 ? "open" : ""}>
        <summary class="line-header">
          <span class="node-icon line-icon">L</span>
          <span class="node-copy"><small>LINIE ${escapeHtml(line.address)} · ${escapeHtml(line.medium)}</small><strong>${escapeHtml(line.name)}</strong></span>
          <span class="node-summary" data-scope="line">${line.devices.length} Geräte · 0 OK · 0 Fehler</span>
          <span class="line-status unchecked">ungeprüft</span>
          <span class="chevron">⌄</span>
        </summary>
        <div class="line-devices">${line.devices.map(deviceCard).join("") || '<p class="empty-line">Keine Geräte in dieser Linie</p>'}</div>
      </details>`).join("") || '<p class="empty-line">Keine Linien in diesem Bereich</p>'}</div>
    </details>`;
  }).join("");
}

function deviceCard(device) {
  return `<div class="device-card" data-address="${escapeHtml(device.address)}">
    <span class="device-dot"></span><div><strong>${escapeHtml(device.address)}</strong><span>${escapeHtml(device.name)}</span><small>○ Noch nicht geprüft</small></div>
  </div>`;
}

function updateTopologySummaries() {
  document.querySelectorAll(".topology-line").forEach((line) => {
    const cards = [...line.querySelectorAll(".device-card")];
    const ok = cards.filter((card) => card.classList.contains("online")).length;
    const errors = cards.filter((card) => card.classList.contains("offline")).length;
    const open = cards.length - ok - errors;
    line.querySelector('[data-scope="line"]').textContent = `${cards.length} Geräte · ${ok} OK · ${errors} Fehler · ${open} offen`;
    const status = line.querySelector(".line-status");
    status.className = `line-status ${errors ? "error" : open ? "unchecked" : "ok"}`;
    status.textContent = errors ? "Fehler" : open ? "offen" : "OK";
  });
  document.querySelectorAll(".topology-area").forEach((area) => {
    const cards = [...area.querySelectorAll(".device-card")];
    const ok = cards.filter((card) => card.classList.contains("online")).length;
    const errors = cards.filter((card) => card.classList.contains("offline")).length;
    const open = cards.length - ok - errors;
    area.querySelector('[data-scope="area"]').textContent = `${cards.length} Geräte · ${ok} OK · ${errors} Fehler · ${open} offen`;
  });
}

function startMonitor() {
  if (monitorSocket) monitorSocket.close();
  const protocol = location.protocol === "https:" ? "wss" : "ws";
  monitorSocket = new WebSocket(`${protocol}://${location.host}/api/knx/monitor`);
  monitorSocket.onopen = () => {
    byId("monitor-state").className = "badge good";
    byId("monitor-state").textContent = "● Monitor aktiv";
  };
  monitorSocket.onmessage = (event) => addTelegram(JSON.parse(event.data));
  monitorSocket.onclose = () => {
    byId("monitor-state").className = "badge muted";
    byId("monitor-state").textContent = "○ Monitor getrennt";
  };
}

function addTelegram(telegram) {
  const body = byId("monitor-body");
  body.querySelector(".monitor-empty")?.remove();
  const row = document.createElement("tr");
  const time = new Date(telegram.time).toLocaleTimeString("de-CH", {hour12: false, fractionalSecondDigits: 3});
  const destination = telegram.group_name ? `${telegram.destination} · ${telegram.group_name}` : telegram.destination;
  row.innerHTML = `<td>${escapeHtml(time)}</td><td>${escapeHtml(telegram.source)}</td><td>${escapeHtml(destination)}</td><td>${escapeHtml(telegram.dpt || "—")}</td><td>${escapeHtml(telegram.service)}${telegram.secure ? " · Secure" : ""}</td><td class="monitor-value">${escapeHtml(telegram.value || "—")}</td><td class="monitor-raw">${escapeHtml(telegram.raw || "—")}</td>`;
  body.prepend(row);
  while (body.rows.length > 250) body.deleteRow(-1);
}

byId("clear-monitor-button").addEventListener("click", () => {
  byId("monitor-body").innerHTML = '<tr class="monitor-empty"><td colspan="7">Anzeige geleert. Neue Telegramme erscheinen automatisch.</td></tr>';
});

byId("shutdown-button").addEventListener("click", () => byId("shutdown-dialog").showModal());

byId("shutdown-dialog").addEventListener("close", async () => {
  if (byId("shutdown-dialog").returnValue !== "confirm") return;
  const button = byId("confirm-shutdown-button");
  button.disabled = true;
  button.textContent = "Verbindung wird getrennt …";
  try {
    const response = await fetch("/api/application/shutdown", {method: "POST"});
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || "Anwendung konnte nicht beendet werden");
    if (monitorSocket) monitorSocket.close();
    document.body.innerHTML = `<main class="shutdown-screen"><div><span>✓</span><h1>Anwendung beendet</h1><p>${escapeHtml(data.message)}</p><small>Dieses Browserfenster kann jetzt geschlossen werden.</small><button onclick="window.close()">Fenster schließen</button></div></main>`;
    setTimeout(() => window.close(), 800);
  } catch (error) {
    button.disabled = false;
    button.textContent = "KNX trennen & beenden";
    alert(error.message);
  }
});

function escapeHtml(value) {
  return String(value).replace(/[&<>'"]/g, (char) => ({"&":"&amp;","<":"&lt;",">":"&gt;","'":"&#39;",'"':"&quot;"})[char]);
}
