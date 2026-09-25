// Pure matcher behind retrofitRemoteMatches in new-client-repo.mjs, split out
// so it has a regression test (node --test) without turning the scaffolding
// script itself into a package. No dependencies, on purpose.

function escapeRegex(value) {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

export function matchesRemote(remote, org, repo) {
  return new RegExp(`[:/]${escapeRegex(org)}/${escapeRegex(repo)}(\\.git)?$`).test(remote);
}
