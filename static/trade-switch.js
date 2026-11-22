const tradeSwitch = document.querySelector(".trade-switch");
const tradePanes = document.querySelector(".trade-panes");

if (tradeSwitch && tradePanes) {
  const links = [...tradeSwitch.querySelectorAll("[data-trade-mode]")];
  const panes = [...tradePanes.querySelectorAll("[data-trade-pane]")];
  const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)");
  const easing = "cubic-bezier(0.25, 0.7, 0.25, 1)";
  let currentPane = panes.find(pane => !pane.hidden);
  let running = null;

  function show(mode) {
    const incoming = panes.find(pane => pane.dataset.tradePane === mode);
    if (!incoming || incoming === currentPane) return;

    const oldHeight = tradePanes.getBoundingClientRect().height;
    running?.finish();
    const outgoing = currentPane;
    currentPane = incoming;
    for (const link of links) {
      if (link.dataset.tradeMode === mode) link.setAttribute("aria-current", "page");
      else link.removeAttribute("aria-current");
    }
    document.title = (mode === "post" ? "交換に出す" : "交換を探す") + " | Catch the Brainrot";

    if (reduceMotion.matches || typeof incoming.animate !== "function") {
      outgoing.hidden = true;
      incoming.hidden = false;
      return;
    }

    const direction = mode === "post" ? 1 : -1;
    outgoing.dataset.outgoing = "";
    outgoing.inert = true;
    incoming.hidden = false;
    const newHeight = tradePanes.getBoundingClientRect().height;
    tradePanes.dataset.switching = "";

    const animations = [
      tradePanes.animate([{height: oldHeight + "px"}, {height: newHeight + "px"}], {duration: 350, easing}),
      outgoing.animate(
        [{opacity: 1, transform: "translateX(0)"}, {opacity: 0, transform: "translateX(" + (-direction * 18) + "px)"}],
        {duration: 200, easing, fill: "forwards"}
      ),
      incoming.animate(
        [{opacity: 0, transform: "translateX(" + (direction * 20) + "px)"}, {opacity: 1, transform: "translateX(0)"}],
        {duration: 280, delay: 55, easing, fill: "backwards"}
      ),
    ];

    const transition = {
      finish() {
        if (running !== transition) return;
        running = null;
        outgoing.hidden = true;
        outgoing.inert = false;
        delete outgoing.dataset.outgoing;
        delete tradePanes.dataset.switching;
        for (const animation of animations) animation.cancel();
      },
    };
    running = transition;
    Promise.allSettled(animations.map(animation => animation.finished)).then(transition.finish);
  }

  tradeSwitch.addEventListener("click", event => {
    const link = event.target.closest("[data-trade-mode]");
    if (!link || event.defaultPrevented || event.button !== 0 ||
        event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
    event.preventDefault();
    if (link.hasAttribute("aria-current")) return;
    history.pushState(null, "", link.href);
    show(link.dataset.tradeMode);
  });

  window.addEventListener("popstate", () => {
    show(new URLSearchParams(location.search).get("mode") === "post" ? "post" : "search");
  });
}
