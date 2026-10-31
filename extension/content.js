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

  // -- LinkedIn ------------------------------------------------------
  function detectLinkedIn() {
    // The feed's left identity card links to your own /in/<slug> with
    // your name as the link text (rendered more than once: photo+name).
    var links = document.querySelectorAll('a[href*="/in/"]');
    for (var i = 0; i < links.length; i++) {
      var a = links[i];
      var r = a.getBoundingClientRect();
      if (r.top > 500) continue; // identity card sits near the top
      var t = textOf(a);
      if (looksLikeName(t)) return t;
      // Name may live in a child heading inside the link.
      var inner = a.querySelector("div, span, h3");
      if (inner && looksLikeName(textOf(inner))) return textOf(inner);
    }
    // Fallback: any top-of-page /in/ link with a name-like label.
    for (var j = 0; j < links.length; j++) {
      var t2 = textOf(links[j]);
      if (looksLikeName(t2)) return t2;
    }
    return null;
  }

  // -- Dice ----------------------------------------------------------
  function detectDice() {
    // Dice's signed-in header carries an account menu whose contents
    // include the full name; scan header/nav scopes first, preferring
    // elements near an account/profile control.
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
