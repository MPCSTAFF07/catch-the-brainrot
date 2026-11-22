const menu = document.querySelector(".site-menu");
const summary = menu?.querySelector("summary");
const panel = menu?.querySelector(".site-nav-sheet");

if (menu && summary && panel && typeof panel.animate === "function") {
  const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");
  const duration = 470;
  const keyframes = [
    { transform: "translateY(-100%)" },
    { transform: "translateY(0)" },
  ];

  let targetOpen = menu.open;
  let animation = null;
  menu.dataset.animated = "";

  function settle() {
    menu.open = targetOpen;
    animation?.cancel();
    animation = null;
  }

  summary.addEventListener("click", event => {
    if (reducedMotion.matches) return;

    event.preventDefault();
    targetOpen = animation ? !targetOpen : !menu.open;
    menu.open = true;

    if (animation) {
      animation.updatePlaybackRate(targetOpen ? 1 : -1);
      animation.play();
      return;
    }

    const active = panel.animate(keyframes, {
      duration,
      easing: "cubic-bezier(0.28, 0.72, 0.38, 1)",
      fill: "both",
    });
    animation = active;
    active.currentTime = targetOpen ? 0 : duration;
    active.playbackRate = targetOpen ? 1 : -1;
    active.play();
    active.onfinish = () => {
      if (animation === active) settle();
    };
  });

  reducedMotion.addEventListener?.("change", event => {
    if (event.matches && animation) settle();
  });
}
