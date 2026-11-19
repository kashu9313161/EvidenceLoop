// Applies the saved light/dark theme before the page draws, so it doesn't flash.
// A separate file (not inline) so a strict Content Security Policy (script-src 'self') allows it.
try {
  var t = localStorage.getItem('el-theme');
  if (t === 'light' || t === 'dark') document.documentElement.dataset.theme = t;
} catch {
  // private browsing: keep the default theme
}
