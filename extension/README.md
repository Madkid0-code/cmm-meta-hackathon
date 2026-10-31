# CMM Scout — the browser helper that makes CMM independent

CMM Scout is a small Chrome extension that lives in the **job seeker's
own browser**. It exists so that connecting a job portal never needs
an agent, a server-side browser, or anyone's saved login:

1. In CMM (Your search → Your accounts) the seeker presses
   **Get my pairing code** and downloads this helper (`scout.zip` from
   the CMM site).
2. They load it in Chrome (chrome://extensions → Developer mode →
   Load unpacked), click the Scout icon, and paste the pairing code
   once. The code only says "this browser belongs to this candidate" —
   it is not a portal credential.
3. From then on, **Connect Dice / Connect LinkedIn** works like this:
   CMM opens the portal's own sign-in page in a new tab; the seeker
   signs in there themselves (or is already signed in); Scout notices
   the signed-in page, reads **the account name the portal itself
   displays**, and posts `{portal, code, display_name}` to CMM's
   `/api/connect-confirm`. CMM flips the card to
   "✓ Connected as \<that name\>".

What Scout never does: it never reads form fields, never sees or sends
a password, never reads or exports cookies. CMM stores only
portal / status / the displayed name — the same record the agent path
produced, now confirmed entirely inside the seeker's own browser.

Files: `manifest.json` (MV3), `content.js` (portal pages: detect the
displayed account name), `background.js` (holds the pairing code,
makes the one POST), `popup.html` / `popup.js` (pairing form, status,
"Confirm now").
