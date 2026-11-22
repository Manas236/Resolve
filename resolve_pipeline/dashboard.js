const root = document.documentElement;
try { root.dataset.theme = localStorage.getItem("resolve-theme") || "dark"; } catch {}
const main = () => document.querySelector("#pipeline");
const feedback = () => document.querySelector("#feedback");
let pending = 0;
let dismiss;
let activeReel;
let dragged;

function toast(message, failure = false) {
    feedback().textContent = message;
    feedback().classList.toggle("failure", failure);
    clearTimeout(dismiss);
    dismiss = setTimeout(() => { feedback().textContent = ""; }, 5000);
}

async function refreshStatus() {
    if (!main() || pending) return;
    try {
        const response = await fetch("/dashboard-status");
        if (!response.ok || pending) return;
        const html = new DOMParser().parseFromString(await response.text(), "text/html");
        if (!pending) document.querySelector("#status-bar")?.replaceWith(html.querySelector("#status-bar"));
    } catch {}
}

async function progress() {
    const target = document.querySelector("#renders");
    if (!target) return;
    try {
        const response = await fetch("/render-status");
        if (!response.ok) return;
        const rows = await response.json();
        const recent = rows.slice(0, 20);
        target.replaceChildren(...recent.map(row => {
            const line = document.createElement("p");
            line.textContent = `Reel ${row.id} · ${row.state}${row.error ? " · " + row.error : ""}`;
            line.className = row.state === "failed" ? "error" : "";
            return line;
        }));
        if (!recent.length) target.textContent = pending ? "Preparing render…" : "No renders yet.";
    } catch {}
}

async function drawWaves() {
    for (const canvas of document.querySelectorAll(".wave")) {
        try {
            const response = await fetch(canvas.dataset.src);
            if (!response.ok) throw new Error();
            const {peaks, duration, timestamp} = await response.json();
            canvas.width = 384;
            canvas.height = 64;
            const ctx = canvas.getContext("2d");
            const style = getComputedStyle(canvas);
            if (timestamp !== null && timestamp < duration) {
                ctx.fillStyle = style.getPropertyValue("--selection");
                ctx.fillRect(timestamp / duration * 384, 0, Math.min(22, duration - timestamp) / duration * 384, 64);
            }
            ctx.fillStyle = style.getPropertyValue("--accent");
            const pitch = 384 / peaks.length;
            peaks.forEach((peak, index) => {
                const height = Math.max(2, peak * 60);
                ctx.fillRect(index * pitch, (64 - height) / 2, Math.max(1, pitch - 1), height);
            });
        } catch { canvas.title = "Waveform unavailable"; }
        finally { canvas.dataset.loaded = "true"; }
    }
}

function syncQueue() {
    const rows = [...document.querySelectorAll(".queue-row")];
    const order = document.querySelector('[name="order"]');
    if (!order) return;
    order.value = rows.map(row => row.dataset.id).join(",");
    const list = document.querySelector("#queue-list");
    const slots = list.dataset.slots.split(",");
    rows.forEach((row, index) => {
        row.querySelector(".slot").textContent = slots[index] ? slots[index] + " " + list.dataset.zone : "Later";
    });
}

