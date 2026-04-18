#!/usr/bin/env node
"use strict";

const fs = require("fs");
const path = require("path");
const vm = require("vm");

function assert(condition, message) {
  if (!condition) {
    throw new Error(message);
  }
}

function makeStorage(seed = {}) {
  const data = new Map(Object.entries(seed));
  return {
    getItem(key) {
      return data.has(key) ? String(data.get(key)) : null;
    },
    setItem(key, value) {
      data.set(key, String(value));
    },
    removeItem(key) {
      data.delete(key);
    },
    dump() {
      return Object.fromEntries(data.entries());
    },
  };
}

function loadAuthJwtUtils() {
  const filePath = path.resolve(__dirname, "../webapp/static/auth-jwt-utils.js");
  const source = fs.readFileSync(filePath, "utf8");
  const sandbox = {
    console,
    globalThis: {},
  };
  sandbox.globalThis.globalThis = sandbox.globalThis;
  vm.createContext(sandbox);
  vm.runInContext(source, sandbox, { filename: filePath });
  return sandbox.globalThis.TradingBotAuthJwt;
}

function run() {
  const authJwt = loadAuthJwtUtils();
  assert(authJwt, "TradingBotAuthJwt missing after load");

  const key = "tradingbot.jwt";
  const legacyKeys = ["supabasetoken", "supabaseToken", "supabase_token"];
  const validJwtA = "aaa.bbb.ccc";
  const validJwtB = "ddd.eee.fff";

  // 1) Read current key first; legacy values remain untouched.
  const s1 = makeStorage({ [key]: validJwtA, supabasetoken: validJwtB });
  const out1 = authJwt.readStoredApiJwt({
    storage: s1,
    authTokenKey: key,
    legacyAuthTokenKeys: legacyKeys,
    normalizeUserJwt: authJwt.normalizeUserJwt,
    isProbablyAccessJwt: authJwt.isProbablyAccessJwt,
    jwtBadShapeHint: authJwt.JWT_BAD_SHAPE_HINT,
  });
  assert(out1 === validJwtA, "expected current key token");
  assert(s1.getItem("supabasetoken") === validJwtB, "legacy token should be untouched when current key exists");

  // 2) Migrate once from first legacy hit and clear all legacy keys.
  const s2 = makeStorage({ supabasetoken: validJwtB, supabaseToken: validJwtA, supabase_token: validJwtA });
  const out2 = authJwt.readStoredApiJwt({
    storage: s2,
    authTokenKey: key,
    legacyAuthTokenKeys: legacyKeys,
    normalizeUserJwt: authJwt.normalizeUserJwt,
    isProbablyAccessJwt: authJwt.isProbablyAccessJwt,
    jwtBadShapeHint: authJwt.JWT_BAD_SHAPE_HINT,
  });
  assert(out2 === validJwtB, "expected migration from first populated legacy key");
  assert(s2.getItem(key) === validJwtB, "expected migrated token under current key");
  assert(s2.getItem("supabasetoken") === null, "legacy key 1 should be cleared");
  assert(s2.getItem("supabaseToken") === null, "legacy key 2 should be cleared");
  assert(s2.getItem("supabase_token") === null, "legacy key 3 should be cleared");

  // 3) Invalid current token clears current + legacy keys.
  const s3 = makeStorage({ [key]: "not-a-jwt", supabasetoken: validJwtA });
  const out3 = authJwt.readStoredApiJwt({
    storage: s3,
    authTokenKey: key,
    legacyAuthTokenKeys: legacyKeys,
    normalizeUserJwt: authJwt.normalizeUserJwt,
    isProbablyAccessJwt: authJwt.isProbablyAccessJwt,
    jwtBadShapeHint: authJwt.JWT_BAD_SHAPE_HINT,
  });
  assert(out3 === "", "invalid current token should be rejected");
  assert(s3.getItem(key) === null, "invalid current token should be removed");
  assert(s3.getItem("supabasetoken") === null, "legacy keys should be cleared on invalid current token");

  console.log("auth-jwt-utils storage migration checks passed");
}

run();
