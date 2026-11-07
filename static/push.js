"use strict";

const panel = document.querySelector(".push-panel");
const csrf = document.querySelector('meta[name="csrf-token"]').content;
const maxItems = Number(panel.dataset.maxItems);
const maxFile = Number(panel.dataset.maxFile);
const list = document.getElementById("items");
const text = document.getElementById("composer-text");
const fileInput = document.getElementById("file-input");
const items = new Map();
let latest = 0;
let pollTimer = 0;
let syncing = null;
let queue = Promise.resolve();

function formatBytes(value) {
  if (value >= 1e6) return `${(value / 1e6).toLocaleString("it-IT", { maximumFractionDigits: 1 })} MB`;
  if (value >= 1e3) return `${(value / 1e3).toLocaleString("it-IT", { maximumFractionDigits: 1 })} KB`;
  return `${value} B`;
}

function formatDate(iso) {
  const date = new Date(iso);
  const time = date.toLocaleTimeString("it-IT", { hour: "2-digit", minute: "2-digit" });
  if (date.toDateString() === new Date().toDateString()) return `Oggi, ${time}`;
  return `${date.toLocaleDateString("it-IT", { day: "numeric", month: "short", year: "numeric" })}, ${time}`;
}

function toast(message, error = false) {
  const node = document.getElementById("toast");
  node.textContent = message;
  node.classList.toggle("error", error);
  node.hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => { node.hidden = true; }, 4000);
}

async function api(url, options = {}) {
  const response = await fetch(url, { ...options, credentials: "same-origin", headers: { "X-CSRFToken": csrf, ...options.headers } });
  if (response.status === 401) { location.href = "/login/"; throw new Error("Sessione scaduta."); }
  const value = await response.json().catch(() => ({ error: "Richiesta non riuscita." }));
  if (!response.ok) throw new Error(value.error || "Richiesta non riuscita.");
  return value;
}

function button(icon, label, action, testid) {
  const node = document.createElement(action.startsWith("/") ? "a" : "button");
  node.className = "icon-button";
  node.title = label;
  node.setAttribute("aria-label", label);
  node.dataset.testid = testid;
  if (action.startsWith("/")) { node.href = action; node.setAttribute("download", ""); }
  else node.dataset.action = action;
  node.innerHTML = `<svg><use href="#${icon}"/></svg>`;
  return node;
}

function render(item) {
  const node = document.createElement("li");
  node.className = `item item-${item.kind}`;
  node.dataset.id = item.id;
  node.dataset.testid = "item";
  const body = document.createElement("div");
  body.className = "item-body";
  const actions = document.createElement("div");
  actions.className = "item-actions";
  if (item.kind === "text") {
    const content = document.createElement("div");
    content.className = "item-text";
    content.dataset.testid = "item-text";
    content.textContent = item.text;
    body.append(content);
    const url = item.text.trim();
    if (/^https?:\/\/\S+$/i.test(url)) {
      const open = document.createElement("a");
      open.className = "text-button";
      open.href = url;
      open.target = "_blank";
      open.rel = "noopener noreferrer";
      open.textContent = "Apri link";
      body.append(open);
    }
    actions.append(button("i-copy", "Copia testo", "copy", "copy"));
  } else {
    const fileUrl = `/api/items/${item.id}/file/`;
    if (item.image) {
      const image = document.createElement("img");
      image.className = "item-image";
      image.loading = "lazy";
      image.alt = item.name;
      image.src = `${fileUrl}?inline=1`;
      body.append(image);
    }
    const meta = document.createElement("div");
    meta.className = "file-meta";
    meta.innerHTML = '<span class="file-type"><svg><use href="#i-file"/></svg></span>';
    const name = document.createElement("strong");
    name.textContent = item.name;
    name.dataset.testid = "item-name";
    const size = document.createElement("small");
    size.textContent = formatBytes(item.size);
    const label = document.createElement("span");
    label.append(name, size);
    meta.append(label);
    body.append(meta);
    if (item.image) actions.append(button("i-copy", "Copia immagine", "copy", "copy"));
    actions.append(button("i-download", "Scarica", fileUrl, "download"));
  }
  actions.append(button("i-trash", "Elimina", "delete", "delete"));
  const footer = document.createElement("div");
  footer.className = "item-meta";
  const time = document.createElement("time");
  time.dateTime = item.created_at;
  time.textContent = formatDate(item.created_at);
  footer.append(time, actions);
  node.append(body, footer);
  return node;
}

function showCount() {
  document.querySelector("[data-count]").textContent = `${items.size} / ${maxItems} elementi`;
  document.getElementById("empty-state").hidden = items.size > 0;
}

function apply(value) {
  const current = new Set(value.ids);
  for (const [id, entry] of items) {
    if (!current.has(id)) { entry.node.remove(); items.delete(id); }
  }
  for (const item of value.items.slice().reverse()) {
    if (items.has(item.id) || !current.has(item.id)) continue;
    const node = render(item);
    const next = [...list.children].find(child => Number(child.dataset.id) < item.id);
    list.insertBefore(node, next || null);
    items.set(item.id, { item, node });
    latest = Math.max(latest, item.id);
  }
  showCount();
}

function sync() {
  if (syncing) return syncing;
  syncing = api(`/api/items/?after=${latest}`)
    .then(value => { apply(value); document.querySelector("[data-live-dot]").classList.remove("offline"); })
    .catch(() => { document.querySelector("[data-live-dot]").classList.add("offline"); })
    .finally(() => { syncing = null; schedule(); });
  return syncing;
}

