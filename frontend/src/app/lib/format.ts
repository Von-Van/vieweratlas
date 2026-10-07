// Viewer counts range from tens to hundreds of thousands; "12.5K" reads
// correctly at both ends where a fixed thousands suffix showed "0k".
export const compactNumber = new Intl.NumberFormat("en", {
  notation: "compact",
  maximumFractionDigits: 1,
});

export function initials(name: string) {
  return name.slice(0, 2).toUpperCase();
}
