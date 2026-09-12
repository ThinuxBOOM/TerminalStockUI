# Minimal frontend Dockerfile (placeholder-friendly).
# Frontend agent owns frontend/ source; this file is only created because it was missing.
FROM node:20-alpine

WORKDIR /app

# Deps (works once frontend agent lands package.json; tolerates placeholder state).
COPY package*.json ./
RUN if [ -f package.json ]; then npm install; else echo "no package.json yet — skipping npm install"; fi

COPY . .

# Build only if a build script exists; otherwise fall back to preview/dev server.
EXPOSE 5173

CMD ["sh", "-c", "if [ -f package.json ] && npm run | grep -q ' build'; then npm run build && npm run preview -- --host 0.0.0.0 --port 5173; elif [ -f package.json ]; then npm run dev -- --host 0.0.0.0 --port 5173; else echo 'frontend source not landed yet' && sleep infinity; fi"]
