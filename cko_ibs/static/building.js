// Gebäude-Seite: Gebäude › Etagen › Räume aus dem ETS-Projekt, Räume leuchten bei Busaktivität.
(() => {
  const $ = (id) => document.getElementById(id);
  const esc = (value) => escapeHtml(value ?? "");
  const ROOM_TYPES = new Set(["Room", "Corridor", "Stairway", "DistributionBoard"]);
  const ICONS = [
    [/küche|kueche|kochen/i, "🍳"], [/bad|wc|dusche|toilette/i, "🛁"], [/schlaf|eltern/i, "🛏️"], [/wohn|lounge/i, "🛋️"],
    [/büro|buero|arbeit|office/i, "💻"], [/kind|gäste|gaeste|spiel/i, "🧸"], [/wasch|wäsche|waesche/i, "🧺"],
    [/technik|heiz|keller|lager|haustechnik/i, "⚙️"], [/garage|carport/i, "🚗"], [/flur|korridor|gang|diele|eingang|entrée/i, "🚪"],
    [/treppe/i, "🪜"], [/terrasse|garten|balkon|aussen|außen/i, "🌿"], [/fitness|sport|malen|hobby/i, "🏋️"],
    [/essen|ess/i, "🍽️"], [/dach|estrich/i, "🏠"], [/verteil|schaltschrank/i, "🔌"], [/halle|werkstatt/i, "🏭"],
  ];
  const state = {layout: [], rooms: [], byGa: new Map(), selected: null, loaded: false};

  const iconFor = (room) => (ICONS.find(([pattern]) => pattern.test(`${room.name} ${room.usage}`)) || [null, "▢"])[1];
  const isRoom = (node) => ROOM_TYPES.has(node.type) || !node.children?.length;

  const flatRooms = (node) => (isRoom(node) ? [node] : node.children.flatMap(flatRooms));

  function floorsOf(building) {
    // Etagen in ETS-Reihenfolge (meist oben → unten); Räume ohne Etage in eigener Reihe
    const floors = [];
    const loose = [];
    const walk = (node) => node.children.forEach((child) => {
      if (child.type === "Floor") floors.push(child);
      else if (isRoom(child)) loose.push(child);
      else walk(child);
    });
    walk(building);
    if (loose.length) floors.push({name: "Ohne Etage", type: "Floor", children: loose, synthetic: true});
    return floors;
  }

  function render() {
    const root = $("building-root");
    if (!state.layout?.length) {
      root.innerHTML = `<div class="bd-empty"><span>🏠</span><strong>${state.loaded ? "Im Projekt ist keine Gebäudestruktur angelegt" : "Noch kein ETS-Projekt geladen"}</strong>
        <small>${state.loaded ? "In der ETS unter «Gebäude» Etagen und Räume anlegen und Geräte bzw. Funktionen zuordnen – dann erscheint hier das Haus mit Live-Aktivität je Raum. Alle anderen Funktionen sind davon nicht betroffen." : "Gebäude, Etagen und Räume stammen aus der ETS-Ansicht «Gebäude». Projekt in Schritt 1 laden."}</small>
        <button type="button" class="secondary" data-go-view="project">Zu Schritt 1: Projekt</button></div>`;
      return;
    }
    const roomTile = (room) => `<button type="button" class="bd-room" data-room="${room.id}" title="${esc(room.name)} – ${room.devices.length} Geräte · ${room.functions.length} Funktionen">
        <span class="bd-icon">${iconFor(room)}</span><strong>${esc(room.name)}</strong>
        <small>${room.devices.length} Geräte${room.functions.length ? ` · ${room.functions.length} Funktionen` : ""}</small>
        <em class="bd-last"></em><i class="bd-glow"></i></button>`;
    root.innerHTML = `<div class="bd-layout"><div class="bd-houses">${state.layout.map(({building, floors}) => `
      <section class="bd-house"><div class="bd-roof"><span>${esc(building.name)}</span><small>${esc(building.type_label)}</small></div>
        <div class="bd-floors">${floors.map(({floor, rooms}) => `<div class="bd-floor"><div class="bd-floor-label"><strong>${esc(floor.name)}</strong><small>${floor.synthetic ? "" : "Etage"} · ${rooms.length} Räume</small></div>
          <div class="bd-rooms">${rooms.map(roomTile).join("") || '<span class="bd-none">keine Räume</span>'}</div></div>`).join("")}</div>
        <div class="bd-ground"></div></section>`).join("")}</div>
      <aside id="bd-detail" class="bd-detail"><p class="bd-hint">Raum anklicken, um Geräte, Funktionen und aktuelle Werte zu sehen.</p></aside></div>`;
    if (state.selected) showRoom(state.selected);
  }

  function index(project) {
    state.loaded = true;
    state.rooms = [];
    state.byGa = new Map();
    state.layout = (project.buildings || []).map((building) => {
      let floors = floorsOf(building);
      if (!floors.length) floors = [{name: "Räume", type: "Floor", children: building.children, synthetic: true}];
      return {building, floors: floors.map((floor) => ({floor, rooms: (floor.children || []).flatMap(flatRooms).map((node) => {
        const room = {...node, floor: floor.synthetic ? null : floor, building, id: `r${state.rooms.length}`};
        state.rooms.push(room);
        return room;
      })}))};
    });
    state.rooms.forEach((room) => room.group_addresses.forEach((address) => {
      if (!state.byGa.has(address)) state.byGa.set(address, []);
      state.byGa.get(address).push(room);
    }));
    state.selected = null;
    render();
  }

  function showRoom(id) {
    const room = state.rooms.find((item) => item.id === id);
    const panel = $("bd-detail");
    if (!room || !panel) return;
    state.selected = id;
    document.querySelectorAll(".bd-room.selected").forEach((tile) => tile.classList.remove("selected"));
    document.querySelector(`.bd-room[data-room="${id}"]`)?.classList.add("selected");
    const api = window.gmApi;
    const gaRow = (address) => {
      const group = api?.groups.get(address);
      return `<button type="button" class="bd-ga" data-ga="${esc(address)}"><span>${esc(address)}</span><b>${esc(group?.name || "")}</b><em>${group ? esc(api.valueText(group)) : "–"}</em></button>`;
    };
    const used = new Set(room.functions.flatMap((fn) => fn.group_addresses));
    const other = room.group_addresses.filter((address) => !used.has(address));
    panel.innerHTML = `<div class="bd-detail-head"><span class="bd-icon">${iconFor(room)}</span><div><small>${esc(room.building?.name || "")}${room.floor ? ` › ${esc(room.floor.name)}` : ""}</small><h2>${esc(room.name)}</h2>${room.usage ? `<p>${esc(room.usage)}</p>` : ""}</div></div>
      ${room.functions.length ? `<h3>Funktionen</h3>${room.functions.map((fn) => `<div class="bd-function"><strong>${esc(fn.name)}</strong>${fn.group_addresses.map(gaRow).join("")}</div>`).join("")}` : ""}
      ${other.length ? `<h3>Gruppenadressen der Geräte</h3>${other.slice(0, 60).map(gaRow).join("")}${other.length > 60 ? `<p class="bd-hint">… und ${other.length - 60} weitere</p>` : ""}` : ""}
      <h3>Geräte (${room.devices.length})</h3>${room.devices.map((device) => `<div class="bd-device"><span>${esc(device.address)}</span>${esc(device.name)}</div>`).join("") || '<p class="bd-hint">Keine Geräte zugeordnet.</p>'}
      <p class="bd-hint">Gruppenadresse anklicken, um sie zu bedienen.</p>`;
  }

  $("building-root").addEventListener("click", (event) => {
    const tile = event.target.closest(".bd-room");
    if (tile) { showRoom(tile.dataset.room); return; }
    const ga = event.target.closest(".bd-ga");
    if (ga && window.gmApi?.groups.has(ga.dataset.ga)) window.gmApi.openControl(ga.dataset.ga);
  });

  window.addEventListener("knx-project", (event) => index(event.detail || {}));
  window.addEventListener("knx-telegram", (event) => {
    if (!$("bd-live")?.checked) return;
    const item = event.detail;
    const rooms = state.byGa.get(item.destination);
    if (!rooms) return;
    const group = window.gmApi?.groups.get(item.destination);
    rooms.forEach((room) => {
      const tile = document.querySelector(`.bd-room[data-room="${room.id}"]`);
      if (!tile) return;
      tile.classList.remove("pulse");
      void tile.offsetWidth;
      tile.classList.add("pulse", "active");
      clearTimeout(room.timer);
      room.timer = setTimeout(() => tile.classList.remove("active"), 8000);
      const last = tile.querySelector(".bd-last");
      if (last && item.service !== "GroupValueRead") last.textContent = `${group?.name || item.destination}: ${group ? window.gmApi.valueText(group) : item.value || ""}`;
      if (state.selected === room.id) showRoom(room.id);
    });
  });
  render();
})();
