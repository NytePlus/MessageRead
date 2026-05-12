FROM node:20-alpine AS frontend-build
WORKDIR /src
COPY package.json package-lock.json ./
COPY frontend/package.json ./frontend/package.json
RUN npm install
COPY frontend ./frontend
RUN npm run build -w frontend

FROM python:3.12-alpine
WORKDIR /app
ENV PORT=4000
ENV WEB_DIST=/app/frontend/dist
COPY backend/requirements.txt ./backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt
COPY backend ./backend
COPY --from=frontend-build /src/frontend/dist ./frontend/dist
EXPOSE 4000
CMD ["python", "backend/main.py"]
