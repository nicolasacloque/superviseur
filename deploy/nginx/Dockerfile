# Contexte de construction : la racine du dépôt (voir docker-compose.yml).
# Nginx sert le frontend compilé et relaie /api (dont le WebSocket) vers le service api.

FROM node:22-alpine AS frontend
WORKDIR /build
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM nginx:1.27-alpine
# Le modèle est rendu au démarrage par l'entrypoint de l'image (variables d'environnement seulement).
COPY deploy/nginx/supervisor.conf.template /etc/nginx/templates/default.conf.template
COPY deploy/nginx/security-headers.conf /etc/nginx/snippets/security-headers.conf
COPY --from=frontend /build/dist /usr/share/nginx/html
# Les pages de démonstration (données simulées) ne font pas partie de la production.
RUN rm -f /usr/share/nginx/html/demo.html
EXPOSE 80 443
