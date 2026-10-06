import themes from "../review_heatmap/color_themes.json";

export function themeAccentRgb(theme: string): string {
  const color = (themes[theme] || themes.lime)[4];
  return [1, 3, 5].map(offset => parseInt(color.slice(offset, offset + 2), 16)).join(", ");
}

/** Preserve the original heatmap and statistics colors in both themes. */
export function themeCss(): string {
  return Object.entries(themes).map(([name, colors]) =>
    [false, true].map(night => {
      const prefix = `${night ? ".night_mode " : ""}.rh-theme-${name}`;
      const palette = night ? [...colors].reverse() : colors;
      return palette.map((color, index) => {
        const level = index + 11;
        // A Baseline statistics row overrides the ancestor's saved theme.
        const statsOverride = name === "lime"
          ? `,${night ? ".night_mode " : ""}.rh-container .streak.rh-theme-lime .rh-col${level}`
          : "";
        return `${prefix} .cal-heatmap-container .q${level}{fill:${color}}\n` +
          `${prefix} .rh-col${level}${statsOverride}{color:${color}}`;
      }).join("\n");
    }).join("\n")
  ).join("\n");
}
