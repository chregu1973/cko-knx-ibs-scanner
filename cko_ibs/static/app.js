const byId = (id) => document.getElementById(id);
let projectDevices = [];
let monitorSocket = null;
let topologyFlowResizeBound = false;
let cancelAddressScan = false;
let busConnected = false;
let selectedScanAddress = null;
let monitorTelegrams = [];
let monitorRunning = false;

function deviceLineAddress(device) {
  return String(device.address || "").split(".").slice(0, 2).join(".");
}

function populateDiagnosticLines(topology) {
  const select = byId("diagnostic-line-select");
  const lines = [];
  (topology || []).forEach((area) => (area.lines || []).forEach((line) => {
    const physicalCount = (line.devices || []).filter((device) => device.kind !== "dummy").length;
    if (!physicalCount) return;
    const address = line.full_address || line.address;
    lines.push({address, label: `${address} · ${area.name} / ${line.name}`, count: physicalCount});
  }));
  select.innerHTML = `<option value="">Alle Linien</option>${lines.map((line) => `<option value="${escapeHtml(line.address)}">${escapeHtml(line.label)} (${line.count})</option>`).join("")}`;
  select.disabled = lines.length === 0;
}

function updateGlobalDeviceStats() {
  const cards = [...document.querySelectorAll('.device-card[data-kind="physical"]')];
  const ok = cards.filter((card) => card.classList.contains("online")).length;
  const errors = cards.filter((card) => card.classList.contains("offline")).length;
  const warnings = cards.filter((card) => card.classList.contains("warning")).length;
  const open = cards.length - ok - errors - warnings;
  document.querySelector(".stats article:nth-child(2) strong").textContent = ok;
  document.querySelector(".stats article:nth-child(3) strong").textContent = errors;
  byId("open-count").textContent = open;
}

function showView(view) {
  document.querySelectorAll(".app-view").forEach((element) => {
    element.hidden = element.id !== `${view}-view`;
  });
  document.querySelectorAll(".nav-item[data-view]").forEach((button) => {
    button.classList.toggle("active", button.dataset.view === view);
  });
  if (view === "monitor") renderMonitor();
}

document.querySelectorAll(".nav-item[data-view]").forEach((button) => {
  button.addEventListener("click", () => showView(button.dataset.view));
});

function addressCards(found) {
  return found
    .sort((a, b) => Number(a.split(".")[2]) - Number(b.split(".")[2]))
    .map((address) => `<button class="found-address${address === selectedScanAddress ? " selected" : ""}" type="button" data-address="${escapeHtml(address)}">${escapeHtml(address)}</button>`)
    .join("");
}

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

function updateConnectionMode() {
  const mode = byId("connection-mode").value;
  const usb = mode === "usb";
  byId("secure-controls").hidden = mode !== "tcp_secure";
  byId("usb-controls").hidden = !usb;
  byId("adapter-field").hidden = usb;
  byId("gateway-field").hidden = usb;
  byId("scan-button").textContent = usb ? "⟳ USB suchen" : "⟳ Netzwerk scannen";
  byId("individual-address-label").textContent = usb
    ? "KNX-Quelladresse (empfohlen)"
    : "KNX-Tunneladresse (optional)";
}

byId("connection-mode").addEventListener("change", updateConnectionMode);
updateConnectionMode();

byId("keyring-file").addEventListener("change", (event) => {
  byId("keyring-file-label").textContent = event.target.files[0]?.name || ".knxkeys auswählen";
});

byId("scan-button").addEventListener("click", async () => {
  const button = byId("scan-button");
  const list = byId("gateway-list");
  const usbMode = byId("connection-mode").value === "usb";
  button.disabled = true;
  button.textContent = "Suche läuft …";
  list.className = "empty-state";
  list.innerHTML = usbMode
    ? "<span>USB</span><strong>Lokale USB-Schnittstellen werden gesucht</strong>"
    : "<span>◌</span><strong>KNX/IP-Netzwerk wird durchsucht</strong><small>Dies dauert ungefähr drei Sekunden.</small>";
  try {
    if (usbMode) {
      const response = await fetch("/api/knx/usb-devices");
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || "USB-Suche fehlgeschlagen");
      const select = byId("usb-device-select");
      select.innerHTML = '<option value="">Automatisch auswählen</option>';
      data.devices.forEach((device) => {
        const option = document.createElement("option");
        option.value = device.id;
        option.textContent = `${device.name}${device.serial_number ? ` · ${device.serial_number}` : ""}`;
        select.appendChild(option);
      });
      if (!data.devices.length) {
        list.innerHTML = "<span>USB</span><strong>Keine Siemens OCI702 gefunden</strong><small>USB anschließen und die ETS-Verbindung trennen.</small>";
      } else {
        select.value = data.devices[0].id;
        list.className = "";
        list.innerHTML = data.devices.map((device) => `<div class="gateway usb-device"><div><strong>${escapeHtml(device.name)}</strong><small>VID ${device.vendor_id} · PID ${device.product_id}${device.serial_number ? ` · ${escapeHtml(device.serial_number)}` : ""}</small></div><div class="gateway-tags"><span>USB</span><span>cEMI</span></div></div>`).join("");
      }
      return;
    }
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
    button.textContent = usbMode ? "⟳ USB suchen" : "⟳ Netzwerk scannen";
  }
});

