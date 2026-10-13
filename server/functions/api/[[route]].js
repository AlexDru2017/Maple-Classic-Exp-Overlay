// Cloudflare Pages sends every /api/... request here (the website itself is public/).
import worker from "../../src/index.js";

export const onRequest = (context) => worker.fetch(context.request, context.env, context);
