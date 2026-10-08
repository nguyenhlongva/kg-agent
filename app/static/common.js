/* Tiện ích dùng chung cho trang người dùng và quản trị */
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
function toast(t){ const el = $("toast"); el.textContent = t; el.hidden = false; clearTimeout(toast.t); toast.t = setTimeout(() => el.hidden = true, 2800); }
function timeAgo(ts){
  if (!ts) return ""; const d = (Date.now() - ts) / 1000;
  if (d < 60) return "vừa xong"; if (d < 3600) return Math.floor(d / 60) + " phút";
  if (d < 86400) return Math.floor(d / 3600) + " giờ";
  return new Date(ts).toLocaleDateString("vi-VN");
}
async function api(path, opts = {}){
  const init = { method: opts.method || "GET", headers: {}, credentials: "same-origin" };
  if (opts.body instanceof FormData) init.body = opts.body;
  else if (opts.body !== undefined){ init.headers["Content-Type"] = "application/json"; init.body = JSON.stringify(opts.body); }
  const r = await fetch(path, init);
  let data = null; try{ data = await r.json(); }catch(e){}
  if (!r.ok){
    let msg = data && data.detail;
    if (Array.isArray(msg)) msg = "Thông tin chưa hợp lệ, kiểm tra lại các ô đã nhập.";
    const err = new Error(msg || ("Lỗi " + r.status)); err.status = r.status; throw err;
  }
  return data;
}
/* Hiển thị câu trả lời: markdown tối giản + chip trích dẫn */
function renderMd(text, msgKey, validIds){
  const lines = esc(text).split("\n"); let html = "", inList = false;
  for (const raw of lines){
    let l = raw.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
    l = l.replace(/\[([^\[\]\n]{2,200})\]/g, (m, inner) => {
      const ids = inner.split(/[,;\s]+/).filter(Boolean);
      const ok = ids.filter(id => validIds.has(id));
      if (!ok.length) return ids.some(id => /-v\d+-c\d{3}$/.test(id)) ? "" : m;
      return ok.map(id => `<button class="cite" data-msg="${msgKey}" data-id="${esc(id)}">${esc(id)}</button>`).join("");
    });
    const li = l.match(/^\s*(?:[-*•]|\d+[.)])\s+(.*)$/);
    if (li){ if (!inList){ html += "<ul>"; inList = true; } html += `<li>${li[1]}</li>`; continue; }
    if (inList){ html += "</ul>"; inList = false; }
    if (l.trim()) html += `<p>${l}</p>`;
  }
  if (inList) html += "</ul>";
  return html;
}
function sourcesHtml(sources, msgKey){
  if (!sources || !sources.length) return "";
  return `<details class="sources"><summary>Nguồn (${sources.length})</summary>` + sources.map(s =>
    `<div class="src" id="${msgKey}-${esc(s.id)}"><b>${esc(s.title)} · ${esc(s.id)}</b><div>${esc(s.text)}</div></div>`).join("") + "</details>";
}
function messageHtml(m, viewer, tools){
  const key = "m" + m.id;
  const mine = viewer === "user" ? m.role === "user" : m.role !== "user";
  if (m.role === "user"){
    return `<div class="msg ${mine ? "mine" : "enduser"}">${viewer === "admin" ? '<div class="who">Người dùng</div>' : ""}<div class="bubble">${esc(m.text)}</div>${tools || ""}</div>`;
  }
  const valid = new Set((m.sources || []).map(s => s.id));
  const who = m.role === "agent"
    ? `<span class="pill agent">Nhân viên hỗ trợ</span>${viewer === "admin" && m.admin_name ? `<span>${esc(m.admin_name)}</span>` : ""}${m.edited_at ? "<span>đã sửa</span>" : ""}`
    : `<span>Trợ lý AI</span>${m.edited_at ? "<span>đã sửa</span>" : ""}`;
  let extra = "";
  if (m.escalated) extra = '<p style="margin-top:6px"><span class="pill warn">Đã chuyển nhân viên</span></p>';
  else if (m.fallback) extra = '<p style="margin-top:6px"><span class="pill info">Trích nguyên văn tài liệu</span></p>';
  return `<div class="msg ${mine ? "mine " : ""}${m.role}"><div class="who">${who}</div><div class="bubble">${renderMd(m.text, key, valid)}${extra}${sourcesHtml(m.sources, key)}</div>${tools || ""}</div>`;
}
document.addEventListener("click", (e) => {
  const b = e.target.closest(".cite"); if (!b) return;
  const t = document.getElementById(b.dataset.msg + "-" + b.dataset.id); if (!t) return;
  t.closest("details").open = true;
  t.scrollIntoView({ behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth", block: "nearest" });
  t.classList.remove("flash"); void t.offsetWidth; t.classList.add("flash");
});