byId("test-connection-button").addEventListener("click", async () => {
  const button = byId("test-connection-button");
  const message = byId("connection-message");
  const gatewayIp = byId("gateway-ip").value.trim();
  const individualAddress = byId("individual-address").value.trim();
  const mode = byId("connection-mode").value;
  if (mode !== "usb" && !gatewayIp) {
    message.className = "message error";
    message.textContent = "Bitte zuerst die IP-Adresse der KNX/IP-Schnittstelle eingeben.";
    return;
  }
  if (individualAddress && projectDevices.some((device) => device.kind !== "dummy" && device.address === individualAddress)) {
    message.className = "message error";
    message.textContent = `${individualAddress} gehört laut ETS bereits zu einem Gerät. Bitte eine freie Tunneladresse verwenden.`;
    return;
  }
  button.disabled = true;
  message.className = "message";
  message.textContent = mode === "usb" ? "Siemens OCI702 wird geöffnet …" : `Verbindung zu ${gatewayIp} wird geprüft …`;
  try {
    let response;
    if (mode === "usb") {
      response = await fetch("/api/knx/connect-usb", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({device_id: byId("usb-device-select").value || null, individual_address: individualAddress || null}),
      });
    } else if (mode === "tcp_secure") {
      const secureForm = new FormData();
      secureForm.append("gateway_ip", gatewayIp);
      if (byId("adapter-select").value) secureForm.append("local_ip", byId("adapter-select").value);
      if (individualAddress) secureForm.append("individual_address", individualAddress);
      const keyring = byId("keyring-file").files[0];
      if (keyring) secureForm.append("keyring", keyring);
      secureForm.append("keyring_password", byId("keyring-password").value || byId("project-password").value);
      if (byId("secure-user-id").value) secureForm.append("user_id", byId("secure-user-id").value);
      if (byId("secure-user-password").value) secureForm.append("user_password", byId("secure-user-password").value);
      if (byId("secure-auth-code").value) secureForm.append("authentication_code", byId("secure-auth-code").value);
      response = await fetch("/api/knx/connect-secure", {method: "POST", body: secureForm});
    } else {
      response = await fetch("/api/knx/test-connection", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({gateway_ip: gatewayIp, local_ip: byId("adapter-select").value || null, mode, individual_address: individualAddress || null}),
      });
    }
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || "Verbindung fehlgeschlagen");
    message.className = "message success";
    message.textContent = `${data.message} Quelladresse: ${data.individual_address || "nicht festgelegt"}.`;
    byId("connection-badge").className = "badge good";
    byId("connection-badge").textContent = `✓ ${data.connection_mode} · KNX ${data.individual_address || "?"}`;
    const diagnostic = byId("tunnel-diagnostic");
    diagnostic.hidden = false;
    diagnostic.className = `tunnel-diagnostic${data.address_warning ? " warning" : ""}`;
    diagnostic.innerHTML = data.address_warning
      ? `<strong>⚠ Tunneladress-Warnung:</strong> ${escapeHtml(data.address_warning)}`
      : mode === "usb"
        ? `<strong>✓ ${escapeHtml(data.usb_device?.name || "KNX USB")} aktiv</strong><br>KNX-Quelladresse: ${escapeHtml(data.individual_address || "0.0.0")}. Für linienübergreifende Prüfungen eine freie Adresse aus der passenden Topologie verwenden.`
        : `<strong>✓ Aktive KNX-Quelladresse: ${escapeHtml(data.individual_address || "nicht ermittelt")}</strong><br>Diese Adresse wurde vom KNX/IP-Tunnel bestätigt. Für parallele Verbindungen muss jede Tunneladresse eindeutig sein.`;
    byId("device-scan-button").disabled = projectDevices.length === 0;
    busConnected = true;
    updateMonitorConnectionBadge();
    byId("disconnect-button").hidden = false;
    startMonitor();
  } catch (error) {
    message.className = "message error";
    message.textContent = error.message;
    byId("connection-badge").className = "badge muted";
    byId("connection-badge").textContent = "○ Nicht verbunden";
    byId("tunnel-diagnostic").hidden = true;
    busConnected = false;
    updateMonitorConnectionBadge();
    byId("disconnect-button").hidden = true;
  } finally {
    button.disabled = false;
  }
});

