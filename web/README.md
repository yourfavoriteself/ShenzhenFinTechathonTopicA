# Web UI

Chat with kernel-rendered confirmation cards, budget, permissions (with revoke), inbox and journal.
Static files served by `app.web` (`uvicorn --factory app.web:create_web_app`), no build step.

- `app.js` keeps the device key in WebCrypto: Ed25519, or ECDSA P-256 where Ed25519 is unavailable.
  The private key is non-extractable and stored in IndexedDB; the browser signs
  `guardian-mandate:<mandate hash>` from the card itself.
- Cards follow the risk level: medium is one press, high is press-and-hold, and an amount from an
  unverified source must be typed in.
- Chat items arrive over SSE (`/api/events`); all text is inserted as text, never as HTML.
- Subscription cancellation (C2) uses a bank protected form, not a chat message: sign-in values go
  straight to the kernel vault (one-time, or remembered for that domain and card), the SMS code is
  typed by the client and used once. The form shows the kernel's domain check: official domain,
  not a look-alike, certificate. Saved sign-ins are listed under Permissions and can be forgotten.
