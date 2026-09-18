const $ = (selector) => document.querySelector(selector);
const token = document.body.dataset.token;
const icons = () => lucide.createIcons();
let current = null;
let requestBusy = false;
let lastJobMessage = "";
const size = (value) => {
  const n = Math.max(0, Number(value || 0));
  const units = ["o", "Ko", "Mo", "Go", "To"];
  const i = Math.min(4, Math.floor(Math.log(Math.max(n, 1)) / Math.log(1024)));
  return `${(n / 1024 ** i).toLocaleString("fr-FR", { maximumFractionDigits: i ? 1 : 0 })} ${units[i]}`;
};
const date = (value) =>
  value
    ? new Date(value).toLocaleString("fr-FR", {
        day: "numeric",
        month: "short",
        hour: "2-digit",
        minute: "2-digit",
      })
    : "Pas encore";
const age = (value) =>
  value
    ? Math.max(0, Math.round((Date.now() - new Date(value).getTime()) / 1000))
    : null;
const elapsed = (value) => {
  const seconds = age(value);
  return seconds === null
    ? "en attente"
    : seconds < 60
      ? `il y a ${seconds} s`
      : seconds < 3600
        ? `il y a ${Math.floor(seconds / 60)} min`
        : date(value);
};
const providerNames = {
  "google-drive": "Google Drive",
  "icloud-drive": "iCloud Drive",
  onedrive: "OneDrive",
  dropbox: "Dropbox",
  "local-folder": "Dossier synchronis\u00e9",
};
const providerNotes = {
  "google-drive":
    "Connexion directe Google Drive via rclone. Si Drive est plein, la sauvegarde s'arr\u00eate et vous pouvez lib\u00e9rer de l'espace ou changer de destination.",
  "icloud-drive":
    "Sauvegarde dans iCloud Drive via le dossier local du Mac. iCloud g\u00e8re ensuite l'envoi vers le cloud.",
  onedrive:
    "Sauvegarde dans le dossier OneDrive local. OneDrive doit \u00eatre install\u00e9 et connect\u00e9 sur ce Mac.",
  dropbox:
    "Sauvegarde dans le dossier Dropbox local. Dropbox doit \u00eatre install\u00e9 et connect\u00e9 sur ce Mac.",
  "local-folder":
    "Choisissez un dossier local, externe ou synchronis\u00e9. L'app v\u00e9rifie l'espace libre avant de copier.",
};
function notice(message, error = false) {
  for (const selector of ["#message", "#settings-message"]) {
    const el = $(selector);
    el.textContent = message;
    el.hidden = !message;
    el.classList.toggle("error", error);
  }
}
function busy() {
  const locked = requestBusy || !!current?.job?.running;
  document.querySelectorAll("[data-action]").forEach((button) => {
    button.disabled =
      locked || (button.dataset.action === "sync" && current?.sync_active);
  });
}
function settings() {
  if (!$("#settings").open) $("#settings").showModal();
}
$("#settings-open").addEventListener("click", settings);
$("#cloud-setup").addEventListener("click", () => {
  settings();
  $("#provider").focus();
});
$("#settings-close").addEventListener("click", () => $("#settings").close());
$("#settings").addEventListener("click", (event) => {
  if (event.target === $("#settings")) {
    const r = event.target.getBoundingClientRect();
    if (
      event.clientX < r.left ||
      event.clientX > r.right ||
      event.clientY < r.top ||
      event.clientY > r.bottom
    )
      event.target.close();
  }
});
function render(data) {
  current = data;
  const storage = data.storage || {},
    watcher = data.watcher || {},
    health = data.health || {},
    sync = data.sync || {};
  const configured = storage.cloud_sync === "configured";
  const installed = data.watcher_service?.installed;
  const healthy = health.state === "running";
  $("#health-badge").dataset.state = health.state;
  $("#health-label").textContent = health.label;
  $("#watcher-title").textContent = health.label;
  $("#overview-subtitle").textContent =
    `${data.archive_count || 0} archives locales \u00b7 ${configured ? "Sauvegarde cloud connect\u00e9e" : "Sauvegarde sur cet ordinateur"}`;
  $("#conversation-count").textContent = (
    data.conversation_count || 0
  ).toLocaleString("fr-FR");
  $("#project-count").textContent = (data.project_count || 0).toLocaleString(
    "fr-FR",
  );
  $("#archive-size").textContent = size(storage.archive_bytes);
  $("#settings-archive-size").textContent = size(storage.archive_bytes);
  $("#db-size").textContent = size(storage.database_bytes);
  $("#total-size").textContent = size(storage.total_bytes);
  $("#archive-path").textContent = storage.archive;
  $("#footer-size").textContent =
    `${size(storage.total_bytes)} sur cet ordinateur`;
  $("#watcher-detail").textContent =
    watcher.last_error ||
    (health.state === "stale"
      ? `Aucune collecte confirm\u00e9e depuis ${date(watcher.last_success_at || watcher.started_at)}`
      : `Dernier scan r\u00e9ussi ${elapsed(watcher.last_success_at)}${installed ? " \u00b7 D\u00e9marrage automatique" : ""}`);
  $("#watcher-enable").hidden = !!watcher.running && !!installed;
  $("#watcher-enable").textContent = watcher.running
    ? "Automatiser"
    : "Activer";
  $("#watcher-check").hidden = !healthy || !installed;
  $("#autostart-status").textContent = installed
    ? "Service install\u00e9"
    : "Non configur\u00e9e";
  $("#autostart-enable").hidden = !!installed;
  $("#mcp-status").textContent = data.mcp_configured
    ? "Configur\u00e9 dans Codex"
    : "Non configur\u00e9";
  $("#mcp-enable").hidden = !!data.mcp_configured;
  $("#menubar-setting").hidden = !data.menubar_available;
  $("#menubar-login").checked = !!data.menubar_login;
  $(".local-label").lastChild.textContent = data.menubar_available
    ? " Sur votre Mac"
    : " Sur cet ordinateur";
  const provider =
    storage.provider_label ||
    providerNames[storage.provider] ||
    "Sauvegarde cloud";
  $("#cloud-title").textContent = configured ? provider : "Sauvegarde cloud";
  const phases = {
    preparing: "Pr\u00e9paration",
    uploading: "Envoi en cours",
    downloading: "R\u00e9ception en cours",
    downloading_raw: "R\u00e9ception des sources",
    verifying: "V\u00e9rification des copies",
    indexing: "Indexation",
    synced: "\u00c0 jour",
    error: "Erreur de synchronisation",
  };
  let cloudText = configured
    ? phases[sync.status] || "En attente de synchronisation"
    : "Aucun compte connect\u00e9";
  if (configured && sync.status === "synced")
    cloudText += ` \u00b7 ${elapsed(sync.last_success_at)}`;
  else if (configured && data.sync_active)
    cloudText += ` \u00b7 ${size(sync.bytes)} / ${size(sync.totalBytes)} \u00b7 ${size(sync.speed)}/s`;
  else if (configured && sync.status !== "error" && sync.status !== "synced")
    cloudText = "En attente de synchronisation";
  if (sync.error && configured) cloudText += ` : ${sync.error}`;
  if (data.sync_active && age(sync.heartbeat_at || sync.started_at) > 180)
    cloudText = `Synchronisation \u00e0 v\u00e9rifier \u00b7 derni\u00e8re activit\u00e9 ${elapsed(sync.heartbeat_at || sync.started_at)}`;
  $("#cloud-detail").textContent = cloudText;
  $("#cloud-progress").hidden = !configured || !data.sync_active;
  if (sync.totalBytes > 0)
    $("#cloud-progress").value = Math.min(
      100,
      (100 * (sync.bytes || 0)) / sync.totalBytes,
    );
  else $("#cloud-progress").removeAttribute("value");
  $("#cloud-setup").hidden = configured;
  $('[data-action="sync"]').hidden = !configured;
  $("#cloud-connect-form").hidden = configured;
  $("#cloud-manage").hidden = !configured;
  $("#cloud-summary").textContent = configured
    ? `${provider} \u00b7 ${storage.remote}`
    : "Aucune destination connect\u00e9e";
  $("#cloud-last").textContent =
    `Derni\u00e8re sauvegarde compl\u00e8te : ${date(sync.last_success_at)}. ${sync.object_count || 0} objets v\u00e9rifi\u00e9s. Les copies locales et distantes sont conserv\u00e9es en cas de d\u00e9connexion.`;
  const audit = data.audit || {};
  $("#audit-detail").textContent = audit.checked_at
    ? `${audit.verified_files}/${audit.files} sources v\u00e9rifi\u00e9es \u00b7 ${(audit.issues || []).length} erreurs \u00b7 ${(audit.changing_files || []).length} sources modifi\u00e9es depuis \u00b7 ${(audit.missing_thread_ids || []).length} conversations sans source. ${date(audit.checked_at)}.`
    : "Aucune v\u00e9rification effectu\u00e9e.";
  $("#watcher-diagnostic").textContent =
    `Dernier scan : ${date(watcher.last_scan_at)}. ${watcher.scanned || 0} fichiers examin\u00e9s, ${watcher.imported || 0} import\u00e9s, ${watcher.skipped || 0} inchang\u00e9s.`;
  const rows = (data.recent_conversations || []).slice(0, 8);
  $("#history-count").textContent = `${rows.length} derni\u00e8res`;
  const list = $("#conversations");
  list.replaceChildren();
  if (!rows.length) {
    const p = document.createElement("p");
    p.className = "empty";
    p.textContent = "Aucune conversation import\u00e9e pour le moment.";
    list.append(p);
  }
  for (const row of rows) {
    const item = document.createElement("div");
    item.className = "conversation";
    item.innerHTML =
      '<i data-lucide="message-square"></i><div class="conversation-copy"><div class="conversation-title"></div><div class="conversation-source"></div></div><time></time>';
    const title =
      row.latest_user_message || row.title || row.source_session_id || row.id;
    item.querySelector(".conversation-title").textContent = title;
    item.querySelector(".conversation-title").title = title;
    item.querySelector(".conversation-source").textContent =
      row.source === "codex" ? "Codex" : row.source;
    item.querySelector("time").textContent = date(
      row.updated_at || row.created_at,
    );
    item.querySelector("time").dateTime =
      row.updated_at || row.created_at || "";
    list.append(item);
  }
  if (data.job?.message && data.job.message !== lastJobMessage) {
    lastJobMessage = data.job.message;
    notice(data.job.message, data.job.error);
  }
  icons();
  busy();
}
async function refresh() {
  try {
    const res = await fetch("/api/status", {
      signal: AbortSignal.timeout(15000),
    });
    if (!res.ok) throw new Error("Lecture impossible");
    render(await res.json());
  } catch (error) {
    $("#health-badge").dataset.state = "error";
    $("#health-label").textContent = "Interface d\u00e9connect\u00e9e";
    notice(
      "Connexion locale perdue. Rouvrez AI Memory depuis la barre de menus.",
      true,
    );
  }
}
async function action(name, body = {}) {
  requestBusy = true;
  busy();
  notice("Op\u00e9ration en cours\u2026");
  try {
    if (name === "connect-cloud") {
      const file = $("#oauth-client").files[0];
      body = {
        provider: $("#provider").value,
        folder: $("#folder").value,
        oauth_client: file ? JSON.parse(await file.text()) : null,
      };
    }
    const res = await fetch(`/api/${name}`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-AI-Memory-Token": token,
      },
      body: JSON.stringify(body),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.message);
    notice(data.message);
    await refresh();
  } catch (error) {
    notice(error.message, true);
  } finally {
    requestBusy = false;
    busy();
  }
}
document
  .querySelectorAll("[data-action]")
  .forEach((button) =>
    button.addEventListener("click", () => action(button.dataset.action)),
  );
$("#menubar-login").addEventListener("change", async (event) => {
  event.target.disabled = true;
  await action("menubar-login", { enabled: event.target.checked });
  event.target.disabled = false;
});
$("#provider").addEventListener("change", (event) => {
  const google = event.target.value === "google-drive";
  const customFolder = event.target.value === "local-folder";
  $("#folder-label").hidden = !customFolder;
  $("#oauth-settings").hidden = !google;
  const label = providerNames[event.target.value] || "la destination";
  $('[data-action="connect-cloud"]').textContent = `Connecter ${label}`;
  $("#provider-note").textContent =
    providerNotes[event.target.value] || providerNotes["local-folder"];
});
$("#provider").dispatchEvent(new Event("change"));
icons();
async function poll() {
  await refresh();
  setTimeout(poll, 5000);
}
poll();
