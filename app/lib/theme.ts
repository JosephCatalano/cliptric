const storageKey = "cliptric-theme";

// Only a fixed storage key is interpolated. Apply the preference before first paint.
export const themeScript = `try {
  if (localStorage.getItem(${JSON.stringify(storageKey)}) === "light") {
    document.documentElement.dataset.theme = "light";
  }
} catch {}`;

export function toggleTheme() {
  const root = document.documentElement;
  const theme = root.dataset.theme === "dark" ? "light" : "dark";
  root.dataset.theme = theme;
  try {
    localStorage.setItem(storageKey, theme);
  } catch {
    // Storage can be disabled. The current page still uses the chosen theme.
  }
}