byId("disconnect-button").addEventListener("click", async () => {
  const button = byId("disconnect-button");
  button.disabled = true;
  button.textContent = "Verbindung wird getrennt …";
  cancelAddressScan = true;
  try {
    const response = await fetch("/api/knx/disconnect", {method: "POST"});
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || "Verbindung konnte nicht getrennt werden");
    if (monitorSocket) {
      monitorSocket.close();
      monitorSocket = null;
    }
    busConnected = false;
    updateMonitorConnectionBadge();
    byId("connection-badge").className = "badge muted";
    byId("connection-badge").textContent = "○ Nicht verbunden";
    byId("connection-message").className = "message success";
    byId("connection-message").textContent = "KNX-Verbindung und Telegrammmonitor wurden sauber getrennt.";
    byId("tunnel-diagnostic").hidden = true;
    byId("device-scan-button").disabled = true;
    button.hidden = true;
  } catch (error) {
    byId("connection-message").className = "message error";
    byId("connection-message").textContent = error.message;
  } finally {
    button.disabled = false;
    button.textContent = "Verbindung trennen";
  }
});

byId("address-scan-button").addEventListener("click", async () => {
  const button = byId("address-scan-button");
  if (button.dataset.running === "true") {
    cancelAddressScan = true;
    button.textContent = "Scan wird beendet …";
    button.disabled = true;
    return;
  }
  const results = byId("address-scan-results");
  if (!busConnected) {
    results.className = "address-scan-results empty";
    results.innerHTML = "<span>Bitte zuerst eine KNX/IP- oder KNX-USB-Verbindung aufbauen.</span>";
    return;
  }
  const area = Number(byId("scan-area").value);
  const line = Number(byId("scan-line").value);
  const first = Number(byId("scan-device-from").value);
  const last = Number(byId("scan-device-to").value);
  if (area < 0 || area > 15 || line < 0 || line > 15 || first < 1 || last > 255 || first > last) {
    results.className = "address-scan-results empty";
    results.innerHTML = "<span>Ungültiger Adressbereich. Bereich/Linie 0–15, Geräte 1–255.</span>";
    return;
  }

  const addresses = Array.from({length: last - first + 1}, (_, index) => `${area}.${line}.${first + index}`);
  const sourceAddress = byId("individual-address").value.trim();
  const scanAddresses = addresses.filter((address) => address !== sourceAddress);
  if (!scanAddresses.length) {
    results.className = "address-scan-results empty";
    results.innerHTML = "<span>Der Scanbereich enthält ausschließlich die eigene Quelladresse.</span>";
    return;
  }
  const found = [];
  const progress = byId("address-scan-progress");
  const bar = progress.querySelector("span");
  const label = progress.querySelector("small");
  let completed = 0;
  cancelAddressScan = false;
  button.dataset.running = "true";
  button.textContent = "Scan abbrechen";
  progress.hidden = false;
  bar.style.width = "0";
  results.className = "address-scan-results";
  results.innerHTML = '<div class="scan-summary">Scan läuft … Je nach Adressbereich und Busantwort kann dies einige Minuten dauern.</div>';

  const checkAddress = async (address) => {
    try {
      const response = await fetch("/api/knx/check-device", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({address}),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || "Prüfung fehlgeschlagen");
      if (data.online) found.push(address);
    } catch (error) {
      if (!cancelAddressScan) console.warn(`Adressprüfung ${address}:`, error);
    } finally {
      completed += 1;
      bar.style.width = `${(completed / scanAddresses.length) * 100}%`;
      label.textContent = `${completed} / ${scanAddresses.length} Adressen geprüft · ${found.length} gefunden`;
      const cards = addressCards(found);
      results.innerHTML = `<div class="scan-summary">Linie ${area}.${line} · ${completed}/${scanAddresses.length} geprüft · ${found.length} belegt${sourceAddress ? ` · eigene Quelladresse ${escapeHtml(sourceAddress)} ausgelassen` : ""}</div>${cards}`;
    }
  };

  for (let index = 0; index < scanAddresses.length && !cancelAddressScan; index += 4) {
    await Promise.all(scanAddresses.slice(index, index + 4).map(checkAddress));
  }
  button.dataset.running = "false";
  button.disabled = false;
  button.textContent = "Linie erneut scannen";
  const state = cancelAddressScan ? "abgebrochen" : "abgeschlossen";
  const cards = addressCards(found);
  results.innerHTML = `<div class="scan-summary">Scan ${state}: Linie ${area}.${line} · ${completed} geprüft · ${found.length} belegte Adressen gefunden${sourceAddress ? ` · eigene Quelladresse ${escapeHtml(sourceAddress)} ausgelassen` : ""}</div>${cards || '<span>Keine antwortende Adresse gefunden.</span>'}`;
});

byId("address-scan-results").addEventListener("click", (event) => {
  const card = event.target.closest(".found-address");
  if (!card) return;
  selectedScanAddress = card.dataset.address;
  document.querySelectorAll(".found-address").forEach((item) => item.classList.toggle("selected", item === card));
  byId("device-info-panel").hidden = false;
  byId("device-info-address").textContent = selectedScanAddress;
  byId("device-info-result").className = "device-info-empty";
  byId("device-info-result").textContent = "Gerätedaten wurden noch nicht gelesen.";
});

