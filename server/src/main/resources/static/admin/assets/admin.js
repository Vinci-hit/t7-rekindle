"use strict";

document.querySelectorAll("form").forEach((form) => {
  form.addEventListener("submit", (event) => {
    const confirmation = event.submitter?.dataset.confirm || form.dataset.confirm;
    if (confirmation && !window.confirm(confirmation)) {
      event.preventDefault();
      return;
    }
    form.querySelectorAll('button[type="submit"]').forEach((button) => {
      button.disabled = true;
    });
  });
});

window.addEventListener("pagehide", () => {
  document.querySelectorAll("[data-secret]").forEach((element) => {
    element.textContent = "密码已显示，请返回账户目录。";
  });
});

window.addEventListener("pageshow", () => {
  document.querySelectorAll('button[type="submit"]:not([data-managed-disabled])').forEach((button) => {
    button.disabled = false;
  });
});
