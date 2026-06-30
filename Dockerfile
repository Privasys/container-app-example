FROM python:3.12-slim
WORKDIR /app
COPY main.py .
# No EXPOSE: the app binds the platform-injected $PORT. Under host networking
# EXPOSE is a no-op anyway, and there is no fixed port to advertise.

CMD ["python", "main.py"]