byId("read-device-info-button").addEventListener("click", async () => {
  if (!selectedScanAddress) return;
  const button = byId("read-device-info-button");
  const result = byId("device-info-result");
  button.disabled = true;
  button.textContent = "Gerät wird gelesen …";
  result.className = "device-info-empty";
  result.textContent = "Standardisierte KNX-Geräteinformationen werden abgefragt …";
  try {
    const response = await fetch("/api/knx/device-info", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({address: selectedScanAddress}),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || "Gerätedaten konnten nicht gelesen werden");
    const fields = [
      ["Maskenversion", data.mask_version],
      ["Hersteller", data.manufacturer_name || data.manufacturer_id],
      ["Hersteller-ID", data.manufacturer_name ? data.manufacturer_id : null],
      ["Seriennummer", data.serial_number],
      ["Firmware", data.firmware_revision],
      ["Programmversion (Rohwert)", data.program_version],
      ["Bestellinformation", data.order_info],
      ["Objektname", data.object_name],
      ["PEI-Typ", data.pei_type],
    ].filter(([, value]) => value);
    result.className = "device-info-content";
    result.innerHTML = `<div class="device-info-summary"><span class="device-info-quality ${escapeHtml(data.quality)}">${escapeHtml(data.quality_label)}</span><small>Angaben stammen direkt aus den vom Gerät unterstützten KNX-Managementdiensten.</small></div><div class="device-info-grid">${fields.map(([label, value]) => `<div class="device-info-item"><small>${escapeHtml(label)}</small><strong>${escapeHtml(String(value))}</strong></div>`).join("")}</div>`;
  } catch (error) {
    result.className = "message error";
    result.textContent = error.message;
  } finally {
    button.disabled = false;
    button.textContent = "Gerätedaten erneut lesen";
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
    document.querySelector(".stats article:nth-child(2) strong").textContent = "0";
    document.querySelector(".stats article:nth-child(3) strong").textContent = "0";
    byId("open-count").textContent = project.devices.filter((device) => device.kind !== "dummy").length;
    message.textContent = `${project.device_count} Geräte und ${project.group_address_count} Gruppenadressen eingelesen.`;
    const topology = byId("topology");
    renderTopology(project.topology, project.devices);
    populateDiagnosticLines(project.topology);
    updateTopologySummaries();
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
  button.disabled = true;
  progress.hidden = false;
  bar.style.width = "0";
  const selectedLine = byId("diagnostic-line-select").value;
  const checkableDevices = projectDevices.filter((device) => device.kind !== "dummy" && (!selectedLine || deviceLineAddress(device) === selectedLine));
  if (!checkableDevices.length) {
    button.disabled = false;
    progress.hidden = true;
    window.alert("In der ausgewählten Linie sind keine prüfbaren Geräte vorhanden.");
    return;
  }
  const diagnosticDevice = checkableDevices.find((device) => device.test_policy === "normal") || checkableDevices[0];
  if (diagnosticDevice) {
    button.textContent = `Diagnosetest ${diagnosticDevice.address} …`;
    try {
      const diagnosticResponse = await fetch("/api/knx/check-device", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({address: diagnosticDevice.address}),
      });
      const diagnosticData = await diagnosticResponse.json();
      if (!diagnosticResponse.ok) throw new Error(diagnosticData.detail || "Diagnosetest fehlgeschlagen");
      if (!diagnosticData.online) {
        const proceed = window.confirm(
          `Der Diagnosetest mit ${diagnosticDevice.address} erhielt keine Antwort.\n\n` +
          "Mögliche Ursache: ungeeignete oder bereits belegte KNX-Tunneladresse, Linienkoppler oder VPN-Routing.\n\n" +
          `Trotzdem ${selectedLine ? `die Linie ${selectedLine}` : "alle Linien"} prüfen?`
        );
        if (!proceed) {
          button.disabled = false;
          button.textContent = "Geräte prüfen";
          progress.hidden = true;
          return;
        }
      }
    } catch (error) {
      window.alert(`Diagnosetest abgebrochen: ${error.message}`);
      button.disabled = false;
      button.textContent = "Geräte prüfen";
      progress.hidden = true;
      return;
    }
  }
  document.querySelectorAll('.device-card[data-kind="dummy"]').forEach((card) => {
    card.className = "device-card info-device";
    card.querySelector("small").textContent = "ℹ Info · virtuelles ETS-Gerät";
  });
  for (let index = 0; index < checkableDevices.length; index += 1) {
    const device = checkableDevices[index];
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
      const rfPlusWarning = !data.online && device.test_policy === "rf_plus";
      card.className = `device-card ${data.online ? "online" : rfPlusWarning ? "warning" : "offline"}`;
      card.querySelector("small").textContent = data.online
        ? "✓ Gerät antwortet"
        : rfPlusWarning
          ? "⚠ RF+ · Antwort abhängig von Geräteversorgung"
          : "× Keine Antwort";
    } catch (error) {
      card.className = "device-card offline";
      card.querySelector("small").textContent = `× ${error.message}`;
    }
    const completed = index + 1;
    bar.style.width = `${(completed / checkableDevices.length) * 100}%`;
    label.textContent = `${completed} / ${checkableDevices.length} physische Geräte geprüft`;
    updateGlobalDeviceStats();
    updateTopologySummaries();
  }
  button.disabled = false;
  button.textContent = selectedLine ? `Linie ${selectedLine} erneut prüfen` : "Geräte erneut prüfen";
});

