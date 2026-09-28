const $ = (selector) => document.querySelector(selector);
const token = document.body.dataset.token;
const icons = () => lucide.createIcons();
let current = null;
let requestBusy = false;
let lastJobMessage = "";
let switchingCloud = false;
let reauthenticating = false;
let providerChosen = false;
let icloudAwaiting2FA = false;
let icloudAwaitingWebApproval = false;
let icloudAwaitingTerms = false;
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
  "dropbox-online": "Dropbox",
  "onedrive-online": "OneDrive",
  "icloud-online": "iCloud Drive",
  "icloud-drive": "iCloud Drive du Mac",
  onedrive: "OneDrive du Mac",
  dropbox: "Dropbox du Mac",
  "local-folder": "Dossier synchronis\u00e9",
};
const providerNotes = {
  "google-drive":
    "Connexion directe Google Drive via rclone. Si Drive est plein, la sauvegarde s'arr\u00eate et vous pouvez lib\u00e9rer de l'espace ou changer de destination.",
  "dropbox-online":
    "Connexion directe Dropbox via rclone. Le compte choisi peut \u00eatre diff\u00e9rent du Dropbox install\u00e9 sur ce Mac.",
  "onedrive-online":
    "Connexion directe OneDrive via rclone. Choisissez personnel ou professionnel selon le compte \u00e0 connecter.",
  "icloud-online":
    "Connexion directe iCloud via rclone, ind\u00e9pendante de l'iCloud local du Mac. Utilisez le mot de passe Apple ID normal puis le code 2FA affich\u00e9 sur votre appareil. Les mots de passe sp\u00e9cifiques d'app ne sont pas accept\u00e9s.",
  "icloud-drive":
    "Sauvegarde dans l'iCloud Drive d\u00e9j\u00e0 connect\u00e9 \u00e0 cette session macOS.",
  onedrive:
    "Sauvegarde dans le dossier OneDrive local. OneDrive doit \u00eatre install\u00e9 et connect\u00e9 sur ce Mac.",
  dropbox:
    "Sauvegarde dans le dossier Dropbox local. Dropbox doit \u00eatre install\u00e9 et connect\u00e9 sur ce Mac.",
  "local-folder":
    "Choisissez un dossier local, externe ou synchronis\u00e9. L'app v\u00e9rifie l'espace libre avant de copier.",
};
const icloud2faMessage =
  "Code demand\u00e9. Validez la demande Apple sur votre appareil, entrez le code ici, puis cliquez sur Confirmer le code iCloud.";
const icloudWebApprovalMessage =
  "Le code est accept\u00e9. Activez Acc\u00e8s aux donn\u00e9es iCloud sur le Web dans R\u00e9glages > compte Apple > iCloud, approuvez la demande Apple \u00e9ventuelle, puis cliquez sur R\u00e9essayer iCloud.";
const icloudTermsMessage =
  "Le code est accept\u00e9, mais Apple n'a ouvert qu'une session partielle. Ouvrez iCloud.com, connectez-vous et acceptez les nouvelles conditions iCloud \u00e9ventuelles. Revenez ensuite ici et cliquez sur J'ai accept\u00e9, relancer.";
