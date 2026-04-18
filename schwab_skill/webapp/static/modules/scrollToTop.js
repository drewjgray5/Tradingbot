/**
 * Scroll-to-top floating button. Visible only after the user has scrolled
 * past ~400px; uses rAF to coalesce scroll events.
 */

export function setupScrollToTop() {
  const btn = document.getElementById("scrollTopBtn");
  if (!btn) return;
  let ticking = false;
  const toggle = () => {
    btn.classList.toggle("visible", window.scrollY > 400);
    ticking = false;
  };
  window.addEventListener("scroll", () => {
    if (!ticking) {
      requestAnimationFrame(toggle);
      ticking = true;
    }
  }, { passive: true });
  btn.addEventListener("click", () => {
    window.scrollTo({ top: 0, behavior: "smooth" });
  });
}
