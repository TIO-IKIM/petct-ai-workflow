FROM python:3.12-slim
RUN pip install --no-cache-dir SimpleITK==2.4.0 dateparser==1.2.0
WORKDIR /app
COPY resize_volume.py /app/
