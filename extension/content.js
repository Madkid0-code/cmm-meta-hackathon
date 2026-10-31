// CMM Scout — content script for dice.com and linkedin.com.
//
// Its whole job: notice that THIS browser is signed in to the portal,
// read the account name the portal itself displays, and tell the CMM
// background worker so it can confirm the connection. It never reads
// form fields, never touches passwords, and sends nothing but
// {portal, display_name} (the pairing code is added by the worker).
(function () {
  "use strict";

  var PORTAL = location.hostname.indexOf("dice.com") >= 0 ? "dice" : "linkedin";
  var sentFor = null; // name already reported this page load

  var STOP = new Set([
    "find jobs", "my jobs", "saved jobs", "job alerts", "career advice",
    "for employers", "post a job", "sign in", "log in", "log out",
    "sign out", "home", "jobs", "messaging", "notifications", "me",
    "my network", "profile", "settings", "search", "companies",
    "salaries", "resume", "applications", "applied jobs", "easy apply",
    "premium", "learning", "advertise", "work", "more", "view profile"
  ]);

  function looksLikeName(text) {
    if (!text) return false;
    var t = text.replace(/\s+/g, " ").trim();
    if (t.length < 5 || t.length > 60) return false;
    if (STOP.has(t.toLowerCase())) return false;
    // 2-5 word-ish tokens, letters / apostrophes / hyphens / dots only.
    if (!/^[A-Za-zÀ-ÖØ-öø-ÿ'’.\-]+( [A-Za-zÀ-ÖØ-öø-ÿ'’.\-]+){1,4}$/.test(t)) return false;
    return true;
  }

  function textOf(el) {
    return el ? (el.textContent || "").replace(/\s+/g, " ").trim() : "";
  }

  // Verified markers (inspected on the real signed-in pages,
  // 2026-10-31): on BOTH portals the signed-in profile photo carries
  // alt="<full account name>" exactly, and each portal's account
  // control is labelled with the full name. Those are tried first;
  // generic text scanning is only a last resort.

  // Strategy A (both portals): profile-photo alt text inside the
  // page's own chrome (header / nav / sidebar) — never feed content,
  // where other people's avatars also carry name alts.
  function detectByPhotoAlt() {
    var scopes = document.querySelectorAll("header, nav, aside");
    for (var s = 0; s < scopes.length; s++) {
      var imgs = scopes[s].querySelectorAll("img[alt]");
      for (var i = 0; i < imgs.length; i++) {
        var alt = (imgs[i].getAttribute("alt") || "").trim();
        if (looksLikeName(alt)) return alt;
      }
    }
    return null;
  }

  // -- LinkedIn ------------------------------------------------------
  function detectLinkedIn() {
    var byAlt = detectByPhotoAlt();
    if (byAlt) return byAlt;
    // The identity card's profile link is aria-labelled
    // "View <Name>’s profile" — strip the wrapper, keep the name.
    var links = document.querySelectorAll('a[href*="/in/"]');
    for (var i = 0; i < links.length; i++) {
      var label = links[i].getAttribute("aria-label") || "";
      var m = /^View (.+?)(?:’s|') profile$/.exec(label.trim());
      if (m && looksLikeName(m[1])) return m[1].trim();
    }
    // The top-nav "Me" button's accessible name is "<Name> Me".
    var btns = document.querySelectorAll("header button, nav button");
    for (var b = 0; b < btns.length; b++) {
      var bl = (btns[b].getAttribute("aria-label") || "").trim();
      if (/ Me$/.test(bl)) {
        var cand = bl.replace(/ Me$/, "");
        if (looksLikeName(cand)) return cand;
      }
    }
    // Fallback: identity-card link text near the top of the page.
    for (var j = 0; j < links.length; j++) {
      var a = links[j];
      if (a.getBoundingClientRect().top > 500) continue;
      var t = textOf(a);
      if (looksLikeName(t)) return t;
      var inner = a.querySelector("div, span, h3");
      if (inner && looksLikeName(textOf(inner))) return textOf(inner);
    }
    return null;
  }

  // -- Dice ----------------------------------------------------------
  function detectDice() {
    // The account control: <nav aria-label="Account"> holds a button
    // whose label is "<FULL NAME> <initials>" (e.g. "… MOHAMMED SM").
    var nav = document.querySelector('nav[aria-label="Account"]');
    if (nav) {
      var btn = nav.querySelector("button");
      if (btn) {
        var raw = (btn.getAttribute("aria-label") || textOf(btn))
          .replace(/\s+/g, " ").trim();
        // Drop trailing initial tokens (1-2 uppercase letters).
        var parts = raw.split(" ");
        while (parts.length > 2 && /^[A-Z]{1,2}$/.test(parts[parts.length - 1])) {
          parts.pop();
        }
        var nm = parts.join(" ");
        if (looksLikeName(nm)) return nm;
      }
    }
    var byAlt = detectByPhotoAlt();
    if (byAlt) return byAlt;
    // Fallback: leaf-node text scan inside header/nav scopes.
    var scopes = document.querySelectorAll(
      "header, nav, [data-testid*='header'], [class*='header']");
    for (var s = 0; s < scopes.length; s++) {
      var els = scopes[s].querySelectorAll(
        "button, a, span, div, p");
      for (var i = 0; i < els.length; i++) {
        var el = els[i];
        if (el.children.length > 2) continue; // leaf-ish nodes only
        var t = textOf(el);
        if (looksLikeName(t)) return t;
      }
    }
    return null;
  }

  function detect() {
    return PORTAL === "dice" ? detectDice() : detectLinkedIn();
  }

  function scan() {
    var name = null;
    try { name = detect(); } catch (e) { /* page still rendering */ }
    if (name && name !== sentFor) {
      sentFor = name;
      chrome.runtime.sendMessage({
        type: "cmm-confirm", portal: PORTAL, display_name: name
      });
    }
    return name;
  }

  // Portal pages are SPAs: scan on load, then keep an eye out for the
  // signed-in chrome appearing (e.g. right after the login redirect).
  var tries = 0;
  var timer = setInterval(function () {
    tries += 1;
    if (scan() || tries > 40) clearInterval(timer);
  }, 1500);
  scan();

  chrome.runtime.onMessage.addListener(function (msg, _sender, reply) {
    if (msg && msg.type === "cmm-scan") {
      sentFor = null; // popup asked explicitly: allow a re-report
      var name = scan();
      reply({ found: !!name, name: name || null });
    }
    return true;
  });
})();
