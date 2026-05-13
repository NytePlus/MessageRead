FROM python:3.12-alpine
WORKDIR /app
ENV PORT=4000
COPY backend/requirements.txt ./backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt
COPY backend ./backend
EXPOSE 4000
CMD ["python", "backend/main.py"]
