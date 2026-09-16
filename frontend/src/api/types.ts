/**
 * The shapes the API returns, taken from the generated schema.
 *
 * `schema.d.ts` is generated from the backend's OpenAPI document by `pnpm types`
 * and is not edited by hand. Aliasing the pieces we use keeps the generated
 * paths out of the rest of the code, and means a renamed field on the server
 * becomes a TypeScript error here rather than an undefined at runtime.
 */
import type { components } from "./schema";

export type Identity = components["schemas"]["Identity"];
export type TicketSummary = components["schemas"]["TicketSummary"];
export type TicketDetail = components["schemas"]["TicketDetail"];
export type Message = components["schemas"]["Message"];
export type TokenResponse = components["schemas"]["TokenResponse"];
