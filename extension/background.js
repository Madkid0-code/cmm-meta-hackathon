// CMM Scout — background worker.
// Holds the pairing code (chrome.storage) and performs the one network
// call: POST {portal, code, display_name} to the CMM site the user
// paired with. host_permissions cover the portal sites and the CMM
// host, so this fetch is not CORS-blocked. The code identifies whose
// browser this is; it is never a portal credential.
"use strict";

var DEFAULT_CMM = "https://madkidcopilot.duckdns.org/attested";

function getSettings() {
  return new Promise(function (resolve) {
    chrome.storage.local.get(["code", "cmmUrl"], function (items) {
      resolve({
        code: (items.code || "").trim(),
        cmmUrl: (items.cmmUrl || DEFAULT_CMM).replace(/\/+$/, "")
      });
    });
  });
}

function setStatus(text, isError) {
  chrome.storage.local.set({
    lastStatus: text, lastStatusAt: Date.now(), lastIsError: !!isError
  });
  chrome.action.setBadgeText({ text: isError ? "!" : "" });
  if (!isError) chrome.action.setBadgeBackgroundColor({ color: "#0E7A4F" });
}

async function confirm(portal, displayName) {
  var s = await getSettings();
  if (!s.code) {
    setStatus("Not paired yet — click the Scout icon and paste your pairing code from CMM.", true);
    return { ok: false, error: "not paired" };
  }
  var url = s.cmmUrl + "/api/connect-confirm";
  var resp;
  try {
    resp = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "include",
      body: JSON.stringify({
        portal: portal, code: s.code, display_name: displayName
      })
    });
  } catch (e) {
    setStatus("Could not reach CMM at " + s.cmmUrl + " — is the CMM tab open and signed in?", true);
    return { ok: false, error: "network" };
  }
  var data = {};
  try { data = await resp.json(); } catch (e) { /* non-JSON reply */ }
  if (resp.ok && data.ok) {
    var pretty = portal === "dice" ? "Dice" : "LinkedIn";
    setStatus(pretty + " confirmed to CMM as " + displayName + " ✓", false);
    return { ok: true };
  }
  setStatus("CMM did not accept the confirmation (" +
    (data.error || ("HTTP " + resp.status)) + ").", true);
  return { ok: false, error: data.error || resp.status };
}

chrome.runtime.onMessage.addListener(function (msg, _sender, reply) {
  if (!msg) return;
  if (msg.type === "cmm-confirm") {
    confirm(msg.portal, msg.display_name).then(reply);
    return true;
  }
  if (msg.type === "cmm-scan-active") {
    // Popup's "Confirm now": ask the active tab's content script.
    chrome.tabs.query({ active: true, currentWindow: true }, function (tabs) {
      var tab = tabs && tabs[0];
      if (!tab || !tab.id) { reply({ found: false }); return; }
      chrome.tabs.sendMessage(tab.id, { type: "cmm-scan" },
        function (resp2) {
          if (chrome.runtime.lastError || !resp2) {
            setStatus("Open a Dice or LinkedIn tab first, then Confirm now.", true);
            reply({ found: false });
            return;
          }
          reply(resp2);
        });
    });
    return true;
  }
});
