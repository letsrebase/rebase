import { test } from "node:test";
import assert from "node:assert/strict";
import { matchesRemote } from "./retrofit-remote-matches.mjs";

test("accepts the https remote a scaffold-and-clone repository actually has", () => {
  assert.equal(matchesRemote("https://github.com/letsrebase/client.one.git", "letsrebase", "client.one"), true);
});

test("accepts the ssh remote form", () => {
  assert.equal(matchesRemote("git@github.com:letsrebase/point.git", "letsrebase", "point"), true);
});

test("rejects a repo name a literal dot would otherwise let match as a wildcard", () => {
  // Greptile's finding on PR #392: an unescaped "." in "client.one" matched
  // any character, so "clientXone.git" passed the check meant to catch it.
  assert.equal(matchesRemote("https://github.com/letsrebase/clientXone.git", "letsrebase", "client.one"), false);
});

test("rejects a different org with the same repo name", () => {
  assert.equal(matchesRemote("https://github.com/someoneelse/point.git", "letsrebase", "point"), false);
});

test("rejects a repo name that is only a suffix of the remote's", () => {
  assert.equal(matchesRemote("https://github.com/letsrebase/notpoint.git", "letsrebase", "point"), false);
});