function revealIcloud2FA() {
  if ($("#provider").value !== "icloud-online") return;
  const wasWaiting = icloudAwaiting2FA;
  icloudAwaiting2FA = true;
  icloudAwaitingWebApproval = false;
  icloudAwaitingTerms = false;
  switchingCloud = true;
  $("#icloud-password").value = "";
  if (!wasWaiting) $("#icloud-2fa").value = "";
  settings();
  updateProviderFields();
  notice(icloud2faMessage);
  requestAnimationFrame(() => $("#icloud-2fa").focus());
}
function revealIcloudWebApproval() {
  if ($("#provider").value !== "icloud-online") return;
  icloudAwaiting2FA = false;
  icloudAwaitingWebApproval = true;
  icloudAwaitingTerms = false;
  switchingCloud = true;
  $("#icloud-password").value = "";
  $("#icloud-2fa").value = "";
  settings();
  updateProviderFields();
  notice(current?.icloud_auth?.status === "needs_access_retry" ? current.icloud_auth.message : icloudWebApprovalMessage);
}
function revealIcloudTerms() {
  if ($("#provider").value !== "icloud-online") return;
  icloudAwaiting2FA = false;
  icloudAwaitingWebApproval = false;
  icloudAwaitingTerms = true;
  switchingCloud = true;
  $("#icloud-password").value = "";
  $("#icloud-2fa").value = "";
  settings();
  updateProviderFields();
  notice(icloudTermsMessage);
}
function notice(message, error = false) {
  for (const selector of ["#message", "#settings-message"]) {
    const el = $(selector);
    el.textContent = message;
    el.hidden = !message;
    el.classList.toggle("error", error);
  }
}
let refreshFailures = 0;
let connectionNotice = false;
function busy() {
  const locked = requestBusy || !!current?.job?.running;
  document.querySelectorAll("[data-action]").forEach((button) => {
    const name = button.dataset.action;
    const independent = ["open-folder", "pause-sync"].includes(name);
    button.disabled = (independent ? requestBusy : locked) ||
      (["sync", "install-watcher"].includes(name) && current?.sync_active);
  });
  $("#menubar-login").disabled = requestBusy;
  $("#cloud-folder-pick").disabled = locked;
  $("#folder-pick").disabled = requestBusy;
}
async function pickFolder(purpose, field, next) {
  if (requestBusy) return;
  requestBusy = true;
  busy();
  try {
    const res = await fetch("/api/choose-folder", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-AI-Memory-Token": token },
      body: JSON.stringify({ purpose, current: field.value }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.message);
    if (data.path) {
      field.value = data.path;
      notice("");
    }
  } catch (error) {
    notice(error.message, true);
  } finally {
    requestBusy = false;
    busy();
    next.focus();
  }
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
let themePending = false;
function applyTheme(value) {
  if (value === "light" || value === "dark")
    document.documentElement.dataset.theme = value;
  else delete document.documentElement.dataset.theme;
  try {
    localStorage.setItem("aimemory-theme", value);
  } catch (error) {}
  document
    .querySelectorAll("[data-theme-choice]")
    .forEach((button) =>
      button.setAttribute("aria-checked", String(button.dataset.themeChoice === value)),
    );
}
function conversationLabel(row) {
  const text = String(row.latest_user_message || "").trim();
  return { text: text || "Aucun message disponible", untitled: !text };
}
const shortLabel = value => {
  const chars = Array.from(String(value || ""));
  return chars.length > 60 ? chars.slice(0, 60).join("") + "..." : chars.join("");
};

// Views: conversations are the home screen, projects and storage open from the sidebar.
let view = "conv";
let projectFilter = null;
let projects = [];
let results = null;
let searchSeq = 0;
let searchTimer = null;
let nextOffset = 0;
let hasMore = false;
let libraryLoading = false;
let libraryRevision = null;
const conversationSelection = new Map();
let optionSeq = 0, optionOffset = 0, optionTimer = null;
const plural = (count, word) =>
  `${Number(count || 0).toLocaleString("fr-FR")} ${word}${count > 1 ? "s" : ""}`;
const query = () => $("#search").value.trim();
const browsing = () => results !== null || !!projectFilter || (view === "conv" && !!query());
const dayIndex = (value) => {
  const day = new Date(value);
  day.setHours(0, 0, 0, 0);
  return Math.round((new Date().setHours(0, 0, 0, 0) - day.getTime()) / 86400000);
};
function when(value, todayLabel = null) {
  const moment = new Date(value || "");
  if (!value || Number.isNaN(moment.getTime())) return "";
  const days = dayIndex(value);
  if (days <= 0)
    return todayLabel || moment.toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit" });
  if (days === 1) return "hier";
  if (days < 7) return moment.toLocaleDateString("fr-FR", { weekday: "short" });
  return moment.toLocaleDateString("fr-FR", {
    day: "numeric",
    month: "short",
    ...(moment.getFullYear() !== new Date().getFullYear() ? { year: "numeric" } : {}),
  });
}
function conversationItem(row) {
  const item = document.createElement("div");
  item.className = "conversation";
  item.tabIndex = 0;
  item.setAttribute("role", "button");
  item.innerHTML =
    '<svg><use href="#i-bubble"></use></svg><div class="conversation-copy"><div class="conversation-title"></div><div class="conversation-source"></div></div><time></time>';
  const label = conversationLabel(row);
  const heading = item.querySelector(".conversation-title");
  heading.textContent = shortLabel(label.text);
  heading.title = label.text;
  heading.classList.toggle("untitled", label.untitled);
  const meta = item.querySelector(".conversation-source");
  const client = document.createElement("span");
  client.textContent = sourceName(row.source);
  meta.append(client);
  if (row.project_name) {
    const project = document.createElement("span");
    project.className = "conversation-project";
    project.textContent = row.project_name;
    meta.append(project);
  }
  if (row.device_name) {
    const device = document.createElement("span");
    device.textContent = row.device_name;
    device.title = row.device_name;
    meta.append(device);
  }
  const name = document.createElement("span");
  name.className = "conversation-name";
  const fullName = row.name || row.title || "Conversation sans titre";
  name.textContent = shortLabel(fullName);
  name.title = fullName;
  meta.append(name);
  item.addEventListener("click", () => openConversation(row));
  item.addEventListener("keydown", (event) => {
    if (event.key === "Enter" || event.key === " ") { event.preventDefault(); openConversation(row); }
  });
  const stamp = row.updated_at || row.created_at || "";
  item.querySelector("time").textContent = when(stamp);
  item.querySelector("time").dateTime = stamp;
  return item;
}
function renderConversations(rows, { heading = null, empty, foot = "" } = {}) {
  const list = $("#conversations");
  list.replaceChildren();
  if (!rows.length) {
    const p = document.createElement("p");
    p.className = "empty";
    p.textContent = empty;
    list.append(p);
  }
  let group = null;
  for (const row of rows) {
    const label =
      heading || (dayIndex(row.updated_at || row.created_at) <= 0 ? "Aujourd'hui" : "Plus tôt");
    if (label !== group) {
      group = label;
      const title = document.createElement("div");
      title.className = "grouplabel";
      title.textContent = label;
      list.append(title);
    }
    list.append(conversationItem(row));
  }
  $("#history-count").textContent = foot;
}
function renderRecent() {
  const rows = current?.recent_conversations || [];
  const total = current?.conversation_count || 0;
  renderConversations(rows, {
    empty: "Aucune conversation importée pour le moment.",
    foot:
      total > rows.length
        ? `Les ${rows.length} plus récentes sur ${total.toLocaleString("fr-FR")}. La recherche retrouve toutes les autres.`
        : "",
  });
}
async function loadConversations(append = false) {
  const text = view === "conv" ? query() : "";
  $("#project-filter").hidden = !projectFilter;
  $("#project-filter-name").textContent = projectFilter?.name || "";
  if (!append) { results = null; nextOffset = 0; }
  const seq = ++searchSeq;
  libraryLoading = true;
  $("#conversations-more").disabled = true;
  updateHead();
  const params = new URLSearchParams({ limit: "50", offset: String(nextOffset) });
  if (text) params.set("q", text);
  if (projectFilter) params.set("project", projectFilter.id);
  if ($("#device-filter").value) params.set("device", $("#device-filter").value);
  if ($("#source-filter").value) params.set("source", $("#source-filter").value);
  for (const id of conversationSelection.keys()) params.append("conversation", id);
  try {
    const res = await fetch(`/api/conversations?${params}`, { signal: AbortSignal.timeout(15000) });
    if (!res.ok) throw new Error("Recherche impossible pour le moment.");
    const data = await res.json();
    const rows = data.conversations || [];
    if (seq !== searchSeq) return;
    results = [...new Map([...(append ? results || [] : []), ...rows].map(row => [row.id, row])).values()];
    nextOffset = data.next_offset || results.length;
    hasMore = !!data.has_more;
    $("#conversations-more").hidden = !hasMore;
    renderConversations(results, text
      ? { heading: "Résultats", empty: `Aucune conversation ne correspond à « ${text} ».` }
      : { empty: "Aucune conversation pour ces filtres.", foot: `${plural(results.length, "conversation")}${hasMore ? " affichées" : ""}` });
    updateHead();
  } catch (error) {
    if (seq === searchSeq) notice(error.message, true);
  } finally {
    if (seq === searchSeq) { libraryLoading = false; $("#conversations-more").disabled = false; }
  }
}
async function loadDevices() {
  try {
    const response = await fetch("/api/devices", { signal: AbortSignal.timeout(15000) });
    if (!response.ok) return;
    const data = await response.json();
    const select = $("#device-filter"), selected = select.value;
    select.querySelectorAll("option[data-device]").forEach(option => option.remove());
    for (const device of data.devices || []) {
      const option = document.createElement("option");
      option.value = device.id;
      option.dataset.device = "true";
      option.textContent = `${device.name}${device.is_current ? " (cet ordinateur)" : ""}`;
      select.append(option);
    }
    select.value = selected;
  } catch { /* Keep the current device selection on a temporary network failure. */ }
}
function updateConversationSelection() {
  const count = conversationSelection.size;
  $("#conversation-filter-label").textContent = count ? `${plural(count, "conversation")} sélectionnée${count > 1 ? "s" : ""}` : "Toutes les conversations";
  $("#conversation-filter-clear").hidden = !count;
  $("#conversation-filter-options").querySelectorAll("input").forEach(input => {
    input.checked = conversationSelection.has(input.value);
    input.disabled = count >= 200 && !input.checked;
  });
  if ($("#conversation-filter").open) positionConversationMenu();
}
async function loadConversationOptions(append = false) {
  const seq = ++optionSeq;
  if (!append) { optionOffset = 0; $("#conversation-filter-options").replaceChildren(); }
  $("#conversation-filter-more").disabled = true;
  $("#conversation-filter-status").textContent = "Chargement...";
  try {
    const params = new URLSearchParams({q: $("#conversation-filter-search").value.trim(), offset: String(optionOffset)});
    const response = await fetch(`/api/conversation-options?${params}`, {signal: AbortSignal.timeout(15000)});
    if (!response.ok) throw new Error("Chargement impossible. Rouvrez le filtre pour réessayer.");
    const data = await response.json();
    if (seq !== optionSeq) return;
    for (const row of data.conversations || []) {
      const label = document.createElement("label"), input = document.createElement("input"), copy = document.createElement("span");
      input.type = "checkbox";
      input.value = row.id;
      input.checked = conversationSelection.has(row.id);
      const name = document.createElement("span"), meta = document.createElement("small");
      name.textContent = shortLabel(row.name);
      name.title = row.name;
      meta.textContent = [row.project_name, row.device_name].filter(Boolean).join(" · ");
      copy.append(name, meta);
      input.addEventListener("change", () => {
        if (input.checked) conversationSelection.set(row.id, row.name);
        else conversationSelection.delete(row.id);
        updateConversationSelection();
        loadConversations();
      });
      label.append(input, copy);
      $("#conversation-filter-options").append(label);
    }
    optionOffset = data.next_offset;
    $("#conversation-filter-more").hidden = !data.has_more;
    $("#conversation-filter-status").textContent = optionOffset ? "" : "Aucune conversation trouvée.";
    updateConversationSelection();
  } catch (error) {
    if (seq === optionSeq) $("#conversation-filter-status").textContent = error.message;
  } finally { if (seq === optionSeq) $("#conversation-filter-more").disabled = false; }
}
$("#conversation-filter").addEventListener("toggle", () => {
  if ($("#conversation-filter").open) { positionConversationMenu(); loadConversationOptions(); }
});
function positionConversationMenu() {
  const menu = $(".conversation-filter-menu");
  menu.style.left = "0px";
  menu.style.top = "calc(100% + 6px)";
  menu.style.bottom = "auto";
  menu.style.maxHeight = "";
  const anchor = $("#conversation-filter summary").getBoundingClientRect();
  const height = window.visualViewport?.height || window.innerHeight;
  const below = height - anchor.bottom - 22, above = anchor.top - 22;
  const flip = menu.scrollHeight > below && above > below;
  if (flip) { menu.style.top = "auto"; menu.style.bottom = "calc(100% + 6px)"; }
  menu.style.maxHeight = `${Math.max(100, flip ? above : below)}px`;
  const bounds = menu.getBoundingClientRect();
  menu.style.left = `${Math.min(0, window.innerWidth - 16 - bounds.right)}px`;
}
window.addEventListener("resize", () => {
  if ($("#conversation-filter").open) positionConversationMenu();
});
window.visualViewport?.addEventListener("resize", () => {
  if ($("#conversation-filter").open) positionConversationMenu();
});
$("#conversation-filter-search").addEventListener("input", () => {
  clearTimeout(optionTimer);
  optionSeq += 1;
  optionTimer = setTimeout(() => loadConversationOptions(), 180);
});
$("#conversation-filter-more").addEventListener("click", () => loadConversationOptions(true));
$("#conversation-filter-clear").addEventListener("click", () => {
  conversationSelection.clear();
  updateConversationSelection();
  loadConversations();
});
document.addEventListener("click", event => {
  if (!$("#conversation-filter").contains(event.target)) $("#conversation-filter").open = false;
});
$("#conversation-filter").addEventListener("keydown", event => {
  if (event.key === "Escape") {
    $("#conversation-filter").open = false;
    $("#conversation-filter summary").focus();
  }
});
let detailId = null, detailOffset = 0, detailSeq = 0;
async function openConversation(row) {
  detailId = row.id;
  detailOffset = 0;
  $("#conversation-detail-title").textContent = row.name || row.title || "Conversation sans titre";
  $("#conversation-detail-meta").textContent = [row.project_name, sourceName(row.source), row.device_name].filter(Boolean).join(" · ");
  $("#conversation-messages").replaceChildren();
  $("#conversation-detail").showModal();
  await loadMessages();
}
async function loadMessages() {
  const seq = ++detailSeq;
  $("#conversation-messages-more").disabled = true;
  try {
    const params = new URLSearchParams({ id: detailId, offset: String(detailOffset) });
    const response = await fetch(`/api/conversation?${params}`, { signal: AbortSignal.timeout(15000) });
    if (!response.ok) throw new Error("Lecture de la conversation impossible.");
    const page = (await response.json()).conversation;
    if (seq !== detailSeq) return;
    if (!page) throw new Error("Conversation indisponible dans l'index.");
    for (const message of page.messages || []) {
      const article = document.createElement("article"), label = document.createElement("h3"), content = document.createElement("p");
      article.className = "conversation-message";
      label.textContent = `${message.role === "user" ? "Vous" : "Assistant"} · ${date(message.timestamp)}`;
      content.textContent = message.content + (message.truncated ? "\n[Extrait limité à 4 000 caractères]" : "");
      article.append(label, content);
      $("#conversation-messages").append(article);
    }
    if (!page.messages?.length && detailOffset === 0) $("#conversation-messages").textContent = "Aucun message disponible.";
    detailOffset = page.next_offset;
    $("#conversation-messages-more").hidden = detailOffset === null;
  } catch (error) {
    if (seq === detailSeq) $("#conversation-detail-meta").textContent = error.message;
  } finally { if (seq === detailSeq) $("#conversation-messages-more").disabled = false; }
}
$("#conversation-detail-close").addEventListener("click", () => $("#conversation-detail").close());
$("#conversation-detail").addEventListener("close", () => { detailSeq += 1; });
$("#conversation-messages-more").addEventListener("click", () => loadMessages());
$("#conversations-more").addEventListener("click", () => loadConversations(true));
for (const id of ["device-filter", "source-filter"]) $("#" + id).addEventListener("change", () => loadConversations());
async function loadProjects() {
  try {
    const res = await fetch("/api/projects", { signal: AbortSignal.timeout(15000) });
    if (!res.ok) throw new Error();
    projects = ((await res.json()).projects || []).sort(
      (a, b) => (Date.parse(b.latest_conversation_at) || 0) - (Date.parse(a.latest_conversation_at) || 0),
    );
  } catch (error) {
    if (projects.length) return;
  }
  renderProjects();
}
function renderProjects() {
  const text = view === "proj" ? query().toLowerCase() : "";
  const rows = projects.filter((project) =>
    !text || [project.name, project.cwd].some((value) => String(value || "").toLowerCase().includes(text)),
  );
  const list = $("#projects");
  list.replaceChildren();
  if (!rows.length) {
    const p = document.createElement("p");
    p.className = "empty";
    p.textContent = text ? "Aucun projet ne correspond." : "Aucun projet pour le moment.";
    list.append(p);
  }
  for (const project of rows) {
    const item = document.createElement("button");
    item.className = "project";
    item.title = project.cwd || project.name || "";
    item.innerHTML =
      '<svg><use href="#i-folder"></use></svg><span class="p-name"></span><span class="p-count"></span><span class="p-time"></span>';
    const name = project.name || project.cwd || "Projet sans nom";
    item.querySelector(".p-name").textContent = name;
    item.querySelector(".p-count").textContent = plural(project.conversation_count, "conversation");
    item.querySelector(".p-time").textContent = when(project.latest_conversation_at, "aujourd'hui");
    item.addEventListener("click", () => {
      projectFilter = { id: project.id, name, count: project.conversation_count };
      setView("conv");
    });
    list.append(item);
  }
}
const storageParts = [
  ["normalized_bytes", "Conversations consultables"],
  ["raw_bytes", "Sources originales"],
  ["revisions_bytes", "Versions archivées"],
  ["cache_bytes", "Cache et transferts en attente"],
  ["database_bytes", "Index de recherche"],
];
function renderStorage(storage) {
  const bytes = (key) => Math.max(0, Number(storage[key] || 0));
  const total = bytes("total_bytes");
  const parts = storageParts.map(([key, label]) => [label, bytes(key)]);
  const otherArchives = bytes("archive_bytes") - bytes("normalized_bytes") - bytes("raw_bytes") - bytes("revisions_bytes");
  if (otherArchives > 0) parts.push(["Autres archives", otherArchives]);
  const rest = total - bytes("archive_bytes") - bytes("cache_bytes") - bytes("database_bytes");
  if (rest > 0) parts.push(["Journaux et état", rest]);
  parts.sort((a, b) => b[1] - a[1]);
  $("#total-size").textContent = size(total);
  const bar = $("#store-bar");
  const list = $("#store-list");
  bar.replaceChildren();
  list.replaceChildren();
  parts.forEach(([label, value], index) => {
    const color = `var(--sw-${index + 1})`;
    if (total && value) {
      const segment = document.createElement("i");
      segment.style.width = `${(100 * value) / total}%`;
      segment.style.background = color;
      bar.append(segment);
    }
    const row = document.createElement("div");
    row.className = "store-row";
    row.innerHTML = '<span class="s-left"><span class="s-swatch"></span><span></span></span><span class="s-val"></span>';
    row.querySelector(".s-swatch").style.background = color;
    row.querySelector(".s-left > span:last-child").textContent = label;
    row.querySelector(".s-val").textContent = size(value);
    list.append(row);
  });
}
function updateHead() {
  const data = current || {};
  const storage = data.storage || {};
  const provider = storage.provider_label || providerNames[storage.provider] || "cloud";
  const text = query();
  $("#view-title").textContent = { conv: "Conversations", proj: "Projets", store: "Stockage" }[view];
  let subtitle = null;
  if (view === "conv" && text)
    subtitle = results === null
      ? "Recherche…"
      : `${plural(results.length, "résultat")} pour « ${text} »${projectFilter ? ` dans ${projectFilter.name}` : ""}`;
  else if (view === "conv" && projectFilter)
    subtitle = `${plural(projectFilter.count, "conversation")} dans ce projet`;
  else if (view === "conv" && current)
    subtitle = `${plural(data.archive_count, "conversation")} · Tous les appareils${storage.cloud_sync === "configured" ? ` · ${provider}` : " · Cloud non connecté"}`;
  else if (view === "proj" && current)
    subtitle = `${plural(data.project_count, "projet")} ${data.project_count > 1 ? "suivis" : "suivi"}`;
  else if (view === "store") subtitle = "Répartition sur cet ordinateur";
  if (subtitle) $("#overview-subtitle").textContent = subtitle;
}
function setView(next) {
  view = next;
  document.querySelectorAll(".navi").forEach((button) => {
    if (button.dataset.view === next) button.setAttribute("aria-current", "page");
    else button.removeAttribute("aria-current");
  });
  for (const name of ["conv", "proj", "store"]) $(`#view-${name}`).hidden = name !== next;
  $("#search").value = "";
  $("#search-box").hidden = next === "store";
  $("#search").placeholder = next === "proj" ? "Rechercher un projet" : "Rechercher";
  $("#search").setAttribute("aria-label", next === "proj" ? "Rechercher un projet" : "Rechercher dans les conversations");
  $(".main-scroll").scrollTop = 0;
  if (next === "conv") loadConversations();
  if (next === "proj") {
    renderProjects();
    loadProjects();
  }
  updateHead();
}
$("#nav").addEventListener("click", (event) => {
  const button = event.target.closest(".navi");
  if (!button) return;
  if (button.dataset.view === "conv") projectFilter = null;
  setView(button.dataset.view);
});
$("#project-filter-clear").addEventListener("click", () => {
  projectFilter = null;
  loadConversations();
});
$("#search").addEventListener("input", () => {
  clearTimeout(searchTimer);
  if (view === "proj") renderProjects();
  else searchTimer = setTimeout(loadConversations, 220);
});
document.addEventListener("keydown", (event) => {
  if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "f" && view !== "store" && !$("#settings").open) {
    event.preventDefault();
    $("#search").focus();
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
  $("#conversation-count").textContent = (
    data.conversation_count || 0
  ).toLocaleString("fr-FR");
  $("#project-count").textContent = (data.project_count || 0).toLocaleString(
    "fr-FR",
  );
  $("#nav-storage").textContent = size(storage.total_bytes);
  $("#archive-size").textContent = size(storage.archive_bytes);
  renderStorage(storage);
  $("#app-version").textContent = data.version ? `Version ${data.version}` : "";
  const retention = data.retention || {};
  $("#cleanup-size").textContent = retention.last_run_at
    ? `${size(retention.freed_bytes)} retir\u00e9s du Mac ${elapsed(retention.last_run_at)}`
    : data.cleanup?.last_run_at
      ? `${size(data.cleanup.freed_bytes)} lib\u00e9r\u00e9s ${elapsed(data.cleanup.last_run_at)}`
    : "En attente";
  $("#archive-path").textContent = storage.archive || "";
  $("#archive-path").title = storage.archive || "";
  $("#watcher-detail").textContent =
    watcher.last_error ||
    (health.state === "stale"
      ? `Aucune collecte confirm\u00e9e depuis ${date(watcher.last_success_at || watcher.started_at)}`
      : `Scan ${elapsed(watcher.last_success_at)}`);
  $("#watcher-enable").hidden = healthy && !!installed;
  $("#watcher-enable").textContent = ["error", "stale"].includes(health.state)
    ? "R\u00e9parer" : watcher.running
    ? "Automatiser"
    : "Activer";
  $("#autostart-status").textContent = installed
    ? "Service install\u00e9"
    : "Non configur\u00e9e";
  $("#autostart-enable").hidden = !!installed && healthy;
  $("#autostart-check").hidden = !installed || !healthy;
  $("#autostart-enable").textContent = installed ? "R\u00e9parer" : "Activer";
  const mcpClients = Object.values(data.mcp_clients || {}).filter(
    (client) => client.available,
  );
  const readyMcpClients = mcpClients
    .filter((client) => client.configured)
    .map((client) => client.label);
  const missingMcpClients = mcpClients
    .filter((client) => !client.configured)
    .map((client) => client.label);
  $("#mcp-status").textContent = data.mcp_configured
    ? `Configur\u00e9 pour ${readyMcpClients.join(", ")}`
    : missingMcpClients.length
      ? `\u00c0 activer pour ${missingMcpClients.join(", ")}`
      : "Non configur\u00e9";
  $("#mcp-enable").hidden = false;
  $("#mcp-enable").textContent = data.mcp_configured ? "R\u00e9parer" : "Activer";
  $("#menubar-setting").hidden = !data.menubar_available;
  if (!themePending) applyTheme(data.theme || "system");
  $("#menubar-login").checked = !!data.menubar_login;
  if (data.status_icon_platform)
    document.documentElement.dataset.platform = data.status_icon_platform;
  $("#status-icon-label").textContent = data.status_icon_platform === "windows"
    ? "Icône dans la zone de notification"
    : "Icône dans la barre de menus";
  $("#status-icon-help").textContent = "Lancement à l'ouverture de session";
  $("#on-computer").textContent = data.menubar_available && data.status_icon_platform === "mac"
    ? "Sur votre Mac"
    : "Sur cet ordinateur";
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
    waiting_local_cloud: "En attente du dossier cloud local",
    paused: "Transferts suspendus",
  };
  let cloudText = configured
    ? phases[sync.status] || "En attente de synchronisation"
    : "Aucun compte connect\u00e9";
  if (configured && sync.status === "synced")
    cloudText += `, ${elapsed(sync.last_success_at)}`;
  else if (configured && data.sync_active) {
    if (sync.totalTransfers > 0)
      cloudText += `, ${sync.transfers || 0} / ${sync.totalTransfers} objets` +
        (sync.confirmedBefore > 0 ? `, ${sync.confirmedBefore} d\u00e9j\u00e0 confirm\u00e9s` : "");
    else if (sync.totalBytes > 0)
      cloudText += `, ${size(sync.bytes)} sur ${size(sync.totalBytes)}, ${size(sync.speed)}/s`;
  }
  else if (configured && !["error", "synced", "paused", "waiting_local_cloud"].includes(sync.status))
    cloudText = "En attente de synchronisation";
  if (sync.error && configured) cloudText += ` : ${sync.error}`;
  if (sync.requires_action) cloudText += " Relance manuelle requise.";
  else if (sync.status === "error" && sync.retry_after)
    cloudText += " Nouvelle tentative automatique pr\u00e9vue.";
  if (data.sync_paused && !data.sync_active) cloudText = "Transferts suspendus";
  if (sync.status === "synced" && sync.confirmation === "local_folder")
    cloudText = `Copie locale \u00e0 jour, envoi cloud g\u00e9r\u00e9 par ${provider}`;
  if (data.sync_active && age(sync.heartbeat_at || sync.started_at) > 180)
    cloudText = `Synchronisation \u00e0 v\u00e9rifier, derni\u00e8re activit\u00e9 ${elapsed(sync.heartbeat_at || sync.started_at)}`;
  $("#cloud-detail").textContent = cloudText;
  $("#cloud-progress").hidden = !configured || !data.sync_active;
  if (sync.totalTransfers > 0)
    $("#cloud-progress").value = Math.min(100, 100 * (sync.transfers || 0) / sync.totalTransfers);
  else if (sync.totalBytes > 0)
    $("#cloud-progress").value = Math.min(
      100,
      (100 * (sync.bytes || 0)) / sync.totalBytes,
    );
  else $("#cloud-progress").removeAttribute("value");
  $("#cloud-setup").hidden = configured;
  $('[data-action="sync"]').hidden = !configured;
  $("#sync-pause").hidden = !configured || !data.sync_active;
  $('[data-action="sync"]').title = data.sync_paused ? "Reprendre les transferts" : "Synchroniser maintenant";
  $('[data-action="sync"]').setAttribute("aria-label", $('[data-action="sync"]').title);
  $("#cloud-connect-form").hidden = configured && !switchingCloud;
  $("#cloud-manage").hidden = !configured;
  $("#cloud-folder-settings").hidden = !configured;
  if (document.activeElement !== $("#cloud-folder") &&
      $("#cloud-folder").dataset.saved !== storage.remote) {
    $("#cloud-folder").value = storage.remote || "AI-Memory";
    $("#cloud-folder").dataset.saved = storage.remote || "AI-Memory";
  }
  $("#cloud-folder-help").textContent = storage.provider === "google-drive"
    ? "Chemin dans Google Drive. Avec l'autorisation actuelle, seuls les dossiers accessibles \u00e0 AI Memory sont utilisables."
    : "Les sauvegardes existantes restent dans l'ancien dossier. La copie vers le nouveau dossier est automatique.";
  $("#cloud-folder-pick").hidden = !data.backup_folder_picker;
  $("#folder-pick").hidden = !data.folder_picker;
  $("#cloud-summary").textContent = configured
    ? `${provider}, dossier ${storage.remote}`
    : "Aucune destination connect\u00e9e";
  $("#cloud-reconnect").hidden = !["google-drive", "icloud-online", "onedrive-online", "dropbox-online"].includes(storage.provider);
  $("#cloud-last").textContent =
    `Dernier transfert termin\u00e9 : ${date(sync.last_success_at)}. ${sync.object_count || 0} objets. ` +
    (sync.confirmation === "local_folder" ? "Copies locales conserv\u00e9es : l'envoi au cloud d\u00e9pend du client du Mac." :
      "Seules les archives confirm\u00e9es sur cette destination sont nettoy\u00e9es du Mac.");
  const audit = data.audit || {};
  $("#audit-detail").textContent = audit.checked_at
    ? `${audit.verified_files} sources v\u00e9rifi\u00e9es sur ${audit.files}, ${(audit.issues || []).length} erreurs, ${(audit.changing_files || []).length} sources modifi\u00e9es depuis, ${(audit.missing_thread_ids || []).length} conversations sans source. ${date(audit.checked_at)}.`
    : "Aucune v\u00e9rification effectu\u00e9e.";
  $("#watcher-diagnostic").textContent =
    `Dernier scan : ${date(watcher.last_scan_at)}. ${watcher.scanned || 0} fichiers examin\u00e9s, ${watcher.imported || 0} import\u00e9s, ${watcher.skipped || 0} inchang\u00e9s.`;
  if (!browsing()) renderRecent();
  const revision = `${data.conversation_count}:${sync.last_success_at}:${watcher.last_success_at}`;
  if (libraryRevision !== revision) {
    libraryRevision = revision;
    loadDevices();
    if (view === "conv" && !libraryLoading && nextOffset <= 50) loadConversations();
  }
  $("#library-sync").textContent = storage.cloud_sync !== "configured" ? "Cloud non connecté" :
    data.sync_paused || sync.status === "paused" ? "Synchro en pause · Bibliothèque reçue" :
    sync.status === "waiting_local_cloud" ? "Réception en attente · Bibliothèque reçue" :
    sync.status === "success" || sync.status === "synced" ? `Dernière synchro : ${date(sync.last_success_at)}` :
    sync.status === "error" ? "Synchronisation interrompue · Bibliothèque reçue" :
    "Bibliothèque reçue · Synchronisation en cours";
  if (view === "proj") loadProjects();
  updateHead();
  const pendingIcloud = data.icloud_auth?.status === "needs_2fa";
  if (data.oauth_auth?.status === "needs_selection" && !providerChosen) {
    $("#provider").value = data.oauth_auth.provider;
    switchingCloud = true;
    $("#cloud-connect-form").hidden = false;
    settings();
  }
  const choices = data.oauth_auth?.choices || [];
  const savedChoice = $("#cloud-selection").value;
  $("#cloud-selection").replaceChildren(...choices.map(item => {
    const option = document.createElement("option");
    option.value = item.value;
    option.textContent = item.label;
    return option;
  }));
  if (choices.some(item => item.value === savedChoice)) $("#cloud-selection").value = savedChoice;
  const pendingIcloudWebApproval = ["needs_web_approval", "needs_access_retry"].includes(data.icloud_auth?.status);
  const pendingIcloudTerms = data.icloud_auth?.status === "needs_terms_acceptance";
  if (data.icloud_auth?.status === "checking_access") {
    icloudAwaiting2FA = icloudAwaitingWebApproval = icloudAwaitingTerms = false;
    switchingCloud = false;
    notice(data.icloud_auth.message);
  }
  if (
    (icloudAwaiting2FA || icloudAwaitingWebApproval || icloudAwaitingTerms) &&
    !pendingIcloud &&
    !pendingIcloudWebApproval &&
    !pendingIcloudTerms &&
    !data.icloud_auth?.status && configured &&
    storage.provider === "icloud-online"
  ) {
    icloudAwaiting2FA = false;
    icloudAwaitingWebApproval = false;
    icloudAwaitingTerms = false;
    switchingCloud = false;
    $("#icloud-2fa").value = "";
    notice("iCloud connect\u00e9. La sauvegarde poss\u00e8de son propre suivi ci-dessus.");
    $("#cloud-connect-form").hidden = true;
  }
  if (!providerChosen && pendingIcloudTerms && !icloudAwaitingTerms) {
    $("#provider").value = "icloud-online";
    revealIcloudTerms();
  } else if (!providerChosen && pendingIcloudWebApproval && !icloudAwaitingWebApproval) {
    $("#provider").value = "icloud-online";
    revealIcloudWebApproval();
  } else if (!providerChosen && pendingIcloud && !icloudAwaiting2FA) {
    $("#provider").value = "icloud-online";
    revealIcloud2FA();
  } else if (data.job?.error && data.sync_active && !sync.error) {
    lastJobMessage = data.job.message;
    notice("");
  } else if (data.job?.message && data.job.message !== lastJobMessage) {
    lastJobMessage = data.job.message;
    notice(data.job.message, data.job.error);
  }
  updateProviderFields();
  icons();
  busy();
}
async function refresh() {
  try {
    const res = await fetch("/api/status", {
      signal: AbortSignal.timeout(15000),
    });
    if (!res.ok) throw new Error("Lecture impossible");
    const data = await res.json();
    refreshFailures = 0;
    if (connectionNotice) {
      notice("");
      connectionNotice = false;
    }
    render(data);
  } catch (error) {
    refreshFailures += 1;
    if (refreshFailures >= 3) {
      $("#health-badge").dataset.state = "error";
      $("#health-label").textContent = "Interface d\u00e9connect\u00e9e";
      connectionNotice = true;
      notice(
        "Connexion locale perdue. AI Memory tente de se reconnecter automatiquement.",
        true,
      );
    }
  }
}
async function action(name, body = {}) {
  if (requestBusy || (current?.job?.running && !["open-folder", "pause-sync", "menubar-login"].includes(name))) return;
  requestBusy = true;
  busy();
  notice("Op\u00e9ration en cours\u2026");
  try {
    if (name === "connect-cloud") {
      providerChosen = false;
      const file = $("#oauth-client").files[0];
      body = {
        provider: $("#provider").value,
        folder: $("#folder").value,
        oauth_client: file ? JSON.parse(await file.text()) : null,
        icloud_apple_id:
          icloudAwaiting2FA || icloudAwaitingWebApproval || icloudAwaitingTerms
          ? ""
          : $("#icloud-apple-id").value,
        icloud_password:
          icloudAwaiting2FA || icloudAwaitingWebApproval || icloudAwaitingTerms
          ? ""
          : $("#icloud-password").value,
        icloud_2fa: icloudAwaiting2FA ? $("#icloud-2fa").value : "",
        icloud_resume: icloudAwaitingWebApproval,
        icloud_restart_after_terms: icloudAwaitingTerms,
        onedrive_type: $("#onedrive-type").value,
        reauthenticate: reauthenticating,
        cloud_selection: $("#provider").value === current?.oauth_auth?.provider &&
          current?.oauth_auth?.status === "needs_selection" ? $("#cloud-selection").value : null,
      };
    }
    if (name === "cloud-folder") body = {folder: $("#cloud-folder").value};
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
    if (name === "connect-cloud" || name === "disconnect-cloud")
      switchingCloud = false;
    if (name === "connect-cloud" || name === "disconnect-cloud")
      icloudAwaiting2FA = false;
    if (name === "connect-cloud" || name === "disconnect-cloud")
      icloudAwaitingWebApproval = false;
    if (name === "connect-cloud" || name === "disconnect-cloud")
      icloudAwaitingTerms = false;
    if (name === "reset-icloud") {
      icloudAwaiting2FA = icloudAwaitingWebApproval = icloudAwaitingTerms = false;
      $("#icloud-password").value = $("#icloud-apple-id").value = $("#icloud-2fa").value = "";
      switchingCloud = true;
    }
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
$("#theme").addEventListener("click", async (event) => {
  const choice = event.target.closest("[data-theme-choice]");
  if (!choice) return;
  themePending = true;
  applyTheme(choice.dataset.themeChoice);
  try {
    await fetch("/api/theme", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-AI-Memory-Token": token },
      body: JSON.stringify({ theme: choice.dataset.themeChoice }),
    });
  } catch (error) {
  } finally {
    themePending = false;
  }
});
$("#menubar-login").addEventListener("change", async (event) => {
  event.target.disabled = true;
  await action("menubar-login", { enabled: event.target.checked });
  event.target.disabled = false;
});
$("#cloud-folder-pick").addEventListener("click", () =>
  pickFolder("backup", $("#cloud-folder"), $('[data-action="cloud-folder"]')),
);
$("#folder-pick").addEventListener("click", () =>
  pickFolder("connect", $("#folder"), $('[data-action="connect-cloud"]')),
);
$("#cloud-switch").addEventListener("click", () => {
  reauthenticating = false;
  providerChosen = true;
  switchingCloud = true;
  icloudAwaiting2FA = false;
  icloudAwaitingWebApproval = false;
  icloudAwaitingTerms = false;
  const provider = current?.storage?.provider;
  if (provider && $("#provider").querySelector(`option[value="${provider}"]`))
    $("#provider").value = provider;
  render(current);
  $("#provider").focus();
});
$("#cloud-reconnect").addEventListener("click", () => {
  $("#cloud-switch").click();
  reauthenticating = true;
  updateProviderFields();
});
function updateProviderFields() {
  const value = $("#provider").value;
  const google = value === "google-drive";
  const icloudOnline = value === "icloud-online";
  const onedriveOnline = value === "onedrive-online";
  const customFolder = value === "local-folder";
  $("#folder-label").hidden = !customFolder;
  $("#oauth-settings").hidden = !google;
  $("#icloud-fields").hidden = !icloudOnline;
  $("#icloud-reset").hidden = !icloudOnline;
  const checking = current?.icloud_auth?.status === "checking_access";
  $("#icloud-credentials").hidden =
    icloudOnline &&
    (checking || icloudAwaiting2FA || icloudAwaitingWebApproval || icloudAwaitingTerms);
  $("#icloud-2fa-label").hidden = !icloudOnline || !icloudAwaiting2FA;
  $("#icloud-web-approval").hidden =
    !icloudOnline || !icloudAwaitingWebApproval || current?.icloud_auth?.status === "needs_access_retry";
  $("#icloud-access-retry").hidden = !icloudOnline || current?.icloud_auth?.status !== "needs_access_retry";
  $("#icloud-access-retry").textContent = current?.icloud_auth?.message || "";
  $("#icloud-terms").hidden = !icloudOnline || !icloudAwaitingTerms;
  $("#icloud-flow-help").hidden =
    true;
  $("#provider-note").hidden = icloudOnline && (icloudAwaiting2FA || icloudAwaitingWebApproval || icloudAwaitingTerms);
  $("#onedrive-fields").hidden = !onedriveOnline;
  $("#cloud-selection-label").hidden = !onedriveOnline || current?.oauth_auth?.status !== "needs_selection";
  const label = providerNames[value] || "la destination";
  $('[data-action="connect-cloud"]').textContent =
    icloudOnline && icloudAwaitingTerms
      ? "J'ai accept\u00e9, relancer"
      : icloudOnline && checking
      ? "V\u00e9rification de l'acc\u00e8s iCloud"
      : icloudOnline && icloudAwaitingWebApproval
      ? "R\u00e9essayer iCloud"
      : icloudOnline && icloudAwaiting2FA
        ? "Confirmer le code iCloud"
        : `${reauthenticating ? "Reconnecter" : "Connecter"} ${label}`;
  $("#provider-note").textContent =
    icloudOnline && icloudAwaitingTerms
      ? icloudTermsMessage
      : icloudOnline && icloudAwaitingWebApproval
      ? icloudWebApprovalMessage
      : icloudOnline && icloudAwaiting2FA
        ? icloud2faMessage
        : providerNotes[value] || providerNotes["local-folder"];
}
$("#provider").addEventListener("change", () => {
  providerChosen = true;
  reauthenticating = false;
  icloudAwaiting2FA = false;
  icloudAwaitingWebApproval = false;
  icloudAwaitingTerms = false;
  $("#icloud-2fa").value = "";
  updateProviderFields();
});
updateProviderFields();
icons();
async function poll() {
  await refresh();
  setTimeout(poll, 5000);
}
poll();

function sourceName(source) {
  return (
    {
      codex: "Codex",
      claude: "Claude",
      "claude-code": "Claude Code",
      "claude-desktop": "Claude Desktop",
      vscode: "VS Code",
      "vscode-codex": "Codex VS Code",
      "codex-desktop": "Codex Desktop",
      "vscode-claude": "Claude VS Code",
    }[source] || source
  );
}
