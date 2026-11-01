FROM node:24-bookworm-slim
WORKDIR /app
COPY package.json package-lock.json ./
COPY agents/package.json agents/package.json
COPY web/package.json web/package.json
RUN npm ci --ignore-scripts
COPY agents agents
RUN npm run build:eve --workspace agents
WORKDIR /app/agents
ENV BOLTZFIX_ALLOW_LIVE=0 BOLTZFIX_SPONSOR_CONFIRMED=0 BOLTZFIX_DATA=/data
EXPOSE 8787
CMD ["node", "node_modules/eve/bin/eve.js", "start", "--host", "127.0.0.1", "--port", "8787", "--no-schedules"]
