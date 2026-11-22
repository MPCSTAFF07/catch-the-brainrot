const tradeSwitch = document.querySelector(".trade-switch");

if (tradeSwitch) {
  const links = [...tradeSwitch.querySelectorAll("[data-trade-mode]")];
  const panes = [...document.querySelectorAll("[data-trade-pane]")];

  function select(mode) {
    for (const link of links) {
      if (link.dataset.tradeMode === mode) link.setAttribute("aria-current", "page");
      else link.removeAttribute("aria-current");
    }
    for (const pane of panes) pane.hidden = pane.dataset.tradePane !== mode;
  }

  tradeSwitch.addEventListener("click", event => {
    const link = event.target.closest("[data-trade-mode]");
    if (!link || event.defaultPrevented || event.button !== 0 ||
        event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
    event.preventDefault();
    if (link.hasAttribute("aria-current")) return;
    history.pushState(null, "", link.href);
    select(link.dataset.tradeMode);
  });

  window.addEventListener("popstate", () => {
    select(new URLSearchParams(location.search).get("mode") === "post" ? "post" : "search");
  });
}
