# StorePilot AI public demo: the dashboard with the built-in simulated robot (no Jetson, nothing moves).
# Build from the repo root:
#   docker build -f storepilot/Dockerfile -t storepilot-demo .
#   docker run -p 8080:8080 -e NEBIUS_API_KEY=... -e STOREPILOT_UPLOAD_PASSWORD=... storepilot-demo
# Without NEBIUS_API_KEY it uses the offline parser and autopilot. Data (missions, videos) lives in the
# container: mount a volume on /app/storepilot/data to keep it across restarts.

FROM node:20-slim AS web
WORKDIR /web
COPY storepilot/frontend/package.json storepilot/frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY storepilot/frontend/ ./
RUN npm run build

FROM python:3.11-slim
WORKDIR /app
COPY agent/ agent/
COPY storepilot/backend/ storepilot/backend/
RUN pip install --no-cache-dir -r storepilot/backend/requirements.txt -e agent
COPY --from=web /web/dist storepilot/frontend/dist
# The products GR00T picks (policy/instructions.json is not in Git; the demo uses this copy)
COPY storepilot/deploy/demo_instructions.json policy/instructions.json
# Missions, stock and videos are written here. Hosts like Hugging Face run the container as user 1000.
RUN mkdir -p /app/storepilot/data /app/data && chmod -R a+rwX /app/storepilot/data /app/data
ENV STOREPILOT_MODE=mock STOREPILOT_HOST=0.0.0.0 PYTHONUNBUFFERED=1
EXPOSE 8080
WORKDIR /app/storepilot/backend
# Hosting platforms pass the port in $PORT
CMD ["sh", "-c", "STOREPILOT_PORT=${PORT:-8080} exec python -m storepilot"]
