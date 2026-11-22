const storageKey = "cliptric-theme";

// Only a fixed storage key is interpolated. Apply the preference before first paint.
export const themeScript = `try {
  if (localStorage.getItem(${JSON.stringify(storageKey)}) === "light") {
    document.documentElement.classList.remove("dark");
  }
} catch {}`;

export function toggleTheme() {
  const dark = document.documentElement.classList.toggle("dark");
  try {
    localStorage.setItem(storageKey, dark ? "dark" : "light");
  } catch {
    // Storage can be disabled. The current page still uses the chosen theme.
  }
}