byId("diagnostic-line-select").addEventListener("change", (event) => {
  const line = event.target.value;
  byId("device-scan-button").textContent = line ? `Linie ${line} prüfen` : "Alle Linien prüfen";
});

function renderTopology(areas, fallbackDevices) {
  const topology = byId("topology");
  const source = areas?.length ? areas : [{address: "—", name: "Nicht zugeordnete Geräte", lines: [{address: "—", name: "Geräte", medium: "Unbekannt", devices: fallbackDevices}]}];
  const detailAreas = source.filter((area) => String(area.address) !== "0");
  topology.className = "topology-layout";
  topology.innerHTML = `<div class="topology-map"><svg class="map-flow-layer" aria-hidden="true"></svg>${renderTopologyMap(source)}</div><div class="topology-tree">${detailAreas.map((area, areaIndex) => {
    const areaCount = area.lines.reduce((sum, line) => sum + line.devices.length, 0);
    const mainLine = area.lines.find((line) => line.role === "main");
    const areaMedium = mediumKind(mainLine?.medium);
    return `<details class="topology-area" ${areaIndex === 0 ? "open" : ""}>
      <summary class="area-header">
        <span class="node-icon area-icon medium-symbol ${areaMedium}">${mediumIcon(areaMedium)}</span>
        <span class="node-copy"><small>BEREICH ${escapeHtml(area.address)}</small><strong>${escapeHtml(area.name)}</strong></span>
        <span class="node-summary" data-scope="area">${areaCount} Geräte · ${area.lines.length} Linien</span>
        <span class="chevron">⌄</span>
      </summary>
      <div class="area-content">${area.lines.map((line, lineIndex) => `<details class="topology-line" data-line-address="${escapeHtml(line.full_address)}" ${areaIndex === 0 && lineIndex === 0 ? "open" : ""}>
        <summary class="line-header">
          <span class="node-icon line-icon medium-symbol ${mediumKind(line.medium)}">${mediumIcon(mediumKind(line.medium))}</span>
          <span class="node-copy"><small>LINIE ${escapeHtml(line.full_address)} · ${escapeHtml(line.medium)}</small><strong>${escapeHtml(line.name)}</strong></span>
          <span class="node-summary" data-scope="line">${line.devices.length} Geräte · 0 OK · 0 Fehler</span>
          <span class="line-status unchecked">ungeprüft</span>
          <span class="chevron">⌄</span>
        </summary>
        <div class="line-devices">${renderLineDevices(line)}</div>
      </details>`).join("") || '<p class="empty-line">Keine Linien in diesem Bereich</p>'}</div>
    </details>`;
  }).join("")}</div>`;
  topology.querySelectorAll(".map-line-node,.map-area-node").forEach((button) => button.addEventListener("click", () => {
    const detail = topology.querySelector(`.topology-line[data-line-address="${CSS.escape(button.dataset.lineAddress)}"]`);
    if (!detail) return;
    detail.closest(".topology-area").open = true;
    detail.open = true;
    detail.scrollIntoView({behavior: "smooth", block: "center"});
  }));
  requestAnimationFrame(drawTopologyFlows);
  if (!topologyFlowResizeBound) {
    window.addEventListener("resize", drawTopologyFlows);
    topologyFlowResizeBound = true;
  }
}

function renderTopologyMap(areas) {
  const backboneArea = areas.find((area) => String(area.address) === "0");
  const backbone = backboneArea?.lines.find((line) => line.role === "backbone");
  const installationAreas = areas.filter((area) => String(area.address) !== "0");
  const backboneMedium = mediumKind(backbone?.medium || "IP");
  return `<div class="map-root" data-line-address="${escapeHtml(backbone?.full_address || "0.0")}"><span class="medium-symbol ${backboneMedium}">${mediumIcon(backboneMedium)}</span><strong>IP-Backbone</strong><small>${escapeHtml(backbone?.full_address || "0.0")} · ${escapeHtml(backbone?.medium || "IP")}</small></div>
    <div class="map-areas">${installationAreas.map((area) => {
      const mainLine = area.lines.find((line) => line.role === "main");
      const subLines = area.lines.filter((line) => line.role === "subline");
      const count = area.lines.reduce((sum, line) => sum + line.devices.length, 0);
      const mainMedium = mediumKind(mainLine?.medium);
      return `<div class="map-area"><button class="map-area-node" type="button" data-line-address="${escapeHtml(mainLine?.full_address || `${area.address}.0`)}"><span class="medium-symbol ${mainMedium}">${mediumIcon(mainMedium)}<i class="map-line-state">○</i></span><div><small>BEREICH ${escapeHtml(area.address)} · ${escapeHtml(mainLine?.medium || "Unbekannt")}</small><strong>${escapeHtml(area.name)}</strong><em>${count} Geräte · Hauptlinie ${escapeHtml(mainLine?.full_address || `${area.address}.0`)}</em></div></button>
        <div class="map-lines">${subLines.map((line) => { const kind = mediumKind(line.medium); return `<button class="map-line-node" type="button" data-line-address="${escapeHtml(line.full_address)}"><span class="medium-symbol ${kind}">${mediumIcon(kind)}<i class="map-line-state">○</i></span><div><small>LINIE ${escapeHtml(line.full_address)} · ${escapeHtml(line.medium)}</small><strong>${escapeHtml(line.name)}</strong><em>${line.devices.length} Geräte${line.segments?.length ? ` · ${line.segments.length} Segmente` : ""}</em></div></button>`; }).join("")}</div></div>`;
    }).join("")}</div>`;
}

