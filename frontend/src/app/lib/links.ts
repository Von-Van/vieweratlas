export const REPO_URL = "https://github.com/Von-Van/vieweratlas";
export const AUTHOR_URL = "https://github.com/Von-Van";
export const ISSUES_URL = `${REPO_URL}/issues`;
export const TWITCH_ATLAS_URL = "https://twitchatlas.com/";
export const TWITCHMAP_URL = "https://twitchmap.com/";

/** A file on the main branch, e.g. repoFile("docs/metrics.md"). */
export function repoFile(path: string): string {
  return `${REPO_URL}/blob/main/${path}`;
}
