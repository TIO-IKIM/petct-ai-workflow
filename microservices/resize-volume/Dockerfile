# resize-volume v2: resamples a NIfTI (vol1, the CT) to the grid of another (vol2, the PET), in place.
# Build: docker build --platform linux/amd64 -t negarshahamiri/resize-volume:v2 microservices/resize-volume
FROM python:3.12-slim
RUN pip install --no-cache-dir SimpleITK==2.4.0 dateparser==1.2.0
WORKDIR /app
COPY resize_volume.py /app/