function mediumKind(medium = "") {
  const value = String(medium).toUpperCase();
  if (value.includes("RF") || value.includes("FUNK")) return "rf";
  if (value.includes("IP")) return "ip";
  return "tp";
}

function mediumIcon(kind) {
  if (kind === "ip") return '<svg viewBox="0 0 24 24"><rect x="3" y="4" width="7" height="5" rx="1"/><rect x="14" y="4" width="7" height="5" rx="1"/><rect x="8.5" y="15" width="7" height="5" rx="1"/><path d="M6.5 9v3h11V9M12 12v3"/></svg>';
  if (kind === "rf") return '<svg viewBox="0 0 24 24"><circle cx="12" cy="17.5" r="1.5"/><path d="M8.5 14a5 5 0 0 1 7 0M5.5 11a9 9 0 0 1 13 0M2.5 8a13 13 0 0 1 19 0"/></svg>';
  return '<svg viewBox="0 0 24 24"><path d="M5 4c7 3 7 13 14 16M19 4C12 7 12 17 5 20M8 7h8M8 17h8"/></svg>';
}

function renderLineDevices(line) {
  const assigned = new Set();
  const segments = (line.segments || []).map((segment) => {
    const segmentDevices = line.devices.filter((device) => segment.devices.includes(device.address));
    segmentDevices.forEach((device) => assigned.add(device.address));
    const technology = segment.technology === "rf_multi" ? "RF Multi · voll prüfbar" : segment.technology === "rf_plus" ? "RF+ · versorgungsabhängig" : segment.medium;
    const segmentKind = mediumKind(segment.medium);
    return `<section class="segment-group ${escapeHtml(segment.technology)}"><header><span class="medium-symbol ${segmentKind}">${mediumIcon(segmentKind)}</span><div><strong>${escapeHtml(segment.name)}</strong><small>${escapeHtml(technology)}</small></div><em>${segmentDevices.length} Geräte</em></header><div class="segment-devices">${segmentDevices.map(deviceCard).join("") || '<p class="empty-line">Keine adressierten Geräte</p>'}</div></section>`;
  }).join("");
  const unassigned = line.devices.filter((device) => !assigned.has(device.address));
  const lineKind = mediumKind(line.medium);
  const regular = unassigned.length ? `<section class="segment-group standard"><header><span class="medium-symbol ${lineKind}">${mediumIcon(lineKind)}</span><div><strong>${line.segments?.length ? "TP-/Liniengeräte" : "Geräte"}</strong><small>${escapeHtml(line.medium)}</small></div><em>${unassigned.length} Geräte</em></header><div class="segment-devices">${unassigned.map(deviceCard).join("")}</div></section>` : "";
  return segments || regular ? `${segments}${regular}` : '<p class="empty-line">Keine Geräte in dieser Linie</p>';
}

function drawTopologyFlows() {
  const map = document.querySelector(".topology-map");
  const svg = map?.querySelector(".map-flow-layer");
  const root = map?.querySelector(".map-root");
  if (!map || !svg || !root) return;
  const mapRect = map.getBoundingClientRect();
  const width = map.scrollWidth;
  const height = map.scrollHeight;
  svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
  svg.style.width = `${width}px`;
  svg.style.height = `${height}px`;
  const point = (element, edge) => {
    const rect = element.getBoundingClientRect();
    return {
      x: rect.left - mapRect.left + map.scrollLeft + rect.width / 2,
      y: rect.top - mapRect.top + map.scrollTop + (edge === "bottom" ? rect.height : 0),
    };
  };
  const paths = [];
  const connect = (from, to, state = "normal") => {
    const start = point(from, "bottom");
    const end = point(to, "top");
    const bend = start.y + Math.max(24, (end.y - start.y) * .48);
    const d = `M ${start.x} ${start.y} C ${start.x} ${bend}, ${end.x} ${bend}, ${end.x} ${end.y}`;
    paths.push(`<path class="flow-base ${state}" d="${d}"/><path class="flow-pulse ${state}" d="${d}"/>`);
  };
  map.querySelectorAll(".map-area").forEach((area) => {
    const areaNode = area.querySelector(".map-area-node");
    connect(root, areaNode);
    area.querySelectorAll(".map-line-node").forEach((line) => {
      const state = line.classList.contains("has-error") ? "error" : line.classList.contains("is-ok") ? "ok" : "open";
      connect(areaNode, line, state);
    });
  });
  svg.innerHTML = paths.join("");
}

