const byId = (id) => document.getElementById(id);

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
      body: JSON.stringify({gateway_ip: gatewayIp, local_ip: byId("adapter-select").value || null}),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || "Verbindung fehlgeschlagen");
    message.className = "message success";
    message.textContent = data.message;
    byId("connection-badge").className = "badge good";
    byId("connection-badge").textContent = `✓ Verbunden mit ${data.gateway_ip}`;
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
    byId("project-name").textContent = project.name;
    byId("project-badge").className = "badge good";
    byId("project-badge").textContent = "✓ ETS-Projekt geladen";
    byId("device-count").textContent = project.device_count;
    byId("open-count").textContent = project.device_count;
    message.textContent = `${project.device_count} Geräte und ${project.group_address_count} Gruppenadressen eingelesen.`;
    const topology = byId("topology");
    topology.className = "topology-grid";
    topology.innerHTML = project.devices.slice(0, 40).map((d) => `<div class="device-card"><strong>${escapeHtml(d.address)} · ${escapeHtml(d.name)}</strong><small>○ Noch nicht geprüft</small></div>`).join("") || "<div class='topology-empty'>Keine Geräte gefunden.</div>";
  } catch (error) {
    message.className = "message error";
    message.textContent = error.message;
  }
});

function escapeHtml(value) {
  return String(value).replace(/[&<>'"]/g, (char) => ({"&":"&amp;","<":"&lt;",">":"&gt;","'":"&#39;",'"':"&quot;"})[char]);
}
