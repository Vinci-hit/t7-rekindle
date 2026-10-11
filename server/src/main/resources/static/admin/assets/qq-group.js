"use strict";

(() => {
  const root = document.getElementById("qqGroup");
  if (!root) return;
  const form = document.getElementById("groupSettings");
  const select = document.getElementById("groupNumber");
  const save = document.getElementById("groupSave");
  const start = document.getElementById("scanStart");
  const cancel = document.getElementById("scanCancel");
  const frame = document.getElementById("qrFrame");
  const status = document.getElementById("scanStatus");
  const identity = document.getElementById("groupIdentity");
  const scanField = document.getElementById("scanId");
  const refresh = document.getElementById("groupsRefresh");
  let generation = 0;
  let scanId = "";
  let timer;

  async function request(path) {
    const response = await fetch(`/admin/qq-group${path}`, {
      method: "POST", credentials: "same-origin", cache: "no-store",
      headers: { "X-CSRF-TOKEN": form.querySelector('[name="_csrf"]').value, "X-Requested-With": "XMLHttpRequest" }
    });
    if (!response.headers.get("Content-Type")?.includes("application/json")) {
      if (response.ok && response.status !== 200) return null;
      throw new Error("后台登录可能已过期，请刷新页面重试。");
    }
    const result = await response.json();
    if (!response.ok) throw new Error(result.message || "请求未完成，请重试。");
    return result;
  }
  function groups(items, selected) {
    select.replaceChildren(new Option("请选择目标群", ""));
    items.forEach(group => select.add(new Option(`${group.name}（${group.number}）`, group.number)));
    select.value = items.some(group => group.number === selected) ? selected : "";
    select.disabled = items.length === 0;
    save.disabled = !select.value;
  }
  select.addEventListener("change", () => { save.disabled = !select.value; });
  async function currentGroups() {
    const current = ++generation;
    clearTimeout(timer);
    frame.hidden = cancel.hidden = true;
    scanField.value = "";
    groups([], "");
    status.textContent = "正在读取当前账号的可管理群…";
    try {
      const result = await request("/groups");
      if (current !== generation) return;
      groups(result, root.dataset.group);
      identity.textContent = `当前群站账号：${root.dataset.qq}`;
      status.textContent = result.length ? "请选择目标群并保存。" : "当前 QQ 没有可管理的群，请更换账号扫码。";
    } catch (error) { if (current === generation) status.textContent = error.message; }
  }
  async function poll(current) {
    if (current !== generation) return;
    try {
      const result = await request(`/qr/${scanId}/poll`);
      if (current !== generation) return;
      status.textContent = result.message;
      if (result.state === "READY") {
        frame.hidden = true;
        scanField.value = result.id;
        identity.textContent = `${result.nickname}（${result.qqNumber}）· 10 分钟内选群保存`;
        groups(result.groups, root.dataset.group);
      } else if (result.state === "WAITING" || result.state === "SCANNED") {
        timer = setTimeout(() => poll(current), 3000);
      } else { frame.hidden = true; }
    } catch (error) { if (current === generation) { status.textContent = error.message; frame.hidden = true; } }
  }
  start.addEventListener("click", async () => {
    const current = ++generation;
    clearTimeout(timer);
    start.disabled = true;
    frame.hidden = cancel.hidden = true;
    scanField.value = "";
    groups([], "");
    status.textContent = "正在生成二维码…";
    try {
      const result = await request("/qr");
      if (current !== generation) return;
      scanId = result.id;
      document.getElementById("qrImage").src = `/admin/qq-group/qr/${scanId}/image`;
      frame.hidden = cancel.hidden = false;
      status.textContent = result.message;
      timer = setTimeout(() => poll(current), 3000);
    } catch (error) { if (current === generation) status.textContent = error.message; }
    finally { start.disabled = false; }
  });
  cancel.addEventListener("click", async () => {
    ++generation;
    clearTimeout(timer);
    frame.hidden = cancel.hidden = true;
    scanField.value = "";
    groups([], "");
    try { await request(`/qr/${scanId}/cancel`); status.textContent = "扫码已取消，原配置保持不变。"; }
    catch (error) { status.textContent = error.message; }
    if (root.dataset.qq) await currentGroups();
  });
  refresh?.addEventListener("click", currentGroups);
  window.addEventListener("pagehide", () => { ++generation; clearTimeout(timer); frame.hidden = true; });
  if (root.dataset.qq) currentGroups();
})();
