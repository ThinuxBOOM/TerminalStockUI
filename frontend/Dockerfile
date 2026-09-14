# Frontend image (build context: ./frontend).
# Layer order is deps-before-source so code edits reuse the cached npm layer.
# VITE_API_BASE_URL is embedded at BUILD time: pass --build-arg to bake it in.
FROM node:20-alpine

WORKDIR /app

# Deps first (cached unless manifests change). Prefer reproducible `npm ci`
# when a lockfile is present; fall back to `npm install` (no lockfile yet).
COPY package.json package-lock.json* ./
RUN if [ -f package-lock.json ]; then npm ci --no-audit --no-fund; else npm install --no-audit --no-fund; fi

# Build-time API base (Vite embeds VITE_*; empty = same-origin /api on Vercel).
ARG VITE_API_BASE_URL=http://localhost:8000
ENV VITE_API_BASE_URL=$VITE_API_BASE_URL

# Source last, then build once at image-build time (not on every container start).
COPY . .
RUN npm run build

EXPOSE 5173

# Bounded liveness probe (matches docker-compose.yml): vite preview on :5173.
HEALTHCHECK --interval=30s --timeout=5s --retries=3 --start-period=60s \
  CMD wget -T 3 -qO- http://127.0.0.1:5173 >/dev/null 2>&1 || exit 1

CMD ["npm", "run", "preview", "--", "--host", "0.0.0.0", "--port", "5173"]
