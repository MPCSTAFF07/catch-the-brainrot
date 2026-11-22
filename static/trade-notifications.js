const mailLink = document.querySelector(".header-mail[data-count-url]");

if (mailLink) {
  const badge = mailLink.querySelector(".header-mail-badge");
  const toast = document.querySelector(".notification-toast");
  let latest = null;
  let hideToast;
  let checking = false;

  async function checkNotifications() {
    if (checking || document.hidden) return;
    checking = true;
    try {
      const response = await fetch(mailLink.dataset.countUrl, { headers: { Accept: "application/json" } });
      if (!response.ok || !response.headers.get("content-type")?.includes("application/json")) return;
      const { unread, latest: newest } = await response.json();
      badge.hidden = unread === 0;
      badge.textContent = unread > 99 ? "99+" : String(unread);
      mailLink.setAttribute("aria-label", unread ? "通知、未読 " + unread + " 件" : "通知");
      if (latest !== null && newest > latest && unread > 0 && toast) {
        toast.hidden = false;
        clearTimeout(hideToast);
        hideToast = setTimeout(() => { toast.hidden = true; }, 6500);
      }
      latest = newest;
    } catch {
      // 次の確認まで、直前の未読表示を残す。
    } finally {
      checking = false;
    }
  }

  checkNotifications();
  setInterval(checkNotifications, 15000);
  document.addEventListener("visibilitychange", checkNotifications);
}