function deviceCard(device) {
  const isDummy = device.kind === "dummy";
  return `<div class="device-card${isDummy ? " info-device" : ""}" data-address="${escapeHtml(device.address)}" data-name="${escapeHtml(device.name)}" data-kind="${escapeHtml(device.kind || "physical")}" data-policy="${escapeHtml(device.test_policy || "normal")}">
    <span class="device-dot"></span><div><strong>${escapeHtml(device.address)}</strong><span>${escapeHtml(device.name)}</span><small>${isDummy ? "ℹ Info · virtuelles ETS-Gerät" : "○ Noch nicht geprüft"}</small></div>
  </div>`;
}

function updateTopologySummaries() {
  document.querySelectorAll(".topology-line").forEach((line) => {
    const cards = [...line.querySelectorAll(".device-card")];
    const ok = cards.filter((card) => card.classList.contains("online")).length;
    const errors = cards.filter((card) => card.classList.contains("offline")).length;
    const warnings = cards.filter((card) => card.classList.contains("warning")).length;
    const infos = cards.filter((card) => card.classList.contains("info-device")).length;
    const open = cards.length - ok - errors - warnings - infos;
    line.querySelector('[data-scope="line"]').textContent = `${cards.length} Geräte · ${ok} OK · ${errors} Fehler · ${warnings} Warnung · ${open} offen${infos ? ` · ${infos} Info` : ""}`;
    const status = line.querySelector(".line-status");
    status.className = `line-status ${errors ? "error" : warnings ? "warning" : open ? "unchecked" : "ok"}`;
    status.textContent = errors ? "Fehler" : warnings ? "Warnung" : open ? "offen" : "OK";
    const mapNode = document.querySelector(`[data-line-address="${CSS.escape(line.dataset.lineAddress || "")}"]:not(.topology-line)`);
    if (mapNode) {
      mapNode.classList.remove("has-error", "has-warning", "has-open", "is-ok");
      mapNode.classList.add(errors ? "has-error" : warnings ? "has-warning" : open ? "has-open" : "is-ok");
      const state = mapNode.querySelector(".map-line-state");
      if (state) state.textContent = errors ? "×" : warnings || open ? "!" : "✓";
      const metric = mapNode.querySelector("em");
      if (metric) metric.textContent = `${ok}/${cards.length - infos} geprüft${errors ? ` · ${errors} Fehler` : warnings ? ` · ${warnings} Warnung` : ""}`;
    }
  });
  document.querySelectorAll(".topology-area").forEach((area) => {
    const cards = [...area.querySelectorAll(".device-card")];
    const ok = cards.filter((card) => card.classList.contains("online")).length;
    const errors = cards.filter((card) => card.classList.contains("offline")).length;
    const warnings = cards.filter((card) => card.classList.contains("warning")).length;
    const infos = cards.filter((card) => card.classList.contains("info-device")).length;
    const open = cards.length - ok - errors - warnings - infos;
    area.querySelector('[data-scope="area"]').textContent = `${cards.length} Geräte · ${ok} OK · ${errors} Fehler · ${warnings} Warnung · ${open} offen${infos ? ` · ${infos} Info` : ""}`;
  });
  drawTopologyFlows();
  renderOpenPoints();
}

function renderOpenPoints() {
  const panel = byId("open-points");
  const offline = [...document.querySelectorAll('.device-card.offline[data-kind="physical"]')];
  panel.hidden = false;
  byId("open-points-count").textContent = offline.length;
  byId("open-points-list").innerHTML = offline.length ? offline.map((card) => {
    const line = card.closest(".topology-line")?.dataset.lineAddress || "—";
    return `<div class="open-point-row"><span class="issue-badge">× Fehler</span><strong>${escapeHtml(card.dataset.address)}</strong><span>${escapeHtml(card.dataset.name)}</span><span>Keine Antwort · Gerät nicht erreichbar</span><small>Linie ${escapeHtml(line)}</small></div>`;
  }).join("") : '<p class="no-open-points">Keine Fehler vorhanden. Dummys und virtuelle ETS-Geräte werden als Information behandelt.</p>';
}

function startMonitor() {
  if (!busConnected) {
    byId("monitor-state").className = "badge muted";
    byId("monitor-state").textContent = "○ Keine KNX-Verbindung";
    return;
  }
  if (monitorSocket && monitorSocket.readyState <= WebSocket.OPEN) return;
  const protocol = location.protocol === "https:" ? "wss" : "ws";
  monitorSocket = new WebSocket(`${protocol}://${location.host}/api/knx/monitor`);
  monitorSocket.onopen = () => {
    monitorRunning = true;
    byId("monitor-state").className = "badge good";
    byId("monitor-state").textContent = "● Monitor aktiv";
    byId("monitor-toggle-button").className = "danger monitor-stop";
    byId("monitor-toggle-button").textContent = "Monitor stoppen";
  };
  monitorSocket.onmessage = (event) => addTelegram(JSON.parse(event.data));
  monitorSocket.onclose = () => {
    monitorRunning = false;
    monitorSocket = null;
    byId("monitor-state").className = busConnected ? "badge info" : "badge muted";
    byId("monitor-state").textContent = busConnected ? "Ⅱ Monitor gestoppt" : "○ Monitor getrennt";
    byId("monitor-toggle-button").className = "primary";
    byId("monitor-toggle-button").textContent = "Monitor starten";
  };
}