function schedule() {
  clearTimeout(pollTimer);
  pollTimer = setTimeout(sync, document.hidden ? 15000 : 2000);
}

async function sendText() {
  const value = text.value;
  if (!value.trim()) return;
  const form = new FormData();
  form.append("text", value);
  try {
    await api("/api/items/", { method: "POST", body: form });
    text.value = "";
    await refresh();
  } catch (error) {
    toast(error.message, true);
  }
}

function refresh() {
  return (syncing || Promise.resolve()).then(sync);
}

function uploadOne(file) {
  const progress = document.getElementById("upload-progress");
  const bar = document.getElementById("upload-bar");
  document.getElementById("upload-name").textContent = file.name || "File incollato";
  progress.hidden = false;
  return new Promise((resolve, reject) => {
    const form = new FormData();
    form.append("file", file, file.name || "incollato.png");
    const request = new XMLHttpRequest();
    request.open("POST", "/api/items/");
    request.setRequestHeader("X-CSRFToken", csrf);
    request.upload.onprogress = event => {
      if (!event.lengthComputable) return;
      bar.value = event.loaded / event.total * 100;
      document.getElementById("upload-percent").textContent = `${Math.round(bar.value)}%`;
    };
    request.onload = () => {
      let value = {};
      try { value = JSON.parse(request.responseText); } catch { value = {}; }
      if (request.status === 201) resolve(value);
      else if (request.status === 413 && value.drive) { tooLarge([file]); reject(new Error("")); }
      else reject(new Error(value.error || "Invio non riuscito."));
    };
    request.onerror = () => reject(new Error("Connessione persa durante l'invio."));
    request.send(form);
  }).finally(() => { progress.hidden = true; bar.value = 0; });
}

function tooLarge(files) {
  document.getElementById("drive-name").textContent = files.map(file => `${file.name} (${formatBytes(file.size)})`).join(", ");
  document.getElementById("drive-dialog").showModal();
}

function sendFiles(files) {
  files = [...files];
  const large = files.filter(file => file.size > maxFile);
  if (large.length) tooLarge(large);
  for (const file of files.filter(entry => entry.size <= maxFile)) {
    queue = queue.then(() => uploadOne(file)).then(refresh, error => { if (error.message) toast(error.message, true); });
  }
}

async function pngBlob(id) {
  const blob = await (await fetch(`/api/items/${id}/file/?inline=1`, { credentials: "same-origin" })).blob();
  if (blob.type === "image/png") return blob;
  const bitmap = await createImageBitmap(blob);
  const canvas = document.createElement("canvas");
  canvas.width = bitmap.width;
  canvas.height = bitmap.height;
  canvas.getContext("2d").drawImage(bitmap, 0, 0);
  return new Promise(resolve => canvas.toBlob(resolve, "image/png"));
}

list.addEventListener("click", async event => {
  const target = event.target.closest("button[data-action]");
  if (!target) return;
  const id = Number(target.closest(".item").dataset.id);
  const entry = items.get(id);
  if (!entry) return;
  try {
    if (target.dataset.action === "copy" && entry.item.kind === "text") {
      await navigator.clipboard.writeText(entry.item.text);
      toast("Testo copiato.");
    } else if (target.dataset.action === "copy") {
      await navigator.clipboard.write([new ClipboardItem({ "image/png": pngBlob(id) })]);
      toast("Immagine copiata.");
    } else if (target.dataset.action === "delete") {
      await api(`/api/items/${id}/delete/`, { method: "POST" });
      entry.node.remove();
      items.delete(id);
      showCount();
    }
  } catch (error) {
    toast(error.message || "Operazione non riuscita.", true);
  }
});

document.getElementById("composer").addEventListener("submit", event => { event.preventDefault(); sendText(); });
text.addEventListener("keydown", event => {
  if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) { event.preventDefault(); sendText(); }
});
document.querySelector("[data-attach]").addEventListener("click", () => fileInput.click());
fileInput.addEventListener("change", () => { sendFiles(fileInput.files); fileInput.value = ""; });
document.addEventListener("paste", event => {
  if (!event.clipboardData) return;
  // Office copies carry a picture next to the text: the text wins.
  const value = event.clipboardData.getData("text");
  if (!value && event.clipboardData.files.length) { event.preventDefault(); sendFiles(event.clipboardData.files); return; }
  if (value && event.target !== text) { event.preventDefault(); text.focus(); text.setRangeText(value, text.selectionStart, text.selectionEnd, "end"); }
});
let dragDepth = 0;
document.addEventListener("dragenter", event => { if (event.dataTransfer.types.includes("Files")) { dragDepth++; document.body.classList.add("dragging"); } });
document.addEventListener("dragleave", () => { dragDepth = Math.max(0, dragDepth - 1); if (!dragDepth) document.body.classList.remove("dragging"); });
document.addEventListener("dragover", event => { if (event.dataTransfer.types.includes("Files")) event.preventDefault(); });
document.addEventListener("drop", event => {
  if (!event.dataTransfer.files.length) return;
  event.preventDefault();
  dragDepth = 0;
  document.body.classList.remove("dragging");
  sendFiles(event.dataTransfer.files);
});
document.addEventListener("visibilitychange", () => { if (!document.hidden) sync(); else schedule(); });
addEventListener("focus", () => sync());
sync();
