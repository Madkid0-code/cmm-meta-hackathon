// CMM Scout popup: pairing form + last status + manual confirm.
"use strict";

var DEFAULT_CMM = "https://madkidcopilot.duckdns.org/attested";
var codeEl = document.getElementById("code");
var urlEl = document.getElementById("cmmUrl");
var statusEl = document.getElementById("status");

function showStatus() {
  chrome.storage.local.get(["lastStatus", "lastIsError"], function (items) {
    if (items.lastStatus) {
      statusEl.style.display = "block";
      statusEl.textContent = items.lastStatus;
      statusEl.style.borderColor = items.lastIsError ? "#E5B4B0" : "#E1EFE7";
    }
  });
}

chrome.storage.local.get(["code", "cmmUrl"], function (items) {
  codeEl.value = items.code || "";
  urlEl.value = items.cmmUrl || DEFAULT_CMM;
  showStatus();
});

document.getElementById("save").addEventListener("click", function () {
  chrome.storage.local.set({
    code: codeEl.value.trim().toUpperCase(),
    cmmUrl: urlEl.value.trim().replace(/\/+$/, "") || DEFAULT_CMM,
    lastStatus: "Paired ✓ — open Dice or LinkedIn while signed in, and Scout confirms automatically.",
    lastIsError: false
  }, showStatus);
});

document.getElementById("confirm").addEventListener("click", function () {
  chrome.storage.local.set({
    code: codeEl.value.trim().toUpperCase(),
    cmmUrl: urlEl.value.trim().replace(/\/+$/, "") || DEFAULT_CMM
  }, function () {
    chrome.runtime.sendMessage({ type: "cmm-scan-active" }, function (resp) {
      if (!resp || !resp.found) {
        chrome.storage.local.get(["lastStatus"], function (items) {
          if (!items.lastStatus) {
            statusEl.style.display = "block";
            statusEl.textContent = "No signed-in name found on this tab yet — sign in to the portal first.";
          }
          showStatus();
        });
      }
      setTimeout(showStatus, 1200);
    });
  });
});
