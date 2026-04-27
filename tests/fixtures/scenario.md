# Sample council scenario

We are deciding whether to adopt JWT authentication with refresh-token
rotation for our customer-facing API.

Constraints:
- The legacy session-cookie scheme is locked to a single domain.
- We have no central revocation store today.
- Mobile clients need offline tokens that survive a 24-hour disconnect.
- The auth middleware is owned by a small team; bus factor is 2.

Open questions for the council:
1. Which token rotation cadence balances replay risk and UX friction?
2. Where should refresh tokens live — an HttpOnly cookie or a secure
   keychain on each platform?
3. Do we need a revocation list, or can short access-token lifetimes
   carry the security argument on their own?
