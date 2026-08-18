const byId = (id) => document.getElementById(id);

byId("scan-button").addEventListener("click", async () => {
  const button = byId("scan-button");
  const list = byId("gateway-list");
  button.disabled = true;
  button.textContent = "Suche läuft …";
  list.className = "empty-state";
  list.innerHTML = "<span>◌</span><strong>KNX/IP-Netzwerk wird durchsucht</strong><small>Dies dauert ungefähr drei Sekunden.</small>";
  try {
    const response = await fetch("/api/knx/gateways");
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

