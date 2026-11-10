# Heirloom — single-container Node 20 app
FROM node:20-alpine

WORKDIR /app

COPY package.json ./
# Zero runtime dependencies — no `npm install` step needed.
COPY server.js ./
COPY lib ./lib
COPY public ./public

RUN mkdir -p /data

ENV PORT=3100
ENV DATA_DIR=/data

EXPOSE 3100

CMD ["node", "server.js"]