function stopMonitor() {
  if (monitorSocket) monitorSocket.close();
  else {
    monitorRunning = false;
    byId("monitor-state").className = busConnected ? "badge info" : "badge muted";
    byId("monitor-state").textContent = busConnected ? "Ⅱ Monitor gestoppt" : "○ Monitor getrennt";
  }
}

function updateMonitorConnectionBadge() {
  const badge = byId("monitor-connection-badge");
  badge.className = busConnected ? "badge good" : "badge muted";
  badge.textContent = busConnected ? "✓ KNX verbunden" : "○ Nicht verbunden";
  if (!busConnected) stopMonitor();
}

function addTelegram(telegram) {
  monitorTelegrams.unshift(telegram);
  if (monitorTelegrams.length > 2000) monitorTelegrams.length = 2000;
  renderMonitor();
}

function monitorMatches(telegram) {
  const search = byId("monitor-search").value.trim().toLocaleLowerCase("de-CH");
  const source = byId("monitor-source-filter").value.trim().toLocaleLowerCase("de-CH");
  const service = byId("monitor-service-filter").value;
  const destinationText = `${telegram.destination || ""} ${telegram.group_name || ""} ${telegram.value || ""}`.toLocaleLowerCase("de-CH");
  if (search && !destinationText.includes(search)) return false;
  if (source && !String(telegram.source || "").toLocaleLowerCase("de-CH").includes(source)) return false;
  if (service && telegram.service !== service) return false;
  if (byId("monitor-secure-only").checked && !telegram.secure) return false;
  return true;
}

function renderMonitor() {
  const filtered = monitorTelegrams.filter(monitorMatches);
  const rows = filtered.slice(0, 500);
  byId("monitor-total-count").textContent = monitorTelegrams.length;
  byId("monitor-visible-count").textContent = filtered.length;
  const filtersActive = Boolean(
    byId("monitor-search").value.trim()
    || byId("monitor-source-filter").value.trim()
    || byId("monitor-service-filter").value
    || byId("monitor-secure-only").checked
  );
  byId("monitor-filter-note").textContent = filtersActive ? "Filter aktiv" : "Keine Filter aktiv";
  if (!rows.length) {
    byId("monitor-body").innerHTML = `<tr class="monitor-empty"><td colspan="7">${filtersActive ? "Keine passenden Telegramme gefunden." : "Noch keine Telegramme aufgezeichnet."}</td></tr>`;
    return;
  }
  byId("monitor-body").innerHTML = rows.map((telegram) => {
    const time = new Date(telegram.time).toLocaleTimeString("de-CH", {hour12: false, fractionalSecondDigits: 3});
    const destination = telegram.group_name ? `${telegram.destination} · ${telegram.group_name}` : telegram.destination;
    return `<tr><td>${escapeHtml(time)}</td><td>${escapeHtml(telegram.source)}</td><td>${escapeHtml(destination)}</td><td>${escapeHtml(telegram.dpt || "—")}</td><td>${escapeHtml(telegram.service)}${telegram.secure ? " · Secure" : ""}</td><td class="monitor-value">${escapeHtml(telegram.value || "—")}</td><td class="monitor-raw" title="${escapeHtml(telegram.raw || "—")}">${escapeHtml(telegram.raw || "—")}</td></tr>`;
  }).join("");
}

byId("monitor-toggle-button").addEventListener("click", () => {
  if (monitorRunning) stopMonitor();
  else startMonitor();
});

["monitor-search", "monitor-source-filter"].forEach((id) => byId(id).addEventListener("input", renderMonitor));
["monitor-service-filter", "monitor-secure-only"].forEach((id) => byId(id).addEventListener("change", renderMonitor));

byId("clear-monitor-button").addEventListener("click", () => {
  monitorTelegrams = [];
  renderMonitor();
});

updateMonitorConnectionBadge();

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
    document.body.innerHTML = `<main class="shutdown-screen"><div><span>✓</span><h1>Anwendung beendet</h1><p>${escapeHtml(data.message)}</p><small>Das Programmfenster wird geschlossen.</small></div></main>`;
    if (window.pywebview?.api?.close_window) {
      window.pywebview.api.close_window().catch(() => window.close());
      return;
    }
    setTimeout(() => window.close(), 250);
  } catch (error) {
    button.disabled = false;
    button.textContent = "KNX trennen & beenden";
    alert(error.message);
  }
});

function escapeHtml(value) {
  return String(value).replace(/[&<>'"]/g, (char) => ({"&":"&amp;","<":"&lt;",">":"&gt;","'":"&#39;",'"':"&quot;"})[char]);
}