document.addEventListener("click", event => {
    const button = event.target.closest("button");
    if (!button) return;
    if (button.id === "theme") {
        root.dataset.theme = root.dataset.theme === "light" ? "dark" : "light";
        try { localStorage.setItem("resolve-theme", root.dataset.theme); } catch {}
        drawWaves();
    } else if (button.hasAttribute("data-add-slot")) {
        const label = document.createElement("label");
        label.innerHTML = 'New slot<input type="time" data-slot required><button type="button" data-remove-slot aria-label="Remove slot">×</button>';
        document.querySelector("#slots").append(label);
        label.querySelector("input").focus();
    } else if (button.hasAttribute("data-remove-slot")) {
        if (document.querySelectorAll("[data-slot]").length > 1) button.closest("label").remove();
    } else if (button.hasAttribute("data-move")) {
        const row = button.closest("li");
        if (button.dataset.move === "-1") row.previousElementSibling?.before(row);
        else row.nextElementSibling?.after(row);
        syncQueue();
        button.focus();
    }
});
document.addEventListener("dragstart", event => {
    dragged = event.target.closest(".queue-row");
    if (dragged) event.dataTransfer.setData("text/plain", dragged.dataset.id);
});
document.addEventListener("dragover", event => {
    if (dragged && event.target.closest(".queue-row")) event.preventDefault();
});
document.addEventListener("drop", event => {
    const row = event.target.closest(".queue-row");
    if (!row || !dragged || row === dragged) return;
    event.preventDefault();
    if (event.clientY < row.getBoundingClientRect().top + row.offsetHeight / 2) row.before(dragged);
    else row.after(dragged);
    syncQueue();
});
document.addEventListener("dragend", () => { dragged = null; });
document.addEventListener("input", event => {
    if (event.target.id === "log-filter") {
        const query = event.target.value.toLowerCase();
        document.querySelectorAll("#log-rows tr").forEach(row => {
            row.hidden = !row.textContent.toLowerCase().includes(query);
        });
    }
    if (event.target.hasAttribute("data-weight")) event.target.nextElementSibling.value = event.target.value;
});
document.addEventListener("focusin", event => {
    const card = event.target.closest(".reel-card");
    if (card) activeReel = card.dataset.reel;
});
document.addEventListener("keydown", event => {
    if (event.altKey || event.ctrlKey || event.metaKey || event.repeat ||
        event.target.closest("input,textarea,select,button,a,video,[contenteditable]")) return;
    const cards = [...document.querySelectorAll('.reel-card[data-state="review"]')];
    if (!cards.length || pending) return;
    const index = cards.findIndex(card => card.dataset.reel === activeReel);
    const key = event.key.toLowerCase();
    if (key === "j" || key === "k") {
        event.preventDefault();
        cards[Math.max(0, Math.min(cards.length - 1, index + (key === "j" ? 1 : -1)))].focus();
    } else {
        const action = {a: "approve", r: "reject", g: "regenerate"}[key];
        if (action) (cards[index] || cards[0]).querySelector(`form[action="/${action}"]`)?.requestSubmit();
    }
});

document.addEventListener("submit", async event => {
    const form = event.target;
    const action = new URL(form.action).pathname;
    if (!main() || action === "/logout") return;
    event.preventDefault();
    if (pending) return;
    if (action === "/settings") {
        form.elements.posting_slots.value = Array.from(form.querySelectorAll("[data-slot]"), input => input.value).join(",");
        form.elements.format_weights.value = Array.from(form.querySelectorAll("[data-weight]"), input => input.value).join(",");
    }
    const inline = form.querySelector(".inline-error");
    if (inline) inline.textContent = "";
    const button = form.querySelector('button:not([type="button"])');
    const label = button.textContent;
    button.disabled = true;
    button.textContent = "Working…";
    form.setAttribute("aria-busy", "true");
    pending++;
    try {
        const response = await fetch(form.action, {method: "POST", body: new URLSearchParams(new FormData(form))});
        if (!response.ok) {
            const error = new DOMParser().parseFromString(await response.text(), "text/html");
            throw new Error([...error.querySelectorAll("p")].pop()?.textContent.replace(/^Error code explanation: (?:400 - )?/, "") || response.statusText);
        }
        if (!action.startsWith("/make")) {
            const page = await fetch(location.pathname);
            const html = new DOMParser().parseFromString(await page.text(), "text/html");
            const updated = html.querySelector("#pipeline");
            if (!updated) throw new Error("Session expired. Sign in again.");
            main().replaceWith(updated);
            drawWaves();
            const selected = [...document.querySelectorAll('.reel-card[data-state="review"]')];
            if (activeReel) (selected.find(card => card.dataset.reel === activeReel) || selected[0])?.focus();
        }
        toast("Saved");
    } catch (error) {
        if (inline) inline.textContent = error.message;
        toast(error.message, true);
    } finally {
        pending--;
        button.textContent = label;
        button.disabled = false;
        form.removeAttribute("aria-busy");
        refreshStatus();
        progress();
    }
});
setInterval(refreshStatus, 4000);
setInterval(progress, 1500);
drawWaves();
progress();
