const menu = document.querySelector(".site-menu");
const summary = menu?.querySelector("summary");
const panel = menu?.querySelector(".site-nav");

if (menu && summary && panel && typeof panel.animate === "function") {
  const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");
  const duration = 470;
  const keyframes = [
    { clipPath: "inset(14px 0 100% 0)", transform: "translateY(-14px)", opacity: 0.7 },
    { clipPath: "inset(0)", transform: "translateY(0)", opacity: 1 },
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